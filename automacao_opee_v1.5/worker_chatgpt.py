import argparse
import asyncio
import re
import shutil
import time
from pathlib import Path
from playwright.async_api import async_playwright, Page

import config
from config import (
    DIR_BAIXADOS,
    DIR_EM_ANALISE,
    DIR_CONCLUIDOS,
    DIR_ERROS,
    ARQUIVO_SENTINELA,
    inicializar_pastas,
)
from worker_copilot import (
    LOCK_FILA_GLOBAL,
    LOCK_UI_NAVEGACAO,
    inteiro_positivo,
    obter_proximo_projeto_sincrono,
    recuperar_pastas_com_erro,
)

NOME_GPT = getattr(config, "NOME_AGENTE_CHATGPT", "AvaliaPEE_CHECKLIST 5.4 -")
CHATGPT_URL = getattr(config, "CHATGPT_URL", "https://chatgpt.com/")
BOTAO_STARTER_GPT = getattr(config, "BOTAO_STARTER_CHATGPT", "Avalie o projeto de EE em")
PROMPT_FALLBACK_GPT = "Avalie o projeto de EE em anexo seguindo rigorosamente o checklist."

TIMEOUT_RESPOSTA_IA_MS = 300 * 1000
MAX_TENTATIVAS_POR_PROJETO = 3

URL_DIRETA_GPT: str | None = None


async def entrar_no_gpt(page: Page, nome_gpt: str, tag: str):
    """Abre o GPT personalizado ('AvaliaPEE_CHECKLIST 5.4 -') e clica em 'Continuar usando o GPT' se aparecer."""
    global URL_DIRETA_GPT
    print(f"{tag} [1/4] Entrando no GPT '{nome_gpt}'...")

    # Se já capturamos a URL direta do GPT (/g/g-...), navega direto para ganhar tempo e estabilidade
    if URL_DIRETA_GPT and "/g/" in URL_DIRETA_GPT:
        try:
            await page.goto(URL_DIRETA_GPT, wait_until="domcontentloaded")
            await page.wait_for_timeout(2000)
        except Exception:
            pass

    padrao_gpt = re.compile(re.escape(nome_gpt), re.IGNORECASE)
    botao_gpt = page.get_by_role("button", name=padrao_gpt).first
    item_gpt_generico = page.locator('a, button, [role="link"], [role="button"]').filter(
        has_text=re.compile(r"AvaliaPEE_CHECKLIST", re.IGNORECASE)
    ).first
    botao_sidebar = page.locator(
        'button[aria-label*="barra lateral"], button[aria-label*="sidebar"], button[data-testid*="sidebar"]'
    ).first

    prazo = time.monotonic() + 40
    entrou = "/g/" in page.url

    while time.monotonic() < prazo and not entrou:
        try:
            await page.bring_to_front()
        except Exception:
            pass

        if await botao_gpt.is_visible():
            await botao_gpt.click(timeout=5000)
            entrou = True
            break

        if await item_gpt_generico.is_visible():
            await item_gpt_generico.click(timeout=5000)
            entrou = True
            break

        if await botao_sidebar.is_visible():
            try:
                await botao_sidebar.click(timeout=2000)
                await page.wait_for_timeout(1000)
            except Exception:
                pass

        await page.wait_for_timeout(1000)

    if not entrou:
        await botao_gpt.click(timeout=10000)

    await page.wait_for_timeout(1500)

    # Verifica e clica no botão "Continuar usando o GPT" caso o modal/aviso apareça
    botao_continuar = page.get_by_role(
        "button", name=re.compile(r"Continuar usando o GPT|Continue using", re.IGNORECASE)
    ).first
    try:
        if await botao_continuar.is_visible(timeout=3500):
            await botao_continuar.click()
            await page.wait_for_timeout(1000)
    except Exception:
        pass

    if "/g/" in page.url:
        URL_DIRETA_GPT = page.url


