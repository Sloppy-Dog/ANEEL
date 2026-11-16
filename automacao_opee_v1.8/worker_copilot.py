import argparse
import asyncio
import re
import shutil
import time
from pathlib import Path
from playwright.async_api import async_playwright, Page
import json

import config
from config import (
    DIR_BAIXADOS,
    DIR_EM_ANALISE,
    DIR_CONCLUIDOS,
    DIR_ERROS,
    ARQUIVO_SENTINELA,
    inicializar_pastas,
)

NOME_AGENTE = getattr(config, "NOME_AGENTE_COPILOT", "AvaliaPEE_CHECKLIST5.5-v021126")
COPILOT_URL = getattr(config, "COPILOT_URL", "https://copilot.cloud.microsoft/")

PROMPT_ANALISE = "Analise os arquivos anexados e execute suas instruções"
# Tempo máximo de espera pela análise da IA (padrão: 10 minutos, ajustável no config.py)
TIMEOUT_RESPOSTA_IA_MS = int(getattr(config, "TIMEOUT_RESPOSTA_IA_MINUTOS", 10) * 60 * 1000)
MAX_TENTATIVAS_POR_PROJETO = 3

# Quantidade mínima de arquivos gerados pela IA que OBRIGATORIAMENTE devem ser salvos
# (1 .docx + 1 .json = 2 arquivos). Se salvar menos que isso, lança erro e NÃO dá falso positivo!
QTD_MINIMA_ARQUIVOS_RETORNO = 2

class LockPorLoop:
    """Lock assíncrono reutilizável entre várias chamadas de asyncio.run().

    Um asyncio.Lock() criado no nível do módulo fica preso ao primeiro event loop
    em que houve disputa. Como o main.py chama asyncio.run() a cada ciclo
    (modo intercalado e modo contínuo), o segundo ciclo falhava com
    "RuntimeError: ... is bound to a different event loop". Esta classe cria
    um asyncio.Lock separado para cada event loop, mantendo o uso com
    'async with' inalterado.
    """

    def __init__(self):
        self._locks = {}

    def _lock_atual(self) -> asyncio.Lock:
        loop = asyncio.get_running_loop()
        lock = self._locks.get(loop)
        if lock is None:
            # Descarta locks de loops já encerrados para não acumular referências.
            self._locks = {lp: lk for lp, lk in self._locks.items() if not lp.is_closed()}
            lock = self._locks[loop] = asyncio.Lock()
        return lock

    async def __aenter__(self):
        await self._lock_atual().acquire()
        return self

    async def __aexit__(self, exc_type, exc, tb):
        self._lock_atual().release()
        return False


# Locks globais compartilháveis entre workers (Copilot e ChatGPT)
LOCK_FILA_GLOBAL = LockPorLoop()
LOCK_UI_NAVEGACAO = LockPorLoop()
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
                    async with page.expect_download(timeout=45000) as dl_info:
                        await item_baixar.click(timeout=4000)
                    download = await dl_info.value

                    sugerido = download.suggested_filename
                    nome_seguro = re.sub(r'[<>:"/\\|?*\x00-\x1f]', "_", sugerido).rstrip(" .")
                    if "." in nome_seguro and not nome_seguro.lower().endswith((".docx", ".doc")):
                        # Baixou outro tipo de arquivo (ex.: o .json): não renomeia para .docx
                        await download.cancel()
                        await page.keyboard.press("Escape")
                        continue
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


