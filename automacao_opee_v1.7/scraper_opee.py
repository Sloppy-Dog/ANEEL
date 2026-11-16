import argparse
import re
import time
from playwright.sync_api import sync_playwright, Page
from config import (
    DIR_BAIXADOS,
    DIR_EM_ANALISE,
    DIR_CONCLUIDOS,
    QTD_ARQUIVOS_ESPERADOS,
    ARQUIVO_HISTORICO,
    ARQUIVO_SENTINELA,
    inicializar_pastas,
    MAX_PROJETOS_POR_EXECUCAO,
)

from controle_downloads import HistoricoDownloads, chave_projeto
from pathlib import Path
from controle_planilha import (
    CAMINHO_PLANILHA_PADRAO,
    marcar_projeto_amarelo,
    normalizar_codigo_projeto,
    selecionar_lote_planilha_interativo,
    sincronizar_concluidos_planilha,
)

def extrair_codigo_limpo(texto_botao: str) -> str:
    """
    Remove o status ('Aguardando definição de avaliadores', etc) do texto do botao e
    substitui '/' por '_' para criar um nome de pasta limpo no Windows.
    Ex: 'PE-00404-0072/2016 (EMS) Aguardando definição de avaliadores' -> 'PE-00404-0072_2016 (EMS)'
    """
    match = re.search(r"(PE-[^\s]+\s*\([^)]+\))", texto_botao, re.IGNORECASE)
    nome_base = match.group(1) if match else texto_botao.splitlines()[0]
    nome_limpo = re.sub(r'[<>:"/\\|?*]', "_", nome_base)
    return nome_limpo.strip()


def navegar_e_filtrar(page: Page, termo_busca: str | None = None):
    """
    Navega ate o Repositorio de Projetos, filtra 'Aguardando avaliacao externa'
    e, se `termo_busca` for informado, pesquisa pelo nome/codigo do projeto.
    """
    print("[1/3] Verificando tela do Repositorio de Projetos...")

    if "repositorio" not in page.url.lower() and page.get_by_title("Buscar Projeto").count() == 0:
        print(" -> Abrindo menu lateral -> Observatorio -> Repositorio de Projetos...")
        if page.locator("#menu_toggle").is_visible():
            page.locator("#menu_toggle").click()
        page.get_by_text("Observatório", exact=True).click()
        page.get_by_role("link", name="Repositório de Projetos").click()
        page.wait_for_load_state("networkidle")

    print("[2/3] Aplicando filtro 'Aguardando definição de avaliadores' (opcao 2)...")
    botao_buscar_filtro = page.get_by_title("Buscar Projeto")
    combobox_status = page.get_by_role("combobox").nth(1)

    if not combobox_status.is_visible():
        botao_buscar_filtro.click()
        combobox_status.wait_for(state="visible", timeout=5000)

    # Se estiver em modo automático, limpa eventual texto residual de buscas anteriores na aba
    if not termo_busca:
        caixa_busca = page.get_by_role("textbox").first
        if caixa_busca.is_visible():
            try:
                caixa_busca.fill("")
            except Exception:
                pass

    combobox_status.select_option("2")
    page.get_by_role("button", name="Buscar").click()

    page.wait_for_load_state("networkidle")
    time.sleep(2)

    # Passo adicional: pesquisa por nome/código de projeto caso solicitado pelo usuário
    if termo_busca and termo_busca.strip():
        termo_limpo = termo_busca.strip()
        print(f"[2.5/3] Pesquisando pelo projeto '{termo_limpo}'...")
        caixa_busca = page.get_by_role("textbox").first
        if not caixa_busca.is_visible():
            botao_buscar_filtro.click()
            caixa_busca.wait_for(state="visible", timeout=5000)

        caixa_busca.click()
        caixa_busca.fill(termo_limpo)
        page.get_by_role("button", name="Buscar").click()

        page.wait_for_load_state("networkidle")
        time.sleep(2)