async def anexar_arquivos_chatgpt(page: Page, arquivos: list[str], tag: str):
    """Anexa os arquivos do projeto no ChatGPT seguindo o fluxo de 'Anexar arquivos' sem travar no Explorer."""
    print(f"{tag} [2/4] Anexando {len(arquivos)} arquivo(s) no ChatGPT...")

    input_anexar = page.get_by_label("Anexar arquivos").first
    input_file_generico = page.locator('input[type="file"]').first

    # 1. Tenta injetar diretamente caso o input 'Anexar arquivos' já esteja no DOM
    if await input_anexar.count() > 0:
        try:
            await input_anexar.set_input_files(arquivos)
            await page.wait_for_timeout(1000)
            return
        except Exception:
            pass

    if await input_file_generico.count() > 0:
        try:
            await input_file_generico.set_input_files(arquivos)
            await page.wait_for_timeout(1000)
            return
        except Exception:
            pass

    # 2. Fluxo gravado: 'Adicionar arquivos e mais' -> 'Adicionar fotos e arquivos' -> 'Anexar arquivos'
    botao_mais = page.get_by_role(
        "button", name=re.compile(r"Adicionar arquivos e mais|Add files and more|Anexar", re.IGNORECASE)
    ).first
    if await botao_mais.is_visible():
        await botao_mais.click()
        await page.wait_for_timeout(600)

    if await input_anexar.count() > 0:
        try:
            await input_anexar.set_input_files(arquivos)
            await page.keyboard.press("Escape")
            return
        except Exception:
            pass

    botao_fotos_arquivos = page.get_by_role(
        "button", name=re.compile(r"Adicionar fotos e arquivos|Add photos and files", re.IGNORECASE)
    ).first

    async with page.expect_file_chooser(timeout=10000) as fc_info:
        if await botao_fotos_arquivos.is_visible():
            await botao_fotos_arquivos.click()
        else:
            await page.get_by_text(re.compile(r"Adicionar fotos e arquivos", re.IGNORECASE)).first.click()

    file_chooser = await fc_info.value
    await file_chooser.set_files(arquivos)
    await page.wait_for_timeout(1000)


async def enviar_prompt_chatgpt(page: Page, tag: str):
    """Clica no starter 'Avalie o projeto de EE em' e aguarda o botão 'Enviar' liberar após upload."""
    print(f"{tag} [3/4] Acionando starter '{BOTAO_STARTER_GPT}' e aguardando upload...")

    botao_starter = page.get_by_role(
        "button", name=re.compile(re.escape(BOTAO_STARTER_GPT), re.IGNORECASE)
    ).first

    clicou_starter = False
    if await botao_starter.is_visible():
        try:
            await botao_starter.click(timeout=5000)
            clicou_starter = True
            await page.wait_for_timeout(1000)
        except Exception:
            pass

    if not clicou_starter:
        caixa_prompt = page.locator("#prompt-textarea").first
        if await caixa_prompt.is_visible():
            await caixa_prompt.click()
            await caixa_prompt.fill(PROMPT_FALLBACK_GPT)

    botao_enviar = page.get_by_role("button", name=re.compile(r"^Enviar$|^Send$|Enviar prompt", re.IGNORECASE)).first
    botao_enviar_testid = page.locator('button[data-testid="send-button"]').first
    botao_parar = page.locator(
        'button[data-testid="stop-button"], button[aria-label*="Parar"], button[aria-label*="Stop"]'
    ).first

    prazo = time.monotonic() + 90
    while time.monotonic() < prazo:
        await page.wait_for_timeout(1500)

        # Se o botão de parar geração já apareceu, a mensagem já foi enviada com sucesso
        if await botao_parar.is_visible():
            print(f"{tag}  [OK] Mensagem e anexos enviados ao ChatGPT!")
            return

        # Se clicou no starter e ainda não preencheu/enviou porque o upload bloqueava, tenta clicar novamente
        if await botao_starter.is_visible():
            try:
                await botao_starter.click(timeout=2000)
            except Exception:
                pass

        for btn in (botao_enviar, botao_enviar_testid):
            if await btn.count() > 0 and await btn.is_visible() and await btn.is_enabled():
                try:
                    await btn.click(timeout=3000)
                    await page.wait_for_timeout(1500)
                    print(f"{tag}  [OK] Mensagem e anexos enviados ao ChatGPT!")
                    return
                except Exception:
                    pass

    raise TimeoutError(f"{tag} Não foi possível enviar o prompt no ChatGPT após 90s (upload travado ou botão Enviar desabilitado).")


