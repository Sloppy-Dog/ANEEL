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

NOME_AGENTE = getattr(config, "NOME_AGENTE_COPILOT", "AvaliaPEE_CHECKLIST_5.4v270926")
COPILOT_URL = getattr(config, "COPILOT_URL", "https://copilot.cloud.microsoft/")

PROMPT_ANALISE = "Analise os arquivos anexados e execute suas instruções"
TIMEOUT_RESPOSTA_IA_MS = 300 * 1000
MAX_TENTATIVAS_POR_PROJETO = 3

# Quantidade mínima de arquivos gerados pela IA que OBRIGATORIAMENTE devem ser salvos
# (1 .docx + 1 .json = 2 arquivos). Se salvar menos que isso, lança erro e NÃO dá falso positivo!
QTD_MINIMA_ARQUIVOS_RETORNO = 2

# Locks globais compartilháveis entre workers (Copilot e ChatGPT)
LOCK_FILA_GLOBAL = asyncio.Lock()
LOCK_UI_NAVEGACAO = asyncio.Lock()
URL_DIRETA_AGENTE: str | None = None


def inteiro_positivo(valor) -> int:
    try:
        n = int(valor)
    except (ValueError, TypeError) as erro:
        raise argparse.ArgumentTypeError("Informe um número inteiro maior que zero.") from erro
    if n <= 0:
        raise argparse.ArgumentTypeError("Informe um número inteiro maior que zero.")
    return n


async def texto_visivel_limpo(locator) -> str:
    """Lê apenas o inner_text (compatível com <div> contenteditable) e remove caracteres invisíveis."""
    try:
        bruto = await locator.inner_text(timeout=3000)
    except Exception:
        return ""
    return re.sub(r"[\s\u200b\u200c\u200d\ufeff]+", "", bruto)


def recuperar_pastas_com_erro():
    """Devolve pastas de 02_Em_Analise e 04_Erros para 01_Baixados_OPEE."""
    recuperados = 0
    for pasta_origem in (DIR_EM_ANALISE, DIR_ERROS):
        if not pasta_origem.exists():
            continue
        for item in pasta_origem.iterdir():
            if item.is_dir():
                destino = DIR_BAIXADOS / item.name
                if not (item / ARQUIVO_SENTINELA).exists():
                    (item / ARQUIVO_SENTINELA).touch()
                if destino.exists():
                    shutil.rmtree(destino)
                shutil.move(str(item), str(destino))
                recuperados += 1
                print(f"[RECUPERADO] {item.name} voltou para {DIR_BAIXADOS.name}")
    if recuperados == 0:
        print("[INFO] Nenhuma pasta pendente em 02_Em_Analise ou 04_Erros.")


def obter_proximo_projeto_sincrono() -> Path | None:
    """Busca a próxima pasta pronta em 01_Baixados_OPEE e move para 02_Em_Analise."""
    if not DIR_BAIXADOS.exists():
        return None

    for pasta in sorted(DIR_BAIXADOS.iterdir()):
        try:
            if pasta.is_dir() and (pasta / ARQUIVO_SENTINELA).is_file():
                destino = DIR_EM_ANALISE / pasta.name
                if destino.exists():
                    shutil.rmtree(destino)
                shutil.move(str(pasta), str(destino))
                return destino
        except FileNotFoundError:
            continue
    return None