async def _garantir_download_json_copilot(page: Page, pasta_projeto: Path, tag: str) -> str | None:
    """
    Fallback exclusivo para capturar o arquivo .json no computador do chefe,
    sem tocar na lógica do .docx. Tenta:
    1. Fetch direto via JS caso exista um <a href="..."> apontando para .json
    2. Clique direto ou menu 'Mais ações' -> 'Baixar' em Card/Botão de .json
    3. Extração direta de bloco de código JSON renderizado na resposta
    """
    print(f"{tag}  -> [FALLBACK JSON] Buscando arquivo .json por métodos alternativos...")

    # Estratégia 1: Algum link <a> com texto .json cujo clique normal abriu aba em vez de baixar
    links_json = page.get_by_role("link").filter(has_text=re.compile(r"\.json\b", re.IGNORECASE))
    cnt_links = await links_json.count()
    for i in range(cnt_links - 1, -1, -1):
        lnk = links_json.nth(i)
        if not await lnk.is_visible():
            continue
        nome_arq = (await lnk.inner_text()).strip()
        if not nome_arq.lower().endswith(".json"):
            m = re.search(r"[\w\-]+\.json", nome_arq, re.IGNORECASE)
            nome_arq = m.group(0) if m else "arquivos_recebidos.json"

        # Tenta extrair o conteúdo via fetch() no próprio contexto da página (funciona para blob: e https:)
        try:
            conteudo_texto = await lnk.evaluate("""async (el) => {
                const url = el.href || el.getAttribute('href');
                if (!url || url.startsWith('javascript:')) return null;
                const resp = await fetch(url);
                if (!resp.ok) return null;
                return await resp.text();
            }""")
            if conteudo_texto and "{" in conteudo_texto:
                nome_seguro = re.sub(r'[<>:"/\\|?*\x00-\x1f]', "_", nome_arq).rstrip(" .")
                caminho_final = pasta_projeto / nome_seguro
                caminho_final.write_text(conteudo_texto, encoding="utf-8")
                print(f"{tag}  [OK] Salvo (.json via Fetch JS): {caminho_final.name}")
                return caminho_final.name
        except Exception:
            pass

    # Estratégia 2: O Copilot do chefe renderizou o .json como Card/Button em vez de <a>
    await fechar_painel_preview_copilot(page, tag)

    el_json = page.locator("button, [role='button'], div, span").filter(
        has_text=re.compile(r"[\w\-]+\.json\b", re.IGNORECASE)
    )
    if await el_json.count() > 0:
        # 2A: Tenta clicar diretamente no elemento que menciona .json
        for i in range(await el_json.count() - 1, -1, -1):
            item = el_json.nth(i)
            if not await item.is_visible():
                continue
            try:
                async with page.expect_download(timeout=8000) as dl_info:
                    await item.click(timeout=3000)
                dl = await dl_info.value
                sugerido = dl.suggested_filename or "arquivos_recebidos.json"
                nome_seguro = re.sub(r'[<>:"/\\|?*\x00-\x1f]', "_", sugerido).rstrip(" .")
                if not nome_seguro.lower().endswith(".json"):
                    nome_seguro += ".json"
                caminho_final = pasta_projeto / nome_seguro
                await dl.save_as(str(caminho_final))
                print(f"{tag}  [OK] Salvo (.json via clique no Card): {caminho_final.name}")
                return caminho_final.name
            except Exception:
                await fechar_painel_preview_copilot(page, tag)

        # 2B: Procura botões 'Mais ações' na tela e verifica se algum baixa o .json
        botoes_mais = page.get_by_role(
            "button", name=re.compile(r"^Mais ações$|^More actions$|Mais op[çc][õo]es", re.IGNORECASE)
        )
        total_mais = await botoes_mais.count()
        for i in range(total_mais - 1, -1, -1):
            btn = botoes_mais.nth(i)
            if not await btn.is_visible():
                continue
            box = await btn.bounding_box()
            if box and box["y"] <= 95:
                continue
            try:
                await btn.scroll_into_view_if_needed(timeout=2000)
                await btn.click(timeout=3000)
                await page.wait_for_timeout(800)

                item_baixar = page.get_by_role(
                    "menuitem", name=re.compile(r"^Baixar$|^Download$", re.IGNORECASE)
                ).first
                if not await item_baixar.is_visible():
                    item_baixar = page.get_by_text(re.compile(r"^Baixar$|^Download$", re.IGNORECASE)).first

                if await item_baixar.is_visible():
                    async with page.expect_download(timeout=25000) as dl_info:
                        await item_baixar.click(timeout=4000)
                    dl = await dl_info.value
                    sugerido = dl.suggested_filename or ""
                    # Só aproveita se este Card for realmente o .json (ignora se for o .docx novamente)
                    if sugerido.lower().endswith(".json"):
                        nome_seguro = re.sub(r'[<>:"/\\|?*\x00-\x1f]', "_", sugerido).rstrip(" .")
                        caminho_final = pasta_projeto / nome_seguro
                        await dl.save_as(str(caminho_final))
                        print(f"{tag}  [OK] Salvo (.json via Card 'Mais ações'): {caminho_final.name}")
                        return caminho_final.name
                else:
                    await page.keyboard.press("Escape")
            except Exception:
                try:
                    await page.keyboard.press("Escape")
                except Exception:
                    pass

    # Estratégia 3: O Copilot exibiu o conteúdo JSON dentro de um bloco <code> / <pre> no chat
    blocos_codigo = page.locator("pre, code")
    cnt_code = await blocos_codigo.count()
    for i in range(cnt_code - 1, -1, -1):
        try:
            txt = (await blocos_codigo.nth(i).inner_text(timeout=2000)).strip()
            if txt.startswith("{") and txt.endswith("}"):
                json.loads(txt)  # Valida se é um JSON íntegro
                caminho_final = pasta_projeto / "arquivos_recebidos.json"
                caminho_final.write_text(txt, encoding="utf-8")
                print(f"{tag}  [OK] Salvo (.json extraído de bloco de código): {caminho_final.name}")
                return caminho_final.name
        except Exception:
            pass

    return None