async def aguardar_e_baixar_arquivos_chatgpt(page: Page, pasta_projeto: Path, qtd_botoes_antes: int, tag: str):
    """Aguarda o ChatGPT gerar a resposta, ignora botões de imagem e baixa os botões 'Baixar arquivo'."""
    print(f"{tag} [4/4] Aguardando o ChatGPT processar '{pasta_projeto.name}'...")

    botoes_baixar = page.get_by_role(
        "button", name=re.compile(r"Baixar arquivo|Download file", re.IGNORECASE)
    )
    links_arquivos = page.get_by_role("link").filter(
        has_text=re.compile(r"\.(txt|json|docx|doc|xlsx|csv|pdf|zip)\b", re.IGNORECASE)
    )

    prazo = time.monotonic() + (TIMEOUT_RESPOSTA_IA_MS / 1000.0)
    novo_arquivo_detectado = False

    while time.monotonic() < prazo:
        total_botoes = await botoes_baixar.count()
        total_links = await links_arquivos.count()
        if total_botoes > qtd_botoes_antes or total_links > 0:
            novo_arquivo_detectado = True
            break
        await page.wait_for_timeout(1500)

    if not novo_arquivo_detectado:
        raise TimeoutError(f"{tag} Tempo limite atingido sem botões 'Baixar arquivo' no ChatGPT.")

    # Aguarda o ChatGPT terminar toda a resposta (para garantir que TODOS os arquivos foram gerados)
    botao_parar = page.locator(
        'button[data-testid="stop-button"], button[aria-label*="Parar"], button[aria-label*="Stop"]'
    )
    fim_estabilizacao = time.monotonic() + 120
    while time.monotonic() < fim_estabilizacao:
        if await botao_parar.count() == 0 or not await botao_parar.first.is_visible():
            break
        await page.wait_for_timeout(1500)

    await page.wait_for_timeout(3500)

    arquivos_salvos = 0
    total_botoes = await botoes_baixar.count()

    if total_botoes > qtd_botoes_antes:
        novos_indices = list(range(qtd_botoes_antes, total_botoes))
        print(f"{tag}  -> {len(novos_indices)} botão(ões) 'Baixar arquivo' detectado(s)! Baixando...")

        for pos, idx in enumerate(novos_indices, start=1):
            botao = botoes_baixar.nth(idx)
            try:
                await botao.scroll_into_view_if_needed(timeout=2000)
            except Exception:
                pass

            try:
                # O botão "Baixar arquivo" real (ícone) muitas vezes fica fisicamente escondido 
                # debaixo do botão invisível "Abrir prévia". Forçamos o clique direto via evaluate.
                await botao.evaluate("el => el.click()")

                # Precisamos usar um expect_download avulso com wait_for_event porque
                # o clique assíncrono via evaluate escapa do 'async with' padrão
                download = await page.wait_for_event("download", timeout=60000)
                
                sugerido = download.suggested_filename
                if sugerido and sugerido.lower().endswith((".png", ".jpg", ".jpeg", ".webp")):
                    print(f"{tag}  [IGNORADO] Arquivo de imagem pulado: {sugerido}")
                    continue

                nome_seguro = re.sub(r'[<>:"/\\|?*\x00-\x1f]', "_", sugerido).rstrip(" .")
                caminho_final = pasta_projeto / (nome_seguro or f"resultado_gpt_{pos}.dat")
                await download.save_as(str(caminho_final))
                arquivos_salvos += 1
                print(f"{tag}  [OK] Salvo: {caminho_final.name}")
            except Exception as e:
                print(f"{tag} [ERRO] Falha ao baixar no botão #{idx}: {e}")

    # Fallback: caso o ChatGPT tenha renderizado links diretos em vez de botões 'Baixar arquivo'
    if arquivos_salvos == 0 and await links_arquivos.count() > 0:
        total_links = await links_arquivos.count()
        print(f"{tag}  -> {total_links} link(s) direto(s) detectado(s)! Baixando...")
        for idx in range(total_links):
            link = links_arquivos.nth(idx)
            try:
                await link.scroll_into_view_if_needed(timeout=2000)
                nome_link = (await link.inner_text()).strip()
                async with page.expect_download(timeout=60000) as download_info:
                    await link.click()
                download = await download_info.value
                sugerido = download.suggested_filename
                if "." not in sugerido and "." in nome_link:
                    sugerido = nome_link
                nome_seguro = re.sub(r'[<>:"/\\|?*\x00-\x1f]', "_", sugerido).rstrip(" .")
                caminho_final = pasta_projeto / (nome_seguro or f"resultado_gpt_{idx + 1}.txt")
                await download.save_as(str(caminho_final))
                arquivos_salvos += 1
                print(f"{tag}  [OK] Salvo: {caminho_final.name}")
            except Exception as e:
                print(f"{tag} [ERRO] Falha ao baixar link #{idx}: {e}")

    if arquivos_salvos == 0:
        raise RuntimeError(f"{tag} Nenhum arquivo pôde ser baixado do ChatGPT.")