async def entrar_no_agente(page: Page, nome_agente: str, tag: str):
    """Garante que a aba do worker está dentro do Agente Declarativo e em um chat novo."""
    global URL_DIRETA_AGENTE
    print(f"{tag} [1/4] Entrando no agente '{nome_agente}'...")

    padrao_caixa_agente = re.compile(rf"Enviar mensagem para\s+{re.escape(nome_agente)}", re.IGNORECASE)
    caixa_agente = page.get_by_role("textbox", name=padrao_caixa_agente)

    if URL_DIRETA_AGENTE and URL_DIRETA_AGENTE != COPILOT_URL:
        try:
            await page.goto(URL_DIRETA_AGENTE, wait_until="domcontentloaded")
            await caixa_agente.first.wait_for(state="visible", timeout=15000)
            await page.wait_for_timeout(1000)
            return caixa_agente.first
        except Exception:
            pass

    padrao_nome = re.compile(re.escape(nome_agente), re.IGNORECASE)
    item_agente = page.locator(
        'a, button, [role="link"], [role="button"], [role="treeitem"], [role="menuitem"], [role="option"]'
    ).filter(has_text=padrao_nome).first

    botao_agentes = page.locator('button, [role="button"], [role="treeitem"]').filter(
        has_text=re.compile(r"^\s*(Agentes|Agents)\s*$", re.IGNORECASE)
    ).first

    botao_mostrar_mais = page.locator('button, [role="button"]').filter(
        has_text=re.compile(r"Mostrar mais|Ver mais|See more|Show more", re.IGNORECASE)
    ).first

    botao_menu = page.locator(
        'button[aria-label*="Navegação"], button[aria-label*="Navigation"], '
        'button[aria-label*="painel"], button[aria-label*="pane"], '
        'button[data-testid*="sidebar"], button[aria-label*="Menu"]'
    ).first

    prazo = time.monotonic() + 45
    clicou_agente = False

    while time.monotonic() < prazo:
        try:
            await page.bring_to_front()
        except Exception:
            pass

        if await caixa_agente.first.is_visible():
            clicou_agente = True
            break

        if await item_agente.is_visible():
            try:
                await item_agente.click(timeout=5000)
                clicou_agente = True
                break
            except Exception:
                pass

        if await botao_mostrar_mais.is_visible():
            try:
                await botao_mostrar_mais.click(timeout=2000)
                await page.wait_for_timeout(800)
            except Exception:
                pass

        if await botao_agentes.is_visible():
            try:
                await botao_agentes.click(timeout=2000)
                await page.wait_for_timeout(1000)
                if await item_agente.is_visible():
                    await item_agente.click(timeout=5000)
                    clicou_agente = True
                    break
            except Exception:
                pass

        if await botao_menu.is_visible():
            try:
                await botao_menu.click(timeout=2000)
                await page.wait_for_timeout(1000)
            except Exception:
                pass

        await page.wait_for_timeout(1000)

    if not clicou_agente:
        await item_agente.click(timeout=10000)

    await caixa_agente.first.wait_for(state="visible", timeout=30000)

    if "copilot" in page.url.lower() and page.url != COPILOT_URL:
        URL_DIRETA_AGENTE = page.url

    botao_novo_chat = page.locator(
        'button[aria-label*="Novo chat"], button[aria-label*="Nova conversa"], button[aria-label*="New chat"]'
    ).first
    if await botao_novo_chat.is_visible():
        try:
            await botao_novo_chat.click(timeout=2000)
        except Exception:
            pass

    await page.wait_for_timeout(1000)
    return caixa_agente.first


async def _anexar_um_arquivo_copilot(page: Page, caminho_arquivo: str, tag: str):
    input_oculto = page.locator("#upload-file-button")
    botao_plus = page.locator(
        '[data-testid="PlusMenuButton"], '
        'button[aria-label*="Adicionar"], '
        'button[aria-label*="Add"], '
        'button[aria-label*="Anexar"]'
    ).first
    opcao_carregar = page.get_by_text(
        re.compile(r"Carregar imagens e arquivos|Upload images and files|Carregar deste dispositivo", re.IGNORECASE)
    ).first

    if await input_oculto.count() > 0:
        try:
            await input_oculto.first.set_input_files([caminho_arquivo])
            await page.keyboard.press("Escape")
            return
        except Exception:
            pass

    ultimo_erro = None
    for tentativa_menu in range(1, 7):
        try:
            await page.bring_to_front()
        except Exception:
            pass

        try:
            if not await opcao_carregar.is_visible():
                if await botao_plus.is_visible():
                    await botao_plus.click(timeout=3000)
                    await page.wait_for_timeout(700)

            if await input_oculto.count() > 0:
                try:
                    await input_oculto.first.set_input_files([caminho_arquivo])
                    await page.keyboard.press("Escape")
                    return
                except Exception:
                    pass

            if await opcao_carregar.is_visible():
                async with page.expect_file_chooser(timeout=6000) as fc_info:
                    await opcao_carregar.click(timeout=3000)
                file_chooser = await fc_info.value
                await file_chooser.set_files([caminho_arquivo])
                return

        except Exception as e:
            ultimo_erro = e

        try:
            await page.keyboard.press("Escape")
        except Exception:
            pass
        await page.wait_for_timeout(600)

    raise RuntimeError(
        f"{tag} Não foi possível anexar '{Path(caminho_arquivo).name}' após 6 tentativas: {ultimo_erro}"
    )