# ==============================================================================
# v1.8 — ESPERA PELA ANÁLISE E DOWNLOAD DO .DOCX + .JSON NO COPILOT
# ==============================================================================

# Nome de arquivo .docx/.json citado na resposta (sem espaços nem reticências de nome truncado)
PADRAO_ARQUIVO_GERADO = re.compile(r"[^\s\\/:*?\"<>|…]+\.(docx|json)\b", re.IGNORECASE)
PADRAO_BOTAO_BAIXAR = re.compile(r"^(Baixar|Download|Fazer download|Baixar arquivo|Download file)$", re.IGNORECASE)
PADRAO_MAIS_ACOES = re.compile(r"^Mais ações$|^More actions$|Mais op[çc][õo]es|^More options$", re.IGNORECASE)
PADRAO_ITEM_BAIXAR = re.compile(r"^Baixar$|^Download$|^Fazer download$", re.IGNORECASE)

# Segundos que a página precisa ficar sem mudanças (e sem o botão "Parar")
# para considerarmos a resposta do Copilot terminada.
SEGUNDOS_ESTABILIDADE_RESPOSTA = 12


def _nome_seguro(nome: str) -> str:
    return re.sub(r'[<>:"/\\|?*\x00-\x1f]', "_", nome or "").rstrip(" .")


def _caminho_sem_sobrescrever_anexo(pasta: Path, nome: str, nomes_anexos: set[str]) -> Path:
    """Evita que um arquivo gerado pela IA substitua um arquivo original do projeto."""
    caminho = pasta / nome
    if nome.lower() in nomes_anexos:
        caminho = pasta / f"{Path(nome).stem}_IA{Path(nome).suffix}"
    return caminho


async def _listar_arquivos_gerados(page: Page, nomes_anexos: set[str]) -> dict[str, list[str]]:
    """Lê o texto visível da conversa e devolve os nomes .docx/.json que NÃO são anexos enviados."""
    try:
        texto = await page.locator("body").inner_text(timeout=5000)
    except Exception:
        return {"docx": [], "json": []}
    encontrados = {"docx": [], "json": []}
    for m in PADRAO_ARQUIVO_GERADO.finditer(texto):
        nome = m.group(0)
        if nome.lower() in nomes_anexos:
            continue
        lista = encontrados[m.group(1).lower()]
        if nome not in lista:
            lista.append(nome)
    return encontrados


async def _copilot_ainda_gerando(page: Page) -> bool:
    botao_parar = page.locator(
        'button[aria-label*="Parar"], button[aria-label*="Stop"], button[data-testid*="stop"]'
    )
    try:
        for i in range(await botao_parar.count()):
            if await botao_parar.nth(i).is_visible():
                return True
    except Exception:
        pass
    return False