def baixar_arquivos_do_projeto(page: Page, botao_projeto, nome_pasta: str) -> bool:
    """Expande o projeto, clica na pasta Relatorio Final e baixa os 5 arquivos."""
    pasta_destino = DIR_BAIXADOS / nome_pasta
    pasta_destino.mkdir(parents=True, exist_ok=True)

    print(f"\n---> Iniciando coleta do projeto: {nome_pasta}")

    # Localiza o <li> raiz deste projeto especifico
    li_projeto = botao_projeto.locator("xpath=ancestor::li[1]")

    # 1. Expande o projeto (caso a sublista dele ainda nao esteja visivel)
    sublista_projeto = li_projeto.locator("> div > ul")
    if not sublista_projeto.is_visible():
        botao_expandir_proj = botao_projeto.get_by_role("button").first
        botao_expandir_proj.click()
        sublista_projeto.wait_for(state="visible", timeout=10000)
        time.sleep(1)

    # 2. Localiza a linha (<p>) especifica da pasta "Relatório Final"
    p_relatorio_final = sublista_projeto.locator("p").filter(
        has_text=re.compile(r"Relat[óo]rio\s+Final", re.IGNORECASE)
    ).first

    if p_relatorio_final.count() == 0:
        print(" [!] Texto 'Relatório Final' nao encontrado, usando 2a pasta (li:nth-child(2))...")
        li_relatorio_final = sublista_projeto.locator("> li:nth-child(2)")
        p_relatorio_final = li_relatorio_final.locator("> p")
    else:
        # Pega o <li> exato que envolve a pasta Relatorio Final
        li_relatorio_final = p_relatorio_final.locator("xpath=ancestor::li[1]")

    # 3. Expande a pasta "Relatório Final" clicando no botao de icone (sem texto) dentro do <p>
    sublista_arquivos = li_relatorio_final.locator("> div > ul")
    if not sublista_arquivos.is_visible():
        print(" -> Expandindo subpasta 'Relatório Final'...")
        botao_expandir_rf = p_relatorio_final.get_by_role("button").filter(
            has_text=re.compile(r"^$")
        ).first

        if botao_expandir_rf.count() > 0:
            botao_expandir_rf.click()
        else:
            p_relatorio_final.locator("button").first.click()

        # Aguarda a lista de arquivos dentro de Relatorio Final ficar visivel na tela
        sublista_arquivos.wait_for(state="visible", timeout=10000)
        time.sleep(1)

    # 4. Seleciona APENAS os <li> filhos (os arquivos), ignorando o <p> da propria pasta!
    itens_arquivos = sublista_arquivos.locator("> li")
    total_arquivos = itens_arquivos.count()
    print(f" -> Encontrados {total_arquivos} arquivos dentro de 'Relatório Final'.")

    if total_arquivos == 0:
        print(f" [ERRO] Nenhum arquivo encontrado dentro de Relatório Final em {nome_pasta}.")
        return False

    if total_arquivos != QTD_ARQUIVOS_ESPERADOS:
        print(f" [AVISO] Esperados {QTD_ARQUIVOS_ESPERADOS} arquivos; o site listou {total_arquivos}. Todos serão exigidos.")
        #print(f" [A quantidade total de arquivos não era o suficiente, cancelando...")
        #return False
        
    # 5. Faz o download de todos os arquivos listados
    arquivos_baixados = 0
    for idx in range(total_arquivos):
        item_li = itens_arquivos.nth(idx)
        # Pega o link de download (3º link da barra direita do arquivo)
        link_download = item_li.locator("> p > .pull-right > a:nth-child(3)")

        try:
            link_download.wait_for(state="visible", timeout=10000)
            with page.expect_download(timeout=60000) as download_info:
                link_download.click()
            download = download_info.value

            # Remove componentes de caminho e caracteres inválidos no Windows.
            nome_seguro = re.sub(r'[<>:"/\\|?*\x00-\x1f]', "_", download.suggested_filename).rstrip(" .")
            nome_arquivo = f"{idx + 1:02d}_{nome_seguro or 'arquivo'}"
            caminho_final = pasta_destino / nome_arquivo
            # Publica o arquivo final somente após concluir a transferência.
            caminho_temporario = caminho_final.with_name(caminho_final.name + ".part")
            download.save_as(str(caminho_temporario))
            if caminho_temporario.stat().st_size == 0:
                caminho_temporario.unlink()
                raise RuntimeError("O arquivo recebido está vazio.")
            caminho_temporario.replace(caminho_final)

            arquivos_baixados += 1
            print(f"    [{arquivos_baixados}/{total_arquivos}] Salvo: {nome_arquivo}")
        except Exception as e:
            print(f"    [ERRO] Falha ao baixar o arquivo {idx + 1}: {e}")

    # 6. Cria o arquivo sentinela (_PRONTO.flag) ao concluir
    if arquivos_baixados == total_arquivos and total_arquivos > 0:
        (pasta_destino / ARQUIVO_SENTINELA).touch()
        print(f" [OK] Projeto {nome_pasta} baixado com sucesso! Sentinela '{ARQUIVO_SENTINELA}' criado.")
        return True
    else:
        print(f" [AVISO] Download incompleto ({arquivos_baixados}/{total_arquivos} arquivos).")
        return False