async def processar_projeto_na_aba_chatgpt(page: Page, pasta_projeto: Path, worker_id: int, ja_entrou: bool = False):
    """Fluxo completo de 1 projeto dentro de uma aba exclusiva do ChatGPT."""
    tag = f"[ChatGPT #{worker_id} | {pasta_projeto.name}]"
    arquivos_para_enviar = [
        str(arq.resolve())
        for arq in sorted(pasta_projeto.iterdir())
        if arq.is_file() and arq.name != ARQUIVO_SENTINELA and not arq.name.endswith(".part")
    ]

    if not arquivos_para_enviar:
        raise FileNotFoundError(f"{tag} Nenhum arquivo encontrado na pasta.")

    if not ja_entrou:
        await entrar_no_gpt(page, NOME_GPT, tag)

    botoes_baixar = page.get_by_role(
        "button", name=re.compile(r"Baixar arquivo|Download file|Baixar", re.IGNORECASE)
    )
    qtd_botoes_antes = await botoes_baixar.count()

    await anexar_arquivos_chatgpt(page, arquivos_para_enviar, tag)
    await enviar_prompt_chatgpt(page, tag)
    await aguardar_e_baixar_arquivos_chatgpt(page, pasta_projeto, qtd_botoes_antes, tag)


async def preparar_e_enviar_projeto_chatgpt(
    context,
    pasta_projeto: Path,
    worker_id: int,
    tentativa: int,
) -> tuple[Page, int, str]:
    """
    Executa as etapas [1/4], [2/4] e [3/4] do ChatGPT sob LOCK_UI_NAVEGACAO
    para não roubar o foco das abas do Copilot enquanto elas anexam arquivos.
    """
    tag = f"[ChatGPT #{worker_id} | {pasta_projeto.name}]"
    arquivos_para_enviar = [
        str(arq.resolve())
        for arq in sorted(pasta_projeto.iterdir())
        if arq.is_file() and arq.name != ARQUIVO_SENTINELA and not arq.name.endswith(".part")
    ]

    if not arquivos_para_enviar:
        raise FileNotFoundError(f"{tag} Nenhum arquivo encontrado na pasta.")

    async with LOCK_UI_NAVEGACAO:
        suff = f" (tentativa {tentativa}/{MAX_TENTATIVAS_POR_PROJETO})" if tentativa > 1 else ""
        print(f"[ChatGPT #{worker_id}] Abrindo nova aba de auditoria para '{pasta_projeto.name}'{suff}...")
        page = await context.new_page()
        try:
            await page.bring_to_front()
            destino_inicial = URL_DIRETA_GPT if URL_DIRETA_GPT else CHATGPT_URL
            await page.goto(destino_inicial, wait_until="domcontentloaded")
            await asyncio.sleep(2.5)
            await entrar_no_gpt(page, NOME_GPT, tag)

            botoes_baixar = page.get_by_role(
                "button", name=re.compile(r"Baixar arquivo|Download file|Baixar", re.IGNORECASE)
            )
            qtd_botoes_antes = await botoes_baixar.count()

            await anexar_arquivos_chatgpt(page, arquivos_para_enviar, tag)
            await enviar_prompt_chatgpt(page, tag)
            return page, qtd_botoes_antes, tag
        except Exception:
            if tentativa < MAX_TENTATIVAS_POR_PROJETO:
                try:
                    await page.close()
                except Exception:
                    pass
            raise