async def aguardar_fim_resposta_copilot(page: Page, nomes_anexos: set[str], tag: str) -> dict[str, list[str]]:
    """
    Aguarda (até TIMEOUT_RESPOSTA_IA_MS) o Copilot terminar a análise.
    Considera terminada quando: já apareceu um .docx gerado, o botão "Parar" sumiu
    e a página ficou sem mudar de texto por SEGUNDOS_ESTABILIDADE_RESPOSTA.
    Se aparecerem o .docx E o .json, não precisa esperar o prazo inteiro.
    """
    minutos = TIMEOUT_RESPOSTA_IA_MS / 60000
    print(f"{tag} [4/4] Aguardando o Copilot processar (limite de {minutos:.0f} min)...")
    inicio = time.monotonic()
    prazo = inicio + (TIMEOUT_RESPOSTA_IA_MS / 1000.0)
    ultimo_texto = None
    estavel_desde = time.monotonic()
    ultimo_aviso = inicio
    arquivos = {"docx": [], "json": []}

    while time.monotonic() < prazo:
        await page.wait_for_timeout(2000)
        try:
            texto = await page.locator("body").inner_text(timeout=5000)
        except Exception:
            texto = ultimo_texto
        if texto != ultimo_texto:
            ultimo_texto = texto
            estavel_desde = time.monotonic()

        arquivos = await _listar_arquivos_gerados(page, nomes_anexos)
        gerando = await _copilot_ainda_gerando(page)
        parado_ha = time.monotonic() - estavel_desde

        if arquivos["docx"] and not gerando and parado_ha >= SEGUNDOS_ESTABILIDADE_RESPOSTA:
            # Se o .json ainda não apareceu, dá uma margem extra (o Copilot às vezes gera em sequência)
            if arquivos["json"] or parado_ha >= SEGUNDOS_ESTABILIDADE_RESPOSTA * 3:
                decorrido = (time.monotonic() - inicio) / 60
                print(
                    f"{tag}  -> Análise concluída em {decorrido:.1f} min. "
                    f"Arquivos na resposta: {arquivos['docx'] + arquivos['json']}"
                )
                return arquivos

        if time.monotonic() - ultimo_aviso >= 60:
            ultimo_aviso = time.monotonic()
            decorrido = (time.monotonic() - inicio) / 60
            print(f"{tag}  ... ainda processando ({decorrido:.0f}/{minutos:.0f} min)")

    if arquivos["docx"] or arquivos["json"]:
        print(f"{tag} [AVISO] Prazo de {minutos:.0f} min atingido; tentando baixar o que já foi gerado.")
        return arquivos
    raise TimeoutError(f"{tag} Tempo limite de {minutos:.0f} min atingido sem arquivos gerados pelo Copilot.")


async def _salvar_download(download, pasta: Path, nome_esperado: str, extensao: str,
                           nomes_anexos: set[str]) -> Path | None:
    """Salva o download só se ele for realmente do tipo esperado (.docx ou .json)."""
    sugerido = download.suggested_filename or ""
    if not sugerido.lower().endswith(f".{extensao}"):
        if "." in sugerido:
            # Veio outro tipo de arquivo (ex.: baixou o .docx quando queríamos o .json)
            try:
                await download.cancel()
            except Exception:
                pass
            return None
        sugerido = nome_esperado
    caminho = _caminho_sem_sobrescrever_anexo(pasta, _nome_seguro(sugerido), nomes_anexos)
    await download.save_as(str(caminho))
    if not caminho.is_file() or caminho.stat().st_size == 0:
        caminho.unlink(missing_ok=True)
        return None
    return caminho


async def _clicar_e_capturar(page: Page, alvo, pasta: Path, nome: str, extensao: str,
                             nomes_anexos: set[str], timeout_ms: int = 20000) -> Path | None:
    async with page.expect_download(timeout=timeout_ms) as dl_info:
        await alvo.click(timeout=4000)
    return await _salvar_download(await dl_info.value, pasta, nome, extensao, nomes_anexos)