def botoes_dos_projetos(page: Page):
    # Mantém o seletor de projetos do fluxo original.
    return page.get_by_role("button").filter(
        has_text=re.compile(r"PE-\d+", re.IGNORECASE)
    )


def listar_projetos(page: Page):
    """Fotografa os projetos antes de expandir pastas e alterar seus textos."""
    projetos = []
    vistos = set()
    for texto in botoes_dos_projetos(page).all_inner_texts():
        nome = extrair_codigo_limpo(texto.strip())
        codigo = chave_projeto(nome)
        if codigo not in vistos:
            projetos.append((codigo, nome))
            vistos.add(codigo)
    return projetos


def assinatura_da_pagina(page: Page):
    return tuple(codigo for codigo, _ in listar_projetos(page))


def avancar_pagina(page: Page, numero: int, assinatura_anterior,
                    timeout_segundos: float = 30) -> bool:
    """Usa os links numéricos confirmados na gravação enviada pelo usuário.

    Só confirma a troca depois que a lista de códigos mudar e estabilizar.
    Sem link habilitado para a página seguinte, encerra a varredura.
    """
    links = page.get_by_role("link", name=str(numero), exact=True)
    link = None
    for indice in range(links.count()):
        candidato = links.nth(indice)
        if candidato.is_visible() and candidato.is_enabled():
            if candidato.get_attribute("aria-disabled") != "true":
                link = candidato
                break
    if link is None:
        return False

    print(f"[PAGINAÇÃO] Abrindo página {numero}...")
    link.click(timeout=15000)
    prazo = time.monotonic() + timeout_segundos
    ultima = None
    estavel_desde = time.monotonic()
    while time.monotonic() < prazo:
        atual = assinatura_da_pagina(page)
        if atual and atual != assinatura_anterior:
            if atual != ultima:
                ultima = atual
                estavel_desde = time.monotonic()
            elif time.monotonic() - estavel_desde >= 0.6:
                return True
        else:
            ultima = None
            estavel_desde = time.monotonic()
        page.wait_for_timeout(200)
    raise TimeoutError(
        f"O clique na página {numero} não produziu uma nova lista estável "
        f"em {timeout_segundos:g} segundos. Confira a sessão e a paginação."
    )