async def anexar_arquivos_silenciosamente(page: Page, arquivos: list[str], tag: str):
    print(f"{tag} [2/4] Anexando {len(arquivos)} arquivo(s) em segundo plano...")

    for idx, arq in enumerate(arquivos, start=1):
        await _anexar_um_arquivo_copilot(page, arq, tag)
        if idx < len(arquivos):
            await page.wait_for_timeout(700)


async def enviar_prompt_quando_pronto(page: Page, caixa_texto, prompt: str, tag: str):
    print(f"{tag} [3/4] Enviando prompt e aguardando upload dos anexos...")
    await caixa_texto.click()
    await caixa_texto.fill(prompt)

    prazo = time.monotonic() + 60
    while time.monotonic() < prazo:
        await page.wait_for_timeout(1500)

        if not await texto_visivel_limpo(caixa_texto):
            print(f"{tag}  [OK] Mensagem e anexos enviados!")
            return

        botao_enviar = page.locator(
            'button[aria-label*="Enviar"]:not([disabled]), '
            'button[aria-label*="Send"]:not([disabled]), '
            'button[data-testid*="submit"]:not([disabled])'
        ).first

        if await botao_enviar.count() > 0 and await botao_enviar.is_visible() and await botao_enviar.is_enabled():
            await botao_enviar.click()
        else:
            await caixa_texto.press("Enter")

        await page.wait_for_timeout(1500)

        if not await texto_visivel_limpo(caixa_texto):
            print(f"{tag}  [OK] Mensagem e anexos enviados!")
            return

    raise TimeoutError(f"{tag} Não foi possível enviar a mensagem após 60s.")


async def fechar_painel_preview_copilot(page: Page, tag: str) -> bool:
    """
    Fecha o painel lateral de pré-visualização do Word (.docx) no Copilot.
    Usa os seletores exatos gravados no Playwright + busca coordenada no canto superior direito.
    """
    fechou = False

    # 1. Tenta os seletores exatos que você gravou no Playwright Codegen
    seletores_exatos = [
        page.get_by_role("button", name="Fechar visualização"),
        page.get_by_role("button", name="Fechar", exact=True),
        page.get_by_role("button", name="Close preview"),
        page.get_by_role("button", name="Close", exact=True),
    ]
    for loc in seletores_exatos:
        try:
            cnt = await loc.count()
            # Varre de trás para frente (o botão da direita no topo da prévia costuma ser o último no DOM)
            for i in range(cnt - 1, -1, -1):
                btn = loc.nth(i)
                if await btn.is_visible():
                    await btn.click(timeout=3000)
                    await page.wait_for_timeout(1200)
                    fechou = True
                    break
            if fechou:
                break
        except Exception:
            pass

    # 2. Se ainda houver o texto "You're viewing read only content" ou botão X no canto superior direito da tela,
    # clica via JavaScript no botão 'X' localizado na metade direita superior da janela (x > largura*0.65, y < 120)
    try:
        clicou_canto_direito = await page.evaluate("""() => {
            const w = window.innerWidth;
            const botoes = Array.from(document.querySelectorAll('button, [role="button"]'));
            // Procura botões visíveis no canto superior direito do painel de preview
            const candidatos = botoes.filter(b => {
                const r = b.getBoundingClientRect();
                if (r.width <= 0 || r.height <= 0) return false;
                const label = (b.getAttribute('aria-label') || b.getAttribute('title') || b.innerText || '').toLowerCase();
                const ehFechar = label.includes('fechar') || label.includes('close');
                const ehCantoSuperiorDireito = (r.left > w * 0.85 && r.top >= 0 && r.top < 90 && r.width < 60 && r.height < 60);
                return ehFechar || ehCantoSuperiorDireito;
            });
            // Ordena pelo mais à direita (o 'X' é sempre o último ícone à direita do cabeçalho da prévia)
            candidatos.sort((a, b) => b.getBoundingClientRect().right - a.getBoundingClientRect().right);
            if (candidatos.length > 0) {
                candidatos[0].click();
                return true;
            }
            return false;
        }""")
        if clicou_canto_direito:
            fechou = True
            await page.wait_for_timeout(1200)
    except Exception:
        pass

    return fechou