async def _menu_baixar(page: Page, botao_mais, pasta: Path, nome: str, extensao: str,
                       nomes_anexos: set[str]) -> Path | None:
    """Abre 'Mais ações' e clica em 'Baixar'."""
    await botao_mais.scroll_into_view_if_needed(timeout=2000)
    await botao_mais.click(timeout=3000)
    await page.wait_for_timeout(800)
    item = page.get_by_role("menuitem", name=PADRAO_ITEM_BAIXAR).first
    if not await item.is_visible():
        item = page.get_by_text(PADRAO_ITEM_BAIXAR).first
    if not await item.is_visible():
        await page.keyboard.press("Escape")
        return None
    return await _clicar_e_capturar(page, item, pasta, nome, extensao, nomes_anexos, timeout_ms=45000)


async def baixar_arquivo_gerado_copilot(page: Page, nome: str, extensao: str, pasta: Path,
                                        nomes_anexos: set[str], tag: str) -> Path | None:
    """
    Baixa um arquivo (.docx ou .json) que aparece na resposta do Copilot, tentando em ordem:
      1. Link <a> direto com o nome do arquivo;
      2. Botão 'Baixar'/'Download' dentro do próprio card do arquivo;
      3. Botão 'Mais ações' do card -> 'Baixar';
      4. Abrir o card (pré-visualização) e clicar em 'Baixar' no painel, depois fechar o painel.
    """
    print(f"{tag}  -> Baixando '{nome}'...")
    await fechar_painel_preview_copilot(page, tag)

    ocorrencias = page.get_by_text(nome, exact=False)
    total = await ocorrencias.count()
    # Do último para o primeiro: o card do arquivo costuma vir depois do texto que o menciona
    for i in range(total - 1, -1, -1):
        el = ocorrencias.nth(i)
        try:
            if not await el.is_visible():
                continue
            await el.scroll_into_view_if_needed(timeout=2000)
            await el.hover(timeout=2000)
        except Exception:
            continue

        # 1. Link direto
        link = el.locator("xpath=ancestor-or-self::a[1]")
        if await link.count() > 0:
            try:
                await link.first.evaluate("(a, n) => a.setAttribute('download', n)", nome)
            except Exception:
                pass
            try:
                salvo = await _clicar_e_capturar(page, link.first, pasta, nome, extensao, nomes_anexos)
                if salvo:
                    return salvo
            except Exception:
                await fechar_painel_preview_copilot(page, tag)

        # 2 e 3. Procura botões no card (sobe até 6 níveis a partir do nome do arquivo)
        for nivel in range(1, 7):
            card = el.locator(f"xpath=ancestor::*[{nivel}]")
            try:
                if await card.get_by_text(PADRAO_ARQUIVO_GERADO).count() > 1:
                    break  # Subiu demais: o bloco já contém outros arquivos
                await card.hover(timeout=1500)
            except Exception:
                break
            for botao, abre_menu in ((card.get_by_role("button", name=PADRAO_BOTAO_BAIXAR), False),
                                     (card.get_by_role("button", name=PADRAO_MAIS_ACOES), True)):
                if await botao.count() == 0 or not await botao.first.is_visible():
                    continue
                try:
                    if abre_menu:
                        salvo = await _menu_baixar(page, botao.first, pasta, nome, extensao, nomes_anexos)
                    else:
                        salvo = await _clicar_e_capturar(page, botao.first, pasta, nome, extensao, nomes_anexos)
                    if salvo:
                        return salvo
                except Exception:
                    try:
                        await page.keyboard.press("Escape")
                    except Exception:
                        pass

        # 4. Abre a pré-visualização e procura 'Baixar' no painel
        try:
            try:
                salvo = await _clicar_e_capturar(page, el, pasta, nome, extensao, nomes_anexos, timeout_ms=6000)
                if salvo:
                    return salvo
            except Exception:
                pass
            await page.wait_for_timeout(1500)
            botoes_painel = page.get_by_role("button", name=PADRAO_BOTAO_BAIXAR)
            for j in range(await botoes_painel.count() - 1, -1, -1):
                b = botoes_painel.nth(j)
                if await b.is_visible():
                    try:
                        salvo = await _clicar_e_capturar(page, b, pasta, nome, extensao, nomes_anexos)
                        if salvo:
                            return salvo
                    except Exception:
                        pass
            mais_painel = page.get_by_role("button", name=PADRAO_MAIS_ACOES)
            for j in range(await mais_painel.count() - 1, -1, -1):
                b = mais_painel.nth(j)
                if await b.is_visible():
                    box = await b.bounding_box()
                    if box and box["y"] <= 95:
                        continue  # '...' da barra superior do Copilot
                    try:
                        salvo = await _menu_baixar(page, b, pasta, nome, extensao, nomes_anexos)
                        if salvo:
                            return salvo
                    except Exception:
                        try:
                            await page.keyboard.press("Escape")
                        except Exception:
                            pass
        finally:
            await fechar_painel_preview_copilot(page, tag)

    return None