def coletar_projetos(page: Page, historico: HistoricoDownloads, quantidade: int):
    """A meta considera apenas novos projetos integralmente concluídos."""
    if quantidade <= 0:
        raise ValueError("A quantidade deve ser um inteiro maior que zero.")
    resumo = {"concluidos": 0, "pulados": 0, "falhas": 0,
              "paginas": 0, "motivo": ""}
    pagina_atual = 1  # A busca filtrada inicia a listagem na primeira página.
    paginas_visitadas = set()
    projetos_visitados = set()

    while resumo["concluidos"] < quantidade:
        projetos = listar_projetos(page)
        assinatura = tuple(codigo for codigo, _ in projetos)
        if not assinatura:
            resumo["motivo"] = "Nenhum projeto encontrado na listagem atual."
            break
        if assinatura in paginas_visitadas:
            resumo["motivo"] = "Página repetida detectada; varredura interrompida."
            break
        paginas_visitadas.add(assinatura)
        resumo["paginas"] += 1
        print(f"\n[PÁGINA {pagina_atual}] {len(projetos)} projetos; "
              f"meta: {resumo['concluidos']}/{quantidade} novos concluídos.")

        for codigo, nome_pasta in projetos:
            if resumo["concluidos"] >= quantidade:
                break
            if codigo in projetos_visitados:
                continue
            projetos_visitados.add(codigo)
            if historico.contem(codigo):
                resumo["pulados"] += 1
                print(f" -> Pulando {codigo} (já registrado).")
                continue

            # Localiza novamente pelo código; expandir um projeto pode alterar
            # o DOM, então não reutiliza o índice da lista anterior.
            botao = botoes_dos_projetos(page).filter(
                has_text=re.compile(re.escape(codigo) + r"(?!\d)", re.I)
            ).first
            try:
                sucesso = baixar_arquivos_do_projeto(page, botao, nome_pasta)
            except Exception as erro:
                print(f" [ERRO] Falha no projeto {codigo}: {erro}")
                sucesso = False
            if sucesso:
                # Erros de gravação do histórico interrompem a execução.
                historico.registrar(codigo)
                resumo["concluidos"] += 1
                print(f" [META] {resumo['concluidos']}/{quantidade} novos concluídos.")
            else:
                resumo["falhas"] += 1

        if resumo["concluidos"] >= quantidade:
            resumo["motivo"] = "Quantidade solicitada atingida."
            break
        try:
            avancou = avancar_pagina(page, pagina_atual + 1, assinatura)
        except Exception as erro:
            resumo["motivo"] = f"Falha ao trocar de página: {erro}"
            break
        if not avancou:
            resumo["motivo"] = (
                f"Não há link habilitado para a página {pagina_atual + 1}. "
                "Fim das páginas acessíveis pelos links numéricos."
            )
            break
        pagina_atual += 1

    print(f"\nResumo: {resumo['concluidos']}/{quantidade} novos concluídos, "
          f"{resumo['pulados']} já registrados, {resumo['falhas']} com falha, "
          f"{resumo['paginas']} página(s) percorrida(s).")
    print(resumo["motivo"])
    if resumo["concluidos"] < quantidade:
        print("[AVISO] A meta não foi atingida. Confira as falhas e a disponibilidade de novos projetos.")
    return resumo


def _projeto_ja_baixado_em_disco(codigo: str) -> bool:
    """Verifica se já existe pasta pronta para o projeto em 01_Baixados, 02_Em_Analise ou 03_Concluidos."""
    cod_norm = normalizar_codigo_projeto(codigo)
    if not cod_norm:
        return False
    for diretorio in (DIR_BAIXADOS, DIR_EM_ANALISE, DIR_CONCLUIDOS):
        if not diretorio.exists():
            continue
        for pasta in diretorio.iterdir():
            if pasta.is_dir() and normalizar_codigo_projeto(pasta.name) == cod_norm:
                if diretorio == DIR_BAIXADOS and not (pasta / ARQUIVO_SENTINELA).is_file():
                    continue
                return True
    return False