async def baixar_docx_via_card_copilot(page: Page, pasta_projeto: Path, pos: int, tag: str) -> bool:
    """
    Baixa o arquivo .docx que o Copilot renderiza como Card (e NÃO como link <a>):
    1. Fecha o painel lateral de pré-visualização (se estiver aberto), fazendo aparecer os 3 pontinhos ('Mais ações').
    2. Clica em 'Mais ações' no card do .docx.
    3. Clica em 'Baixar' no menu suspenso.
    4. Se mesmo assim não fechar sozinho, aguarda até 45s caso o usuário queira clicar no X manualmente (como funcionava antes!).
    """
    print(f"{tag}  -> Preparando download do documento Word (.docx)...")

    # Passo 1: Fecha a aba lateral de visualização para que o botão 'Mais ações' apareça no card do .docx
    await fechar_painel_preview_copilot(page, tag)

    # Passo 2: Procura o botão 'Mais ações' (que só aparece no card quando a visualização fecha)
    # Nota: Na barra superior do chat existe um '...' também, então pegamos o 'Mais ações' que pertence ao card do docx
    # ou aguardamos ele ficar visível (inclusive permitindo que você clique no X manualmente se quiser)
    prazo_espera_card = time.monotonic() + 45
    download_concluido = False

    while time.monotonic() < prazo_espera_card and not download_concluido:
        try:
            await page.bring_to_front()
        except Exception:
            pass

        # Tenta fechar novamente caso o painel ainda esteja aberto
        await fechar_painel_preview_copilot(page, tag)

        # Localiza todos os botões 'Mais ações' visíveis
        botoes_mais_acoes = page.get_by_role(
            "button", name=re.compile(r"^Mais ações$|^More actions$|Mais op[çc][õo]es", re.IGNORECASE)
        )
        total_mais = await botoes_mais_acoes.count()

        # Filtra pelo botão 'Mais ações' que está na área da resposta do chat (y > 100, ignorando o '...' do topo da página)
        indices_candidatos = []
        for i in range(total_mais):
            btn = botoes_mais_acoes.nth(i)
            if await btn.is_visible():
                box = await btn.bounding_box()
                if box and box["y"] > 95:
                    indices_candidatos.append(i)

        # Se não achou com y > 95, tenta qualquer 'Mais ações' visível (do último para o primeiro)
        if not indices_candidatos:
            for i in range(total_mais - 1, -1, -1):
                if await botoes_mais_acoes.nth(i).is_visible():
                    indices_candidatos.append(i)

        for idx_btn in indices_candidatos:
            btn_mais = botoes_mais_acoes.nth(idx_btn)
            try:
                await btn_mais.scroll_into_view_if_needed(timeout=2000)
                await btn_mais.click(timeout=3000)
                await page.wait_for_timeout(800)

                # Procura o item 'Baixar' que abriu no menu
                item_baixar = page.get_by_role(
                    "menuitem", name=re.compile(r"^Baixar$|^Download$", re.IGNORECASE)
                ).first
                if not await item_baixar.is_visible():
                    item_baixar = page.get_by_text(re.compile(r"^Baixar$|^Download$", re.IGNORECASE)).first

                if await item_baixar.is_visible():
                    async with page.expect_download(timeout=10000) as dl_info:
                        await item_baixar.click(timeout=4000)
                    download = await dl_info.value

                    sugerido = download.suggested_filename
                    nome_seguro = re.sub(r'[<>:"/\\|?*\x00-\x1f]', "_", sugerido).rstrip(" .")
                    if not nome_seguro.lower().endswith((".docx", ".doc")):
                        nome_seguro = f"{nome_seguro or f'relatorio_{pos}'}.docx"
                    caminho_final = pasta_projeto / nome_seguro
                    await download.save_as(str(caminho_final))
                    print(f"{tag}  [OK] Salvo (.docx): {caminho_final.name}")
                    return True
                else:
                    # Fechar menu errado se abriu o menu do topo
                    await page.keyboard.press("Escape")
            except Exception:
                try:
                    await page.keyboard.press("Escape")
                except Exception:
                    pass

        await page.wait_for_timeout(1500)

    return False