async def aguardar_e_baixar_arquivos(page: Page, pasta_projeto: Path, qtd_links_antes: int, tag: str):
    """
    Aguarda o Copilot terminar a análise (até 10 min) e baixa o .docx E o .json
    que aparecem no final da resposta. Só dá sucesso se os dois forem salvos.
    (qtd_links_antes é mantido por compatibilidade com o loop do agente.)
    """
    # Arquivos originais do projeto (os anexos enviados) — nunca confundir com a resposta da IA
    nomes_anexos = {
        p.name.lower() for p in pasta_projeto.iterdir() if p.is_file() and p.name != ARQUIVO_SENTINELA
    }

    arquivos = await aguardar_fim_resposta_copilot(page, nomes_anexos, tag)

    async with LOCK_UI_NAVEGACAO:
        try:
            await page.bring_to_front()
        except Exception:
            pass

        salvos: dict[str, Path] = {}
        for extensao in ("docx", "json"):
            for nome in reversed(arquivos[extensao]):  # o mais recente primeiro
                try:
                    caminho = await baixar_arquivo_gerado_copilot(
                        page, nome, extensao, pasta_projeto, nomes_anexos, tag
                    )
                except Exception as erro:
                    print(f"{tag} [AVISO] Erro ao baixar '{nome}': {erro}")
                    caminho = None
                if caminho:
                    salvos[extensao] = caminho
                    print(f"{tag}  [OK] Salvo (.{extensao}): {caminho.name}")
                    break

        # Plano B (métodos da v1.7) para o que ainda faltar
        if "docx" not in salvos:
            antes = {p.name for p in pasta_projeto.iterdir()}
            if await baixar_docx_via_card_copilot(page, pasta_projeto, pos=1, tag=tag):
                novos = [p for p in pasta_projeto.iterdir()
                         if p.name not in antes and p.suffix.lower() == ".docx"]
                if novos:
                    salvos["docx"] = novos[0]
        if "json" not in salvos:
            nome_json = await _garantir_download_json_copilot(page, pasta_projeto, tag)
            if nome_json and nome_json.lower() not in nomes_anexos:
                salvos["json"] = pasta_projeto / nome_json

    faltando = [f".{ext}" for ext in ("docx", "json") if ext not in salvos]
    if faltando:
        raise RuntimeError(
            f"{tag} Download INCOMPLETO! Faltando: {', '.join(faltando)} | "
            f"Arquivos citados na resposta: {arquivos['docx'] + arquivos['json']} | "
            f"Salvos: {[p.name for p in salvos.values()]}"
        )
    print(f"{tag}  [OK] .docx e .json salvos em '{pasta_projeto.name}'.")


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

            await anexar_arquivos_silenciosamente(page, arquivos_para_enviar, tag)

            # Conta os links DEPOIS de anexar, para não contar os arquivos enviados como se fossem resposta!
            links_arquivos = page.get_by_role("link").filter(
                has_text=re.compile(r"\.(txt|json|docx|doc|xlsx|csv|pdf|zip)\b", re.IGNORECASE)
            )
            qtd_links_antes = await links_arquivos.count()

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