def coletar_projetos_da_planilha(
    page: Page,
    historico: HistoricoDownloads,
    projetos_candidatos: list[str],
    quantidade: int,
    caminho_planilha: Path = CAMINHO_PLANILHA_PADRAO,
) -> dict:
    """
    Percorre a lista de códigos da planilha em ordem, pesquisando um a um no OPEE.
    - Ao baixar com sucesso, pinta a célula de AMARELO na planilha.
    - Caso um projeto falhe ou não seja achado no OPEE, tenta automaticamente o próximo da lista.
    """
    resumo = {
        "concluidos": 0,
        "pulados": 0,
        "falhas": 0,
        "paginas": 0,
        "motivo": "",
        "consumidos_da_lista": 0,
    }

    total_candidatos = len(projetos_candidatos)
    if total_candidatos == 0:
        resumo["motivo"] = "Lista de projetos da planilha está vazia."
        return resumo

    meta_efetiva = min(quantidade, total_candidatos)
    print(
        f"\n[MODO PLANILHA] Buscando até {meta_efetiva} projeto(s) dentre {total_candidatos} "
        "candidato(s) em branco do lote..."
    )

    for idx, codigo_planilha in enumerate(projetos_candidatos, start=1):
        if resumo["concluidos"] >= quantidade:
            break

        resumo["consumidos_da_lista"] = idx
        codigo_norm = normalizar_codigo_projeto(codigo_planilha) or codigo_planilha.strip()

        # Se o projeto já estiver baixado em disco, garante a cor na planilha e pula para o próximo
        if historico.contem(codigo_norm) and _projeto_ja_baixado_em_disco(codigo_norm):
            resumo["pulados"] += 1
            print(f"\n[{idx}/{total_candidatos}] Pulando {codigo_norm} (já baixado em disco).")
            marcar_projeto_amarelo(codigo_norm, caminho_planilha)
            continue

        print(
            f"\n[{idx}/{total_candidatos}] Pesquisando projeto da planilha no OPEE: {codigo_norm} "
            f"(meta: {resumo['concluidos']}/{meta_efetiva})"
        )
        try:
            navegar_e_filtrar(page, termo_busca=codigo_norm)
            resumo["paginas"] += 1
        except Exception as erro_nav:
            print(f" [ERRO] Falha ao filtrar '{codigo_norm}' no OPEE: {erro_nav}. Tentando próximo...")
            resumo["falhas"] += 1
            continue

        projetos_listados = listar_projetos(page)
        if not projetos_listados:
            print(f" [AVISO] Projeto '{codigo_norm}' não encontrado na listagem do OPEE. Tentando próximo...")
            resumo["falhas"] += 1
            continue

        # Procura o projeto exato na resposta da busca (ou pega o primeiro correspondente)
        escolhido = None
        for cod_item, nome_pasta in projetos_listados:
            if normalizar_codigo_projeto(cod_item) == codigo_norm or cod_item.upper() == codigo_norm.upper():
                escolhido = (cod_item, nome_pasta)
                break
        if escolhido is None:
            escolhido = projetos_listados[0]

        cod_item, nome_pasta = escolhido
        botao = botoes_dos_projetos(page).filter(
            has_text=re.compile(re.escape(cod_item) + r"(?!\d)", re.I)
        ).first

        try:
            sucesso = baixar_arquivos_do_projeto(page, botao, nome_pasta)
        except Exception as erro_dl:
            print(f" [ERRO] Falha ao baixar '{codigo_norm}': {erro_dl}")
            sucesso = False

        if sucesso:
            historico.registrar(cod_item)
            marcar_projeto_amarelo(codigo_norm, caminho_planilha)
            resumo["concluidos"] += 1
            print(f" [META PLANILHA] {resumo['concluidos']}/{meta_efetiva} concluídos.")
        else:
            resumo["falhas"] += 1
            print(" -> Passando para o próximo projeto do mesmo lote na planilha...")

    if resumo["concluidos"] >= meta_efetiva:
        resumo["motivo"] = "Meta de downloads da planilha atingida."
    else:
        resumo["motivo"] = "Todos os candidatos do lote na planilha foram percorridos."

    print(
        f"\nResumo (Planilha): {resumo['concluidos']}/{meta_efetiva} novos baixados (AMARELO), "
        f"{resumo['pulados']} já em disco, {resumo['falhas']} não encontrados/falhas."
    )
    print(resumo["motivo"])
    return resumo