async def aguardar_e_baixar_arquivos(page: Page, pasta_projeto: Path, qtd_links_antes: int, tag: str):
    """
    Aguarda a IA gerar TANTO o Card do .docx QUANTO o link do .json (ou demais arquivos),
    garantindo que os 2 arquivos sejam efetivamente baixados antes de dar sucesso!
    """
    print(f"{tag} [4/4] Aguardando o Copilot processar '{pasta_projeto.name}'...")

    links_arquivos = page.get_by_role("link").filter(
        has_text=re.compile(r"\.(txt|json|docx|doc|xlsx|csv|pdf|zip)\b", re.IGNORECASE)
    )
    # O .docx no Copilot é um Card (button/div) e não um <a>!
    elementos_docx = page.locator("button, [role='button'], div, span").filter(
        has_text=re.compile(r"\.docx\b", re.IGNORECASE)
    )

    prazo = time.monotonic() + (TIMEOUT_RESPOSTA_IA_MS / 1000.0)
    resposta_iniciada = False

    while time.monotonic() < prazo:
        total_links = await links_arquivos.count()
        tem_docx = await elementos_docx.count() > 0
        if total_links > qtd_links_antes or tem_docx:
            resposta_iniciada = True
            break
        await page.wait_for_timeout(1500)

    if not resposta_iniciada:
        raise TimeoutError(f"{tag} Tempo limite atingido sem arquivos gerados pelo Copilot.")

    # Aguarda o botão "Parar" sumir (geração 100% concluída)
    botao_parar = page.locator(
        'button[aria-label*="Parar"], button[aria-label*="Stop"], button[data-testid*="stop"]'
    )
    fim_estabilizacao = time.monotonic() + 90
    while time.monotonic() < fim_estabilizacao:
        if await botao_parar.count() == 0 or not await botao_parar.first.is_visible():
            break
        await page.wait_for_timeout(1500)

    await page.wait_for_timeout(3500)

    async with LOCK_UI_NAVEGACAO:
        try:
            await page.bring_to_front()
        except Exception:
            pass

        arquivos_salvos = []

        # ----------------------------------------------------------------------
        # PARTE A: Se houver documento .docx na resposta (Card + Painel Lateral),
        # fecha a visualização e baixa o .docx pelo menu 'Mais ações' -> 'Baixar'
        # ----------------------------------------------------------------------
        tem_card_docx = await elementos_docx.count() > 0
        tem_link_docx = False
        total_links = await links_arquivos.count()
        for idx in range(qtd_links_antes, total_links):
            txt_l = (await links_arquivos.nth(idx).inner_text()).strip().lower()
            if ".docx" in txt_l:
                tem_link_docx = True
                break

        if tem_card_docx and not tem_link_docx:
            sucesso_docx = await baixar_docx_via_card_copilot(page, pasta_projeto, pos=1, tag=tag)
            if sucesso_docx:
                arquivos_salvos.append("docx")
            else:
                raise RuntimeError(
                    f"{tag} O arquivo .docx foi gerado no Copilot, mas NÃO pôde ser baixado "
                    "(painel lateral não fechou ou botão 'Mais ações' -> 'Baixar' não encontrado)."
                )

        # ----------------------------------------------------------------------
        # PARTE B: Baixa todos os links <a> explícitos (ex: arquivos_recebidos.json)
        # ----------------------------------------------------------------------
        total_links = await links_arquivos.count()
        novos_indices = list(range(qtd_links_antes, total_links))
        print(f"{tag}  -> {len(novos_indices)} link(s) direto(s) detectado(s) no chat! Baixando...")

        for pos, idx in enumerate(novos_indices, start=1):
            link = links_arquivos.nth(idx)

            try:
                await link.scroll_into_view_if_needed(timeout=2000)
            except Exception:
                pass

            if not await link.is_visible():
                continue

            nome_link = (await link.inner_text()).strip()

            try:
                await link.evaluate("(el, nome) => el.setAttribute('download', nome)", nome_link)
            except Exception:
                pass

            try:
                async with page.expect_download(timeout=30000) as download_info:
                    await link.click(timeout=5000)
                download = await download_info.value

                sugerido = download.suggested_filename
                if "." not in sugerido and "." in nome_link:
                    sugerido = nome_link

                nome_seguro = re.sub(r'[<>:"/\\|?*\x00-\x1f]', "_", sugerido).rstrip(" .")
                caminho_final = pasta_projeto / (nome_seguro or f"resultado_{pos}.txt")
                await download.save_as(str(caminho_final))
                arquivos_salvos.append(caminho_final.name)
                print(f"{tag}  [OK] Salvo: {caminho_final.name} ({nome_link})")

            except Exception as e_link:
                print(f"{tag} [AVISO] Clique direto em '{nome_link}' não baixou ({e_link}). Tentando via menu 'Mais ações'...")
                if ".docx" in nome_link.lower():
                    sucesso_docx = await baixar_docx_via_card_copilot(page, pasta_projeto, pos=pos, tag=tag)
                    if sucesso_docx:
                        arquivos_salvos.append(nome_link)
                    else:
                        raise RuntimeError(f"{tag} Falha ao baixar '{nome_link}' mesmo após tentar menu 'Mais ações'.")

        # ----------------------------------------------------------------------
        # VALIDAÇÃO RIGOROSA: Proíbe falso positivo se baixou apenas 1 arquivo!
        # ----------------------------------------------------------------------
        if len(arquivos_salvos) < QTD_MINIMA_ARQUIVOS_RETORNO:
            raise RuntimeError(
                f"{tag} Download INCOMPLETO! Foram salvos apenas {len(arquivos_salvos)} arquivo(s) "
                f"({arquivos_salvos}), mas são exigidos pelo menos {QTD_MINIMA_ARQUIVOS_RETORNO} (.docx e .json)."
            )