async def loop_do_agente_chatgpt(
    worker_id: int,
    context,
    lock_fila: asyncio.Lock,
    resultados: dict,
    limite_por_worker_unico: bool = False,
):
    """Loop individual de cada agente ChatGPT: pega da fila, abre nova aba, processa e mantém aberta."""
    await asyncio.sleep((worker_id - 1) * 1.5)

    while True:
        async with lock_fila:
            pasta_atual = obter_proximo_projeto_sincrono()
            if pasta_atual:
                print(f"\n[ChatGPT #{worker_id}] Pegou da fila: {pasta_atual.name}")

        if not pasta_atual:
            break

        sucesso_projeto = False
        ultimo_erro = None

        for tentativa in range(1, MAX_TENTATIVAS_POR_PROJETO + 1):
            page = None
            try:
                page, qtd_botoes_antes, tag = await preparar_e_enviar_projeto_chatgpt(
                    context, pasta_atual, worker_id, tentativa
                )
                await aguardar_e_baixar_arquivos_chatgpt(page, pasta_atual, qtd_botoes_antes, tag)

                destino_final = DIR_CONCLUIDOS / pasta_atual.name
                if destino_final.exists():
                    shutil.rmtree(destino_final)
                shutil.move(str(pasta_atual), str(destino_final))
                resultados["sucessos"] += 1
                print(f"\n[SUCESSO | ChatGPT #{worker_id}] '{pasta_atual.name}' concluído -> {DIR_CONCLUIDOS.name}")
                sucesso_projeto = True
                break

            except Exception as erro:
                ultimo_erro = erro
                print(
                    f"\n[AVISO | ChatGPT #{worker_id}] Tentativa {tentativa}/{MAX_TENTATIVAS_POR_PROJETO} "
                    f"falhou em '{pasta_atual.name}': {erro}"
                )
                if tentativa < MAX_TENTATIVAS_POR_PROJETO:
                    if page is not None:
                        try:
                            await page.close()
                        except Exception:
                            pass
                    await asyncio.sleep(2.0)

        if not sucesso_projeto:
            resultados["falhas"] += 1
            print(f"\n[ERRO | ChatGPT #{worker_id}] Falha definitiva em '{pasta_atual.name}': {ultimo_erro}")
            destino_erro = DIR_ERROS / pasta_atual.name
            if destino_erro.exists():
                shutil.rmtree(destino_erro)
            shutil.move(str(pasta_atual), str(destino_erro))

        if limite_por_worker_unico:
            break


async def executar_pool_chatgpt_async(
    agentes: int = 1,
    recuperar: bool = False,
    apenas_um: bool = False,
    context_externo=None,
    lock_fila_externo: asyncio.Lock | None = None,
) -> dict:
    inicializar_pastas()

    if recuperar:
        recuperar_pastas_com_erro()

    if agentes <= 0:
        return {"sucessos": 0, "falhas": 0}

    pendentes = [
        p for p in DIR_BAIXADOS.iterdir()
        if p.is_dir() and (p / ARQUIVO_SENTINELA).is_file()
    ] if DIR_BAIXADOS.exists() else []

    if not pendentes:
        print(f"[INFO | CHATGPT] Nenhum projeto pronto em '{DIR_BAIXADOS.name}'.")
        return {"sucessos": 0, "falhas": 0}

    num_efetivo = 1 if apenas_um else min(agentes, len(pendentes))
    print(f"\n[POOL CHATGPT] {len(pendentes)} projeto(s) na fila | Iniciando {num_efetivo} agente(s) ChatGPT...")

    resultados = {"sucessos": 0, "falhas": 0}
    lock_fila = lock_fila_externo or LOCK_FILA_GLOBAL

    if context_externo is not None:
        tarefas = [
            loop_do_agente_chatgpt(
                worker_id=idx + 1,
                context=context_externo,
                lock_fila=lock_fila,
                resultados=resultados,
                limite_por_worker_unico=apenas_um,
            )
            for idx in range(num_efetivo)
        ]
        await asyncio.gather(*tarefas)
        return resultados

    async with async_playwright() as p:
        browser = await p.chromium.connect_over_cdp("http://localhost:9222")
        context = browser.contexts[0]

        tarefas = [
            loop_do_agente_chatgpt(
                worker_id=idx + 1,
                context=context,
                lock_fila=lock_fila,
                resultados=resultados,
                limite_por_worker_unico=apenas_um,
            )
            for idx in range(num_efetivo)
        ]
        await asyncio.gather(*tarefas)

    return resultados


def executar_pool_chatgpt(agentes: int = 1, recuperar: bool = False, apenas_um: bool = False) -> dict:
    """Wrapper síncrono para execução standalone ou via main.py."""
    return asyncio.run(
        executar_pool_chatgpt_async(agentes=agentes, recuperar=recuperar, apenas_um=apenas_um)
    )


if __name__ == "__main__":
    parser = argparse.ArgumentParser(
        description="Processa projetos da pasta 01_Baixados_OPEE no GPT personalizado do ChatGPT."
    )
    parser.add_argument(
        "--gpt", "-g",
        type=inteiro_positivo,
        default=1,
        help="Número de abas/agentes do ChatGPT rodando simultaneamente (padrão: 1).",
    )
    parser.add_argument(
        "--recuperar", "-r",
        action="store_true",
        help="Devolve pastas de 02_Em_Analise e 04_Erros para 01_Baixados_OPEE antes de iniciar.",
    )
    parser.add_argument(
        "--unico", "-u",
        action="store_true",
        help="Processa apenas 1 projeto e encerra.",
    )
    args = parser.parse_args()
    executar_pool_chatgpt(agentes=args.gpt, recuperar=args.recuperar, apenas_um=args.unico)
