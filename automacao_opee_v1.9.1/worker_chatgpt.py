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
    parada_solicitada,
    recuperar_pastas_com_erro,
    solicitar_parada,
)

#NOME_GPT = "AvaliaPEE_Checklist_5.6.2"
#CHATGPT_URL = getattr(config, "CHATGPT_URL", "https://chatgpt.com/")
BOTAO_STARTER_GPT = getattr(config, "BOTAO_STARTER_CHATGPT", "Avalie o projeto de EE em")
PROMPT_FALLBACK_GPT = "Avalie o projeto de EE em anexo seguindo rigorosamente o checklist."

# Tempo máximo de espera pela análise da IA (padrão: 10 minutos, ajustável no config.py)
TIMEOUT_RESPOSTA_IA_MS = int(getattr(config, "TIMEOUT_RESPOSTA_IA_MINUTOS", 10) * 60 * 1000)
MAX_TENTATIVAS_POR_PROJETO = max(1, int(getattr(config, "MAX_TENTATIVAS_POR_PROJETO", 1)))
PARAR_NO_PRIMEIRO_ERRO = bool(getattr(config, "PARAR_NO_PRIMEIRO_ERRO", True))

URL_DIRETA_GPT: str | None = None




NOME_GPT = "AvaliaPEE_Checklist_5.6.2"
CHATGPT_URL = getattr(config, "CHATGPT_URL", "https://chatgpt.com/")


async def entrar_no_gpt(page: Page, nome_gpt: str, tag: str):
    """Seleciona o plugin digitando @Avalia direto na caixa 'Pergunte ao ChatGPT'."""
    print(f"{tag} [1/4] Selecionando o plugin '{nome_gpt}' via @ no chat...")

    caixa = page.get_by_role(
        "textbox", name=re.compile(r"Pergunte ao ChatGPT|Trabalhe com o ChatGPT", re.IGNORECASE)
    ).first
    await caixa.wait_for(state="visible", timeout=20000)
    await caixa.click()
    await caixa.fill("@Avalia")

    botao_plugin = page.get_by_role("button", name=nome_gpt).first
    await botao_plugin.wait_for(state="visible", timeout=10000)
    await botao_plugin.click()
    await page.wait_for_timeout(800)


async def anexar_arquivos_chatgpt(page: Page, arquivos: list[str], tag: str):
    """Anexa os arquivos silenciosamente via set_input_files (sem abrir a janela do Windows)."""
    print(f"{tag} [2/4] Anexando {len(arquivos)} arquivo(s) silenciosamente...")

    input_anexar = page.get_by_label("Anexar arquivos").first

    # 1. Tenta injetar direto no <input type="file" aria-label="Anexar arquivos"> sem clicar em nada
    if await input_anexar.count() > 0:
        try:
            await input_anexar.set_input_files(arquivos)
            await page.wait_for_timeout(1500)
            return
        except Exception:
            pass

    # 2. Se o input só existir após abrir o menu '+', clica apenas em 'Adicionar arquivos e mais'
    # (NÃO clica em 'Adicionar fotos e arquivos' para não abrir a janela do Windows Explorer!)
    botao_mais = page.get_by_role("button", name="Adicionar arquivos e mais").first
    if await botao_mais.is_visible():
        await botao_mais.click()
        await page.wait_for_timeout(500)

    await input_anexar.set_input_files(arquivos)
    await page.keyboard.press("Escape")
    await page.wait_for_timeout(1500)


async def enviar_prompt_chatgpt(page: Page, tag: str):
    """Aguarda o upload terminar de carregar e clica diretamente no botão 'Enviar'."""
    print(f"{tag} [3/4] Aguardando conclusão do upload para clicar em 'Enviar'...")

    botao_enviar = page.get_by_role("button", name="Enviar").first
    botao_parar = page.locator(
        'button[data-testid="stop-button"], button[aria-label*="Parar"], button[aria-label*="Stop"]'
    ).first

    prazo = time.monotonic() + 90
    while time.monotonic() < prazo:
        await page.wait_for_timeout(1500)

        if await botao_parar.is_visible():
            print(f"{tag}  [OK] Arquivos enviados ao ChatGPT!")
            return

        if await botao_enviar.count() > 0 and await botao_enviar.is_visible() and await botao_enviar.is_enabled():
            try:
                await botao_enviar.click(timeout=3000)
                await page.wait_for_timeout(1500)
                if await botao_parar.is_visible():
                    print(f"{tag}  [OK] Arquivos enviados ao ChatGPT!")
                    return
            except Exception:
                pass

    raise TimeoutError(f"{tag} Botão 'Enviar' não habilitou após 90s.")