async def preparar_e_enviar_projeto_copilot(
    context,
    pasta_projeto: Path,
    worker_id: int,
    tentativa: int,
) -> tuple[Page, int, str]:
    tag = f"[Copilot #{worker_id} | {pasta_projeto.name}]"
    arquivos_para_enviar = [
        str(arq.resolve())
        for arq in sorted(pasta_projeto.iterdir())
        if arq.is_file() and arq.name != ARQUIVO_SENTINELA and not arq.name.endswith(".part")
    ]

    if not arquivos_para_enviar:
        raise FileNotFoundError(f"{tag} Nenhum arquivo encontrado na pasta.")

    async with LOCK_UI_NAVEGACAO:
        suff = f" (tentativa {tentativa}/{MAX_TENTATIVAS_POR_PROJETO})" if tentativa > 1 else ""
        print(f"[Copilot #{worker_id}] Abrindo nova aba de auditoria para '{pasta_projeto.name}'{suff}...")
        page = await context.new_page()
        try:
            await page.bring_to_front()
            destino_inicial = URL_DIRETA_AGENTE if URL_DIRETA_AGENTE else COPILOT_URL
            await page.goto(destino_inicial, wait_until="domcontentloaded")
            await asyncio.sleep(2.5)

            caixa_texto = await entrar_no_agente(page, NOME_AGENTE, tag)

            links_arquivos = page.get_by_role("link").filter(
                has_text=re.compile(r"\.(txt|json|docx|doc|xlsx|csv|pdf|zip)\b", re.IGNORECASE)
            )
            qtd_links_antes = await links_arquivos.count()

            await anexar_arquivos_silenciosamente(page, arquivos_para_enviar, tag)
            await enviar_prompt_quando_pronto(page, caixa_texto, PROMPT_ANALISE, tag)
            return page, qtd_links_antes, tag
        except Exception:
            if tentativa < MAX_TENTATIVAS_POR_PROJETO:
                try:
                    await page.close()
                except Exception:
                    pass
            raise


