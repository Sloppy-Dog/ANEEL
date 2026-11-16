import re
from collections import OrderedDict
from pathlib import Path
import openpyxl
from openpyxl.styles import PatternFill

import config
from config import DIR_CONCLUIDOS

# Caminho padrão da planilha (pode ser sobrescrito no config.py se desejar)
CAMINHO_PLANILHA_PADRAO = Path(
    getattr(config, "ARQUIVO_PLANILHA", "Avaliação PEE - IA e Avaliadores.xlsx")
)
ABA_LOTES = "Projeto Piloto Lotes"

# Cores exatas extraídas da aba 'Legenda Cores'
FILL_AMARELO = PatternFill(fill_type="solid", start_color="FFFFFF00", end_color="FFFFFF00")
FILL_ROXO = PatternFill(fill_type="solid", start_color="FF7030A0", end_color="FF7030A0")

PADRAO_CODIGO_PROJETO = re.compile(r"(PE-\d{5}-\d+)[/_](\d{4})", re.IGNORECASE)
PADRAO_PREFIXO_PE = re.compile(r"^(PE-\d{5})", re.IGNORECASE)


def normalizar_codigo_projeto(texto: str) -> str | None:
    """
    Normaliza qualquer string de projeto (da planilha ou nome de pasta no Windows)
    para o formato exato da coluna A da planilha: 'PE-XXXXX-XXXX/YYYY'.
    Ex: 'PE-02937-0032_2012 (EMS)' -> 'PE-02937-0032/2012'
    """
    if not texto:
        return None
    m = PADRAO_CODIGO_PROJETO.search(str(texto).strip())
    if not m:
        return None
    return f"{m.group(1).upper()}/{m.group(2)}"


def extrair_prefixo_pe(codigo: str) -> str | None:
    """Retorna as iniciais 'PE-XXXXX' do código do projeto."""
    m = PADRAO_PREFIXO_PE.match(str(codigo).strip())
    return m.group(1).upper() if m else None


def classificar_cor_celula(cell) -> str:
    """
    Classifica a cor de fundo da célula conforme a aba 'Legenda Cores'.
    Trata tanto células sem preenchimento (None/00000000) quanto células pintadas
    com o Branco de Tema do Excel (theme=0, tint=0.0) como 'BRANCO'.
    """
    fill = cell.fill
    if not fill or fill.fill_type is None or fill.fill_type == "none":
        return "BRANCO"

    fg = fill.fgColor
    if not fg:
        return "BRANCO"

    if fg.type == "theme":
        if fg.theme == 0 and abs(fg.tint or 0.0) < 0.05:
            return "BRANCO"
        return f"TEMA_{fg.theme}"

    if fg.type == "rgb" and fg.rgb:
        rgb_full = fg.rgb.upper()
        rgb6 = rgb_full[-6:]
        if rgb_full == "00000000" or rgb6 == "FFFFFF":
            return "BRANCO"
        if rgb6 == "FFFF00":
            return "AMARELO"
        if rgb6 == "7030A0":
            return "ROXO"
        if rgb6 == "0070C0":
            return "AZUL"
        if rgb6 == "92D050":
            return "VERDE"
        if rgb6 == "FF0000":
            return "VERMELHO"
        return f"RGB_{rgb6}"

    return "OUTRO"


def _salvar_workbook_seguro(wb: openpyxl.Workbook, caminho_planilha: Path):
    """Salva a planilha tratando o caso em que o arquivo está aberto no Excel."""
    while True:
        try:
            wb.save(str(caminho_planilha))
            return
        except PermissionError:
            input(
                f"\n[AVISO PLANILHA] O arquivo '{caminho_planilha.name}' está aberto no Excel! "
                "Feche-o e pressione ENTER para salvar a atualização de cor..."
            )


def listar_grupos_em_branco(caminho_planilha: Path = CAMINHO_PLANILHA_PADRAO) -> OrderedDict[str, list[str]]:
    """
    Lê a aba 'Projeto Piloto Lotes' e agrupa os projetos com cor BRANCA
    pelas iniciais 'PE-XXXXX', mantendo a ordem exata das linhas da planilha.
    """
    if not caminho_planilha.exists():
        raise FileNotFoundError(f"Planilha não encontrada em: {caminho_planilha.resolve()}")

    wb = openpyxl.load_workbook(str(caminho_planilha))
    if ABA_LOTES not in wb.sheetnames:
        raise ValueError(f"Aba '{ABA_LOTES}' não encontrada na planilha '{caminho_planilha.name}'.")

    ws = wb[ABA_LOTES]
    grupos: OrderedDict[str, list[str]] = OrderedDict()

    for row_idx in range(1, ws.max_row + 1):
        cell = ws.cell(row=row_idx, column=1)
        codigo = normalizar_codigo_projeto(cell.value)
        if not codigo:
            continue

        cor = classificar_cor_celula(cell)
        if cor == "BRANCO":
            prefixo = extrair_prefixo_pe(codigo)
            if prefixo:
                grupos.setdefault(prefixo, []).append(codigo)

    wb.close()
    return grupos