def localizar_botoes_baixar_gpt(page: Page):
    """Mesmo seletor para a contagem ANTES do envio e para a busca DEPOIS da resposta."""
    return page.get_by_role("button", name=re.compile(r"Baixar arquivo|Download file", re.IGNORECASE))


SEGUNDOS_ESTABILIDADE_RESPOSTA_GPT = 10

# Links de download escritos pelo próprio GPT na resposta.
# Gravação (Playwright codegen):  page.get_by_role("link", name="Baixar o JSON final")
# O texto é gerado pela IA e pode variar ("Baixar JSON", "Download do JSON final"...),
# por isso o padrão aceita qualquer link que cite JSON (ou DOCX/Word).
PADRAO_LINK_JSON_GPT = re.compile(r"(baixar|download).*\bjson\b|\.json\b", re.IGNORECASE)
PADRAO_LINK_DOCX_GPT = re.compile(r"(baixar|download).*\b(docx?|word)\b|\.docx?\b", re.IGNORECASE)


async def fechar_visualizador_gpt(page: Page, tag: str) -> bool:
    """Fecha o visualizador de arquivo do ChatGPT (gravação: botão 'Fechar visualizador')."""
    botao = page.get_by_role("button", name=re.compile(r"^(Fechar visualizador|Close viewer)$", re.IGNORECASE))
    fechou = False
    try:
        for i in range(await botao.count() - 1, -1, -1):
            if await botao.nth(i).is_visible():
                await botao.nth(i).click(timeout=5000)
                await page.wait_for_timeout(1000)
                print(f"{tag}  [OK] Visualizador de arquivo fechado.")
                fechou = True
                break
    except Exception as e:
        print(f"{tag}  [AVISO] Não consegui fechar o visualizador: {e}")
    if fechou:
        # A gravação clica na página ('html') para tirar o foco; Escape faz o mesmo
        # sem o risco de o clique no meio da tela acertar um link ou botão.
        try:
            await page.keyboard.press("Escape")
            await page.wait_for_timeout(500)
        except Exception:
            pass
    return fechou


def _tipos_salvos(salvos: list[Path]) -> set[str]:
    return {".docx" if p.suffix.lower() in (".docx", ".doc") else p.suffix.lower() for p in salvos}


def localizar_link_download_gpt(page: Page, ext: str):
    """Links 'Baixar o JSON final' / 'Baixar o DOCX ...' da resposta do ChatGPT."""
    padrao = PADRAO_LINK_JSON_GPT if ext == ".json" else PADRAO_LINK_DOCX_GPT
    return page.get_by_role("link", name=padrao)


def _nome_seguro_gpt(nome: str) -> str:
    return re.sub(r'[<>:"/\\|?*\x00-\x1f]', "_", nome or "").rstrip(" .")


async def _salvar_download_gpt(download, pasta: Path, nome_padrao: str, nomes_anexos: set[str]) -> Path | None:
    sugerido = download.suggested_filename or nome_padrao
    if sugerido.lower().endswith((".png", ".jpg", ".jpeg", ".webp")):
        return None
    nome = _nome_seguro_gpt(sugerido) or nome_padrao
    caminho = pasta / nome
    if nome.lower() in nomes_anexos:  # nunca sobrescreve um arquivo original do projeto
        caminho = pasta / f"{Path(nome).stem}_IA{Path(nome).suffix}"
    await download.save_as(str(caminho))
    if not caminho.is_file() or caminho.stat().st_size == 0:
        caminho.unlink(missing_ok=True)
        return None
    return caminho