async def loop_do_agente(
    worker_id: int,
    context,
    lock_fila: asyncio.Lock,
    resultados: dict,
    limite_por_worker_unico: bool = False,
):
    await asyncio.sleep((worker_id - 1) * 1.5)

    while True:
        async with lock_fila:
            pasta_atual = obter_proximo_projeto_sincrono()
            if pasta_atual:
                print(f"\n[Copilot #{worker_id}] Pegou da fila: {pasta_atual.name}")

        if not pasta_atual:
            break

        sucesso_projeto = False
        ultimo_erro = None

        for tentativa in range(1, MAX_TENTATIVAS_POR_PROJETO + 1):
            page = None
            try:
                page, qtd_links_antes, tag = await preparar_e_enviar_projeto_copilot(
                    context, pasta_atual, worker_id, tentativa
                )
                await aguardar_e_baixar_arquivos(page, pasta_atual, qtd_links_antes, tag)

                destino_final = DIR_CONCLUIDOS / pasta_atual.name
                if destino_final.exists():
                    shutil.rmtree(destino_final)
                shutil.move(str(pasta_atual), str(destino_final))
                resultados["sucessos"] += 1
                print(f"\n[SUCESSO | Copilot #{worker_id}] '{pasta_atual.name}' concluído -> {DIR_CONCLUIDOS.name}")
                sucesso_projeto = True
                break

            except Exception as erro:
                ultimo_erro = erro
                print(
                    f"\n[AVISO | Copilot #{worker_id}] Tentativa {tentativa}/{MAX_TENTATIVAS_POR_PROJETO} "
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
            print(f"\n[ERRO | Copilot #{worker_id}] Falha definitiva em '{pasta_atual.name}': {ultimo_erro}")
            destino_erro = DIR_ERROS / pasta_atual.name
            if destino_erro.exists():
                shutil.rmtree(destino_erro)
            shutil.move(str(pasta_atual), str(destino_erro))

        if limite_por_worker_unico:
            break


async def executar_pool_copilot_async(
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
        print(f"[INFO | COPILOT] Nenhum projeto pronto em '{DIR_BAIXADOS.name}'.")
        return {"sucessos": 0, "falhas": 0}

    num_efetivo = 1 if apenas_um else min(agentes, len(pendentes))
    print(f"\n[POOL COPILOT] {len(pendentes)} projeto(s) na fila | Iniciando {num_efetivo} agente(s) Copilot...")

    resultados = {"sucessos": 0, "falhas": 0}
    lock_fila = lock_fila_externo or LOCK_FILA_GLOBAL

    if context_externo is not None:
        tarefas = [
            loop_do_agente(
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
            loop_do_agente(
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


def executar_pool_copilot(agentes: int = 1, recuperar: bool = False, apenas_um: bool = False) -> dict:
    return asyncio.run(
        executar_pool_copilot_async(agentes=agentes, recuperar=recuperar, apenas_um=apenas_um)
    )


def executar_worker(recuperar: bool = False) -> bool:
    res = executar_pool_copilot(agentes=1, recuperar=recuperar, apenas_um=True)
    return res["sucessos"] > 0


if __name__ == "__main__":
    parser = argparse.ArgumentParser(
        description="Processa projetos da pasta 01_Baixados_OPEE no Agente do Copilot (com suporte a múltiplas abas simultâneas)."
    )
    parser.add_argument(
        "--agentes", "-a",
        type=inteiro_positivo,
        default=1,
        help="Número de abas/agentes do Copilot rodando simultaneamente (padrão: 1).",
    )
    parser.add_argument(
        "--recuperar", "-r",
        action="store_true",
        help="Devolve pastas de 02_Em_Analise e 04_Erros para 01_Baixados_OPEE antes de iniciar.",
    )
    parser.add_argument(
        "--unico", "-u",
        action="store_true",
        help="Processa apenas 1 projeto e encerra (em vez de esvaziar toda a fila de 01_Baixados_OPEE).",
    )
    args = parser.parse_args()
    executar_pool_copilot(agentes=args.agentes, recuperar=args.recuperar, apenas_um=args.unico)