def selecionar_lote_planilha_interativo(
    caminho_planilha: Path = CAMINHO_PLANILHA_PADRAO,
) -> tuple[str | None, list[str]]:
    """
    Lista quantos projetos em branco existem para cada inicial 'PE-XXXXX' e pergunta
    ao usuário se deseja seguir com aquele grupo ou procurar o próximo código.
    Retorna (prefixo_escolhido, lista_de_codigos_completos).
    """
    # Antes de listar os projetos em branco, sincroniza eventuais concluídos da pasta 03
    sincronizar_concluidos_planilha(caminho_planilha)

    grupos = listar_grupos_em_branco(caminho_planilha)
    if not grupos:
        print("\n[PLANILHA] Nenhum projeto em branco encontrado na aba 'Projeto Piloto Lotes'.")
        return None, []

    print("\n" + "=" * 70)
    print(f" LOTES EM BRANCO ENCONTRADOS EM '{ABA_LOTES}'")
    print("=" * 70)
    for pref, lista in grupos.items():
        print(f"  • {pref}: {len(lista)} projeto(s) em branco")
    print("=" * 70)

    for prefixo, projetos in grupos.items():
        while True:
            resp = input(
                f"\n[PLANILHA] Encontrados {len(projetos)} projeto(s) em branco com inicial '{prefixo}'. "
                f"Deseja seguir com '{prefixo}'? [S/n]: "
            ).strip().lower()

            if resp in ("", "s", "sim", "y", "yes"):
                print(f" -> Lote '{prefixo}' selecionado ({len(projetos)} projeto(s) na fila da planilha).")
                return prefixo, projetos
            elif resp in ("n", "nao", "não", "no"):
                print(f" -> Pulando '{prefixo}'. Procurando próximo lote na planilha...")
                break
            else:
                print(" [!] Digite 'S' para confirmar ou 'N' para tentar o próximo lote.")

    print("\n[PLANILHA] Todos os lotes em branco disponíveis foram recusados.")
    return None, []


def marcar_projeto_amarelo(
    codigo_ou_pasta: str,
    caminho_planilha: Path = CAMINHO_PLANILHA_PADRAO,
) -> bool:
    """
    Pinta o projeto de AMARELO ('Projeto escolhido para avaliação em IA')
    na aba 'Projeto Piloto Lotes' logo após ser baixado do OPEE.
    """
    codigo_alvo = normalizar_codigo_projeto(codigo_ou_pasta)
    if not codigo_alvo or not caminho_planilha.exists():
        return False

    wb = openpyxl.load_workbook(str(caminho_planilha))
    ws = wb[ABA_LOTES]
    atualizou = False

    for row_idx in range(1, ws.max_row + 1):
        cell = ws.cell(row=row_idx, column=1)
        codigo_celula = normalizar_codigo_projeto(cell.value)
        if codigo_celula == codigo_alvo:
            cor_atual = classificar_cor_celula(cell)
            # Só pinta de amarelo se estiver Branco (não sobrescreve Roxo, Azul ou Verde)
            if cor_atual == "BRANCO":
                cell.fill = FILL_AMARELO
                atualizou = True
            break

    if atualizou:
        _salvar_workbook_seguro(wb, caminho_planilha)
        print(f" [PLANILHA] '{codigo_alvo}' atualizado para AMARELO (Em avaliação pela IA).")
    wb.close()
    return atualizou


def pasta_possui_docx_e_json(pasta_projeto: Path) -> bool:
    """
    Verifica se dentro da pasta do projeto em 03_Concluidos existem
    pelo menos um arquivo .docx e um arquivo .json válidos (tamanho > 0).
    """
    if not pasta_projeto.is_dir():
        return False

    tem_docx = False
    tem_json = False

    for arq in pasta_projeto.iterdir():
        if not arq.is_file():
            continue
        try:
            if arq.stat().st_size <= 0:
                continue
        except OSError:
            continue

        ext = arq.suffix.lower()
        if ext == ".docx":
            tem_docx = True
        elif ext == ".json":
            tem_json = True

        if tem_docx and tem_json:
            return True

    return False


def sincronizar_concluidos_planilha(
    caminho_planilha: Path = CAMINHO_PLANILHA_PADRAO,
    dir_concluidos: Path = DIR_CONCLUIDOS,
) -> int:
    """
    Varre a pasta 03_Concluidos, faz o check se existe 1 arquivo .docx e 1 arquivo .json
    em cada pasta e pinta os projetos correspondentes de ROXO ('Projeto avaliado pela IA')
    na aba 'Projeto Piloto Lotes'.
    """
    if not caminho_planilha.exists() or not dir_concluidos.exists():
        return 0

    codigos_validados_roxo = set()
    for pasta in dir_concluidos.iterdir():
        if pasta.is_dir() and pasta_possui_docx_e_json(pasta):
            cod = normalizar_codigo_projeto(pasta.name)
            if cod:
                codigos_validados_roxo.add(cod)

    if not codigos_validados_roxo:
        return 0

    wb = openpyxl.load_workbook(str(caminho_planilha))
    if ABA_LOTES not in wb.sheetnames:
        wb.close()
        return 0

    ws = wb[ABA_LOTES]
    pintados_roxo = 0

    for row_idx in range(1, ws.max_row + 1):
        cell = ws.cell(row=row_idx, column=1)
        codigo_celula = normalizar_codigo_projeto(cell.value)
        if codigo_celula and codigo_celula in codigos_validados_roxo:
            cor_atual = classificar_cor_celula(cell)
            # Atualiza para ROXO se estava BRANCO ou AMARELO (preserva AZUL, VERDE e VERMELHO)
            if cor_atual in ("BRANCO", "AMARELO"):
                cell.fill = FILL_ROXO
                pintados_roxo += 1
                print(
                    f" [PLANILHA] '{codigo_celula}' validado em 03_Concluidos (.docx + .json OK) -> Pintado de ROXO!"
                )

    if pintados_roxo > 0:
        _salvar_workbook_seguro(wb, caminho_planilha)

    wb.close()
    return pintados_roxo