async def aguardar_e_baixar_arquivos_chatgpt(page: Page, pasta_projeto: Path, qtd_botoes_antes: int, tag: str):
    """
    Aguarda o ChatGPT terminar a resposta (até 10 min) e baixa os arquivos gerados.
    Só dá sucesso se salvar pelo menos um .docx E um .json.
    """
    minutos = TIMEOUT_RESPOSTA_IA_MS / 60000
    print(f"{tag} [4/4] Aguardando o ChatGPT processar '{pasta_projeto.name}' (limite de {minutos:.0f} min)...")

    nomes_anexos = {
        p.name.lower() for p in pasta_projeto.iterdir() if p.is_file() and p.name != ARQUIVO_SENTINELA
    }
    botoes_baixar = localizar_botoes_baixar_gpt(page)
    links_arquivos = page.get_by_role("link").filter(
        has_text=re.compile(r"\.(json|docx|doc)\b", re.IGNORECASE)
    )
    link_json = localizar_link_download_gpt(page, ".json")
    link_docx = localizar_link_download_gpt(page, ".docx")
    botao_parar = page.locator(
        'button[data-testid="stop-button"], button[aria-label*="Parar"], button[aria-label*="Stop"]'
    )

    inicio = time.monotonic()
    prazo = inicio + (TIMEOUT_RESPOSTA_IA_MS / 1000.0)
    ultimo_texto = None
    estavel_desde = inicio
    ultimo_aviso = inicio
    tem_arquivo = False

    # Espera: arquivo gerado + botão "Parar" sumiu + página parada por alguns segundos
    while time.monotonic() < prazo:
        await page.wait_for_timeout(2000)
        try:
            texto = await page.locator("main").first.inner_text(timeout=5000)
        except Exception:
            texto = ultimo_texto
        if texto != ultimo_texto:
            ultimo_texto = texto
            estavel_desde = time.monotonic()

        tem_arquivo = (
            (await botoes_baixar.count() > qtd_botoes_antes)
            or (await links_arquivos.count() > 0)
            or (await link_json.count() > 0)
            or (await link_docx.count() > 0)
        )
        gerando = await botao_parar.count() > 0 and await botao_parar.first.is_visible()
        if tem_arquivo and not gerando and time.monotonic() - estavel_desde >= SEGUNDOS_ESTABILIDADE_RESPOSTA_GPT:
            print(f"{tag}  -> Resposta concluída em {(time.monotonic() - inicio) / 60:.1f} min.")
            break

        if time.monotonic() - ultimo_aviso >= 60:
            ultimo_aviso = time.monotonic()
            print(f"{tag}  ... ainda processando ({(time.monotonic() - inicio) / 60:.0f}/{minutos:.0f} min)")

    if not tem_arquivo:
        raise TimeoutError(f"{tag} Tempo limite de {minutos:.0f} min atingido sem arquivos gerados no ChatGPT.")

    salvos: list[Path] = []

    # Fluxo gravado: primeiro fecha o visualizador que o ChatGPT abre com o arquivo gerado.
    await fechar_visualizador_gpt(page, tag)

    # 1. Botões 'Baixar arquivo' novos (gerados nesta resposta). Fluxo gravado:
    #    page.get_by_role("button", name="Baixar arquivo").click()  -> 1 download por botão
    total_botoes = await botoes_baixar.count()
    novos_indices = list(range(qtd_botoes_antes, total_botoes))
    if any(p.suffix.lower() == ".json" for p in salvos) and any(p.suffix.lower() in (".docx", ".doc") for p in salvos):
        novos_indices = []
    if novos_indices:
        print(f"{tag}  -> {len(novos_indices)} botão(ões) 'Baixar arquivo' detectado(s)! Baixando...")
    for pos, idx in enumerate(novos_indices, start=1):
        botao = botoes_baixar.nth(idx)
        try:
            await botao.scroll_into_view_if_needed(timeout=2000)
        except Exception:
            pass
        if {".docx", ".json"} <= _tipos_salvos(salvos):
            break  # já tem os dois arquivos
        try:
            download = None
            # a) Clique via JavaScript: o botão transparente "Abrir prévia de <arquivo>" fica por
            #    cima do "Baixar arquivo" e intercepta o clique normal (confirmado no teste real).
            try:
                async with page.expect_download(timeout=45000) as dl_info:
                    await botao.evaluate("el => el.click()")
                download = await dl_info.value
            except Exception as e_js:
                primeira_linha = str(e_js).splitlines()[0] if str(e_js) else type(e_js).__name__
                print(f"{tag}  [AVISO] Clique via JS no botão #{idx} não baixou ({primeira_linha}). "
                      "Tentando clique normal...")
                # b) Reserva: clique normal, como na gravação, com o visualizador fechado.
                await fechar_visualizador_gpt(page, tag)
                async with page.expect_download(timeout=20000) as dl_info:
                    await botao.click(timeout=5000)
                download = await dl_info.value
            tipos_antes = _tipos_salvos(salvos)
            caminho = await _salvar_download_gpt(download, pasta_projeto,
                                                 f"resultado_gpt_{pos}.dat", nomes_anexos)
            if caminho and _tipos_salvos([caminho]) & tipos_antes:
                print(f"{tag}  [IGNORADO] '{caminho.name}' repete um tipo já salvo; descartado.")
                caminho.unlink(missing_ok=True)
            elif caminho:
                salvos.append(caminho)
                print(f"{tag}  [OK] Salvo: {caminho.name}")
            else:
                print(f"{tag}  [IGNORADO] Botão #{idx} não era .docx/.json (imagem ou vazio).")
        except Exception as e:
            print(f"{tag} [ERRO] Falha ao baixar no botão #{idx}: {e}")

    # 2. Links "Baixar o JSON final" / "Baixar o DOCX ..." (gravação codegen), só para o que faltar.
    #    Usa o ÚLTIMO link de cada tipo, que é o da resposta mais recente.
    for ext, link in ((".json", link_json), (".docx", link_docx)):
        if await link.count() == 0 or ext in _tipos_salvos(salvos):
            continue
        alvo = link.last
        nome_link = ext
        try:
            nome_link = (await alvo.inner_text(timeout=3000)).strip() or ext
            await alvo.scroll_into_view_if_needed(timeout=2000)
            async with page.expect_download(timeout=15000) as dl_info:
                await alvo.click(timeout=5000)
            caminho = await _salvar_download_gpt(await dl_info.value, pasta_projeto,
                                                 f"resultado_gpt{ext}", nomes_anexos)
            if caminho and caminho.suffix.lower() in ((".docx", ".doc") if ext == ".docx" else (ext,)):
                salvos.append(caminho)
                print(f"{tag}  [OK] Salvo: {caminho.name} (link '{nome_link}')")
            elif caminho:
                salvos.append(caminho)
                print(f"{tag}  [AVISO] Link '{nome_link}' baixou '{caminho.name}' (extensão inesperada).")
        except Exception as e:
            primeira_linha = str(e).splitlines()[0] if str(e) else type(e).__name__
            print(f"{tag} [AVISO] Link '{nome_link}' ({ext}) não baixou ({primeira_linha}). Tentando os outros métodos...")
            await fechar_visualizador_gpt(page, tag)  # o clique pode ter aberto a prévia

    # 3. Links diretos com nome de arquivo, para o que ainda faltar (.docx ou .json)
    def falta(ext: str) -> bool:
        aceitos = (".docx", ".doc") if ext == ".docx" else (ext,)
        return not any(p.suffix.lower() in aceitos for p in salvos)

    if falta(".docx") or falta(".json"):
        for idx in range(await links_arquivos.count()):
            link = links_arquivos.nth(idx)
            try:
                nome_link = (await link.inner_text()).strip()
                if not ((falta(".docx") and ".doc" in nome_link.lower()) or
                        (falta(".json") and ".json" in nome_link.lower())):
                    continue
                await link.scroll_into_view_if_needed(timeout=2000)
                async with page.expect_download(timeout=60000) as dl_info:
                    await link.click()
                caminho = await _salvar_download_gpt(await dl_info.value, pasta_projeto,
                                                     _nome_seguro_gpt(nome_link) or f"resultado_gpt_{idx + 1}.txt",
                                                     nomes_anexos)
                if caminho:
                    salvos.append(caminho)
                    print(f"{tag}  [OK] Salvo: {caminho.name}")
            except Exception as e:
                print(f"{tag} [ERRO] Falha ao baixar link #{idx}: {e}")

    faltando = [ext for ext in (".docx", ".json") if falta(ext)]
    if faltando:
        raise RuntimeError(
            f"{tag} Download INCOMPLETO no ChatGPT! Faltando: {', '.join(faltando)} | "
            f"Salvos: {[p.name for p in salvos]}"
        )
    print(f"{tag}  [OK] .docx e .json salvos em '{pasta_projeto.name}'.")


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

    qtd_botoes_antes = await localizar_botoes_baixar_gpt(page).count()

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

            qtd_botoes_antes = await localizar_botoes_baixar_gpt(page).count()

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
        if parada_solicitada():
            print(f"[ChatGPT #{worker_id}] Parada solicitada; não vou pegar novos projetos da fila.")
            break

        async with lock_fila:
            if parada_solicitada():
                break
            pasta_atual = obter_proximo_projeto_sincrono()
            if pasta_atual:
                print(f"\n[ChatGPT #{worker_id}] Pegou da fila: {pasta_atual.name}")

        if not pasta_atual:
            break

        sucesso_projeto = False
        ultimo_erro = None

        for tentativa in range(1, MAX_TENTATIVAS_POR_PROJETO + 1):
            if tentativa > 1 and parada_solicitada():
                # Outro agente já falhou: não abre nova aba para tentar de novo.
                break
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

            if PARAR_NO_PRIMEIRO_ERRO:
                solicitar_parada(f"ChatGPT #{worker_id} falhou em '{pasta_atual.name}': {ultimo_erro}")
                print(
                    f"[PARADA | ChatGPT #{worker_id}] Pipeline interrompido. A aba com o erro foi mantida aberta "
                    f"para conferência e o projeto está em {DIR_ERROS.name}."
                )
                break

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