def executar_scraper(
    quantidade=MAX_PROJETOS_POR_EXECUCAO,
    termo_busca: str | None = None,
    projetos_planilha: list[str] | None = None,
    caminho_planilha: Path = CAMINHO_PLANILHA_PADRAO,
):
    if not isinstance(quantidade, int) or isinstance(quantidade, bool) or quantidade <= 0:
        raise ValueError("A quantidade deve ser um inteiro maior que zero.")
    inicializar_pastas()
    historico = HistoricoDownloads(ARQUIVO_HISTORICO)
    importados = historico.importar_pastas(
        DIR_BAIXADOS, DIR_EM_ANALISE, DIR_CONCLUIDOS, ARQUIVO_SENTINELA
    )
    print(f"[HISTÓRICO] {len(historico.projetos)} projetos registrados; {importados} importados das pastas.")

    with sync_playwright() as p:
        print("Conectando ao Microsoft Edge aberto na porta 9222...")
        browser = p.chromium.connect_over_cdp("http://localhost:9222")
        context = browser.contexts[0]

        page = context.pages[0]
        for aba in context.pages:
            if "opee" in aba.url.lower() or "aneel" in aba.url.lower():
                page = aba
                break

        print(f"[CONECTADO] Aba ativa: {page.title()}")

        if projetos_planilha is not None:
            resumo = coletar_projetos_da_planilha(
                page,
                historico,
                projetos_candidatos=projetos_planilha,
                quantidade=quantidade,
                caminho_planilha=caminho_planilha,
            )
        else:
            navegar_e_filtrar(page, termo_busca=termo_busca)
            resumo = coletar_projetos(page, historico, quantidade)

        print("Pasta dos downloads:", DIR_BAIXADOS)
        print("Histórico permanente:", ARQUIVO_HISTORICO)
        return resumo


def inteiro_positivo(valor):
    try:
        quantidade = int(valor)
    except (ValueError, TypeError) as erro:
        raise argparse.ArgumentTypeError("Informe um número inteiro maior que zero.") from erro
    if quantidade <= 0:
        raise argparse.ArgumentTypeError("Informe um número inteiro maior que zero.")
    return quantidade


if __name__ == "__main__":
    parser = argparse.ArgumentParser(
        description="Baixa novos projetos do OPEE (via Planilha, busca por código ou modo automático)."
    )
    parser.add_argument(
        "--quantidade", "-q", type=inteiro_positivo,
        default=MAX_PROJETOS_POR_EXECUCAO,
        help=f"Quantidade de novos projetos a concluir (padrão no config.py: {MAX_PROJETOS_POR_EXECUCAO}).",
    )
    parser.add_argument(
        "--projeto", "-p", type=str, default=None,
        help="Código ou nome do projeto para pesquisar no OPEE (ex: PE-00404).",
    )
    parser.add_argument(
        "--planilha", action="store_true",
        help="Inicia diretamente no modo de extração via planilha.",
    )
    args = parser.parse_args()

    usar_planilha = args.planilha
    if not usar_planilha and args.projeto is None:
        print("\n" + "=" * 65)
        print(" SELECIONE O MODO DE EXTRAÇÃO DO SCRAPER")
        print("=" * 65)
        print(" [1] Extração COM Planilha (aba 'Projeto Piloto Lotes')")
        print(" [2] Extração SEM Planilha (Pesquisa por código / Automático)")
        escolha = input("\nEscolha uma opção [1/2] (Padrão=2): ").strip()
        usar_planilha = (escolha == "1")

    if usar_planilha:
        prefixo, lista_projetos = selecionar_lote_planilha_interativo(CAMINHO_PLANILHA_PADRAO)
        if not lista_projetos:
            raise SystemExit(0)
        resultado = executar_scraper(
            args.quantidade,
            projetos_planilha=lista_projetos,
            caminho_planilha=CAMINHO_PLANILHA_PADRAO,
        )
        sincronizar_concluidos_planilha(CAMINHO_PLANILHA_PADRAO)
        raise SystemExit(0 if resultado["concluidos"] > 0 else 1)
    else:
        termo = args.projeto
        if termo is None:
            resposta = input(
                "\n[OPÇÃO DE BUSCA] Digite o nome/código do projeto para pesquisar (ex: PE-00404) "
                "ou pressione ENTER para baixar no modo automático: "
            ).strip()
            termo = resposta if resposta else None

        resultado = executar_scraper(args.quantidade, termo_busca=termo)
        raise SystemExit(0 if resultado["concluidos"] >= args.quantidade else 1)