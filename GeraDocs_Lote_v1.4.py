# -*- coding: utf-8 -*-
"""GeraDocs Lote v1.4.

Mudancas da v1.4:
1. O termo "Avaliador" nos nomes dos arquivos e substituido pelo nome do
   avaliador (ex.: Sheyla ou Felipe). O nome e lido do campo "avaliador" do
   JSON, se existir; caso contrario, e identificado no nome do arquivo JSON.
2. O nome do Oficio nao repete mais a sigla da empresa.

Historico da v1.3:
1. Alteração na formação dos nomes dos arquivos, por nomes menores e mais simples.
    Linhas 435, 439 e 444.

Historico da v1.2.2:
1. Gera um unico Despacho, sem repetir o documento por projeto.
2. Preenche {{ lista_codigos }} com todos os codigos do lote.
3. Expande a tabela do Despacho com uma linha por projeto.
4. Mantem uma unica linha de total ao final da tabela.

Historico da v1.2.1:
1. Aceita qualquer nome de arquivo JSON na pasta data.
2. Corrige a montagem do Despacho consolidado usando o primeiro projeto como documento-base.
3. Valida a existencia e a integridade basica dos arquivos DOCX gravados.

Historico da v1.2:
1. Aceita de 1 a 100 projetos por lote.
2. Gera uma unica Nota Tecnica, um unico Oficio e um unico Despacho consolidado.
3. Salva os tres documentos diretamente em uma unica pasta de output.
4. No Despacho consolidado, replica o modelo completo para cada projeto, com quebra de pagina entre eles.

Historico da v1.1:
1. Descobre automaticamente um arquivo data/CONSOLIDADO_*.json.
2. Mantem {{ sei }} e {{ data }} intactos nos templates.
3. Remove o negrito de todo o conteudo da segunda pagina do Despacho,
   a partir da segunda ocorrencia do cabecalho da ANEEL.

Dependencias: python-docx e num2words.
"""
from __future__ import annotations

import argparse
import copy
import json
import logging
import re
import unicodedata
from decimal import Decimal, InvalidOperation
from pathlib import Path
from typing import Any, Dict, Iterable, List, Mapping, Optional, Sequence, Tuple

from docx import Document
from docx.document import Document as DocxDocument
from docx.table import Table, _Row
from docx.text.paragraph import Paragraph

try:
    from num2words import num2words
except ImportError:
    num2words = None

VERSAO = "1.4"
MAX_PROJETOS = 100
BASE_DIR = Path(__file__).resolve().parent
DEFAULT_DATA_DIR = BASE_DIR / "data"
DEFAULT_TEMPLATES_DIR = BASE_DIR / "templates"
DEFAULT_OUTPUT_DIR = BASE_DIR / "output"
NT_TEMPLATE = "Modelo_NT.docx"
OFICIO_TEMPLATE = "Modelo_Oficio.docx"
DESPACHO_TEMPLATE = "Modelo_Despacho.docx"

PLACEHOLDER_PATTERN = re.compile(r"{{\s*(.*?)\s*}}")
PROJECT_BLOCK_PATTERN = re.compile(r"^projeto_(\d+)$", re.IGNORECASE)
TRAILING_INDEX_PATTERN = re.compile(r"_(\d+)$")
INVALID_FILENAME_CHARS = re.compile(r'[\\/*?:"<>|\r\n\t]+')
PLACEHOLDERS_PRESERVADOS = {"sei", "data"}
DESPACHO_PAGE_HEADER = "AGENCIA NACIONAL DE ENERGIA ELETRICA"
AVALIADORES_CONHECIDOS = ("Sheyla", "Felipe")
AVALIADOR_PADRAO = "Avaliador"

logging.basicConfig(level=logging.INFO, format="%(levelname)s: %(message)s")
LOGGER = logging.getLogger("GeraDocsLote")


def strip_accents(value: str) -> str:
    return "".join(c for c in unicodedata.normalize("NFD", value)
                   if unicodedata.category(c) != "Mn")


def normalize_key(key: Any) -> str:
    text = str(key).replace("{{", "").replace("}}", "")
    return strip_accents(" ".join(text.strip().split()).lower())


def normalize_label(value: Any) -> str:
    return strip_accents(" ".join(str(value or "").strip().lower().split()))


def display_text(value: Any) -> str:
    return "" if value is None else str(value)


def safe_filename(value: Any, fallback: str = "DOCUMENTO") -> str:
    text = INVALID_FILENAME_CHARS.sub("", display_text(value).strip())
    return re.sub(r"\s+", "_", text) or fallback


def codigo_para_nome(codigo: Any) -> str:
    return safe_filename(display_text(codigo).replace("/", "-"), "SEM_CODIGO")


def parse_decimal_br(value: Any) -> Optional[Decimal]:
    if value is None or value == "":
        return None
    if isinstance(value, Decimal):
        return value
    if isinstance(value, (int, float)):
        return Decimal(str(value))
    text = str(value).strip().replace("R$", "").replace(" ", "")
    if "," in text and "." in text:
        text = text.replace(".", "").replace(",", ".") if text.rfind(",") > text.rfind(".") else text.replace(",", "")
    elif "," in text:
        text = text.replace(".", "").replace(",", ".")
    try:
        return Decimal(text)
    except InvalidOperation:
        return None


def valor_monetario_por_extenso(value: Any) -> str:
    numero = parse_decimal_br(value)
    if numero is None:
        return ""
    if num2words is None:
        LOGGER.warning("Biblioteca num2words ausente; valor por extenso ficou vazio.")
        return ""
    numero = numero.quantize(Decimal("0.01"))
    reais = int(numero)
    centavos = int((numero - Decimal(reais)) * 100)
    partes: List[str] = []
    if reais:
        partes.append(f"{num2words(reais, lang='pt_BR')} {'real' if reais == 1 else 'reais'}")
    if centavos:
        partes.append(f"{num2words(centavos, lang='pt_BR')} {'centavo' if centavos == 1 else 'centavos'}")
    return " e ".join(partes) if partes else "zero reais"


def localizar_json_consolidado(data_dir: Path) -> Path:
    """Localiza exatamente um arquivo JSON, independentemente do nome."""
    if not data_dir.exists():
        raise FileNotFoundError(f"Pasta data nao encontrada: {data_dir}")
    if not data_dir.is_dir():
        raise NotADirectoryError(f"O caminho data nao e uma pasta: {data_dir}")

    arquivos = sorted(
        (p for p in data_dir.iterdir()
         if p.is_file() and p.suffix.lower() == ".json"),
        key=lambda p: p.name.lower(),
    )
    if not arquivos:
        raise FileNotFoundError(
            f"Nenhum arquivo JSON encontrado em: {data_dir}"
        )
    if len(arquivos) > 1:
        nomes = "\n  - ".join(p.name for p in arquivos)
        raise ValueError(
            "Mais de um arquivo JSON foi encontrado na pasta data. "
            "Mantenha apenas o arquivo que sera processado ou informe o caminho com --json."
            f"\n  - {nomes}"
        )
    return arquivos[0]

def carregar_json(path: Path) -> Dict[str, Any]:
    if not path.exists():
        raise FileNotFoundError(f"JSON nao encontrado: {path}")
    with path.open("r", encoding="utf-8-sig") as file:
        data = json.load(file)
    if not isinstance(data, dict):
        raise ValueError("A raiz do JSON consolidado deve ser um objeto.")
    return data


def remover_sufixo_numerico(key: str, expected_index: int) -> str:
    match = TRAILING_INDEX_PATTERN.search(key)
    if match and int(match.group(1)) == expected_index:
        return key[:match.start()]
    return key


def extrair_projetos(dados: Mapping[str, Any]) -> List[Dict[str, Any]]:
    encontrados: List[Tuple[int, str, Mapping[str, Any]]] = []
    for block_key, block_value in dados.items():
        match = PROJECT_BLOCK_PATTERN.fullmatch(str(block_key).strip())
        if not match:
            continue
        if not isinstance(block_value, Mapping):
            raise ValueError(f"O bloco {block_key!r} deve ser um objeto JSON.")
        encontrados.append((int(match.group(1)), str(block_key), block_value))
    encontrados.sort(key=lambda x: x[0])

    projetos: List[Dict[str, Any]] = []
    codigos = set()
    for indice, block_key, block in encontrados:
        projeto: Dict[str, Any] = {"indice": indice, "bloco_origem": block_key}
        for raw_key, value in block.items():
            key = remover_sufixo_numerico(str(raw_key), indice)
            if key in projeto and projeto[key] != value:
                raise ValueError(f"Colisao de campos em {block_key}: {key!r}.")
            projeto[key] = value
        codigo = display_text(projeto.get("codigo_projeto")).strip()
        if not codigo:
            raise ValueError(f"Campo codigo_projeto_{indice} ausente em {block_key}.")
        if codigo in codigos:
            raise ValueError(f"Codigo de projeto duplicado: {codigo}.")
        codigos.add(codigo)
        projetos.append(projeto)
    if not projetos:
        raise ValueError("Nenhum bloco projeto_N foi encontrado no JSON.")
    if len(projetos) > MAX_PROJETOS:
        raise ValueError(
            f"O lote contem {len(projetos)} projetos; o limite e {MAX_PROJETOS}."
        )
    return projetos


def iter_paragraphs(parent: Any) -> Iterable[Paragraph]:
    for paragraph in getattr(parent, "paragraphs", []):
        yield paragraph
    for table in getattr(parent, "tables", []):
        for row in table.rows:
            for cell in row.cells:
                yield from iter_paragraphs(cell)


def iter_document_parts(doc: DocxDocument) -> Iterable[Any]:
    yield doc
    seen = set()
    for section in doc.sections:
        parts = (section.header, section.first_page_header, section.even_page_header,
                 section.footer, section.first_page_footer, section.even_page_footer)
        for part in parts:
            marker = id(part._element)
            if marker not in seen:
                seen.add(marker)
                yield part


def replace_in_paragraph(paragraph: Paragraph, mapping: Mapping[str, Any]) -> bool:
    full_text = "".join(run.text for run in paragraph.runs)
    if "{{" not in full_text:
        return False
    normalized = {normalize_key(k): display_text(v) for k, v in mapping.items()}

    def replacement(match: re.Match[str]) -> str:
        key = normalize_key(match.group(1))
        return normalized.get(key, match.group(0))

    new_text = PLACEHOLDER_PATTERN.sub(replacement, full_text)
    if new_text == full_text:
        return False
    if paragraph.runs:
        paragraph.runs[0].text = new_text
        for run in paragraph.runs[1:]:
            run.text = ""
    else:
        paragraph.add_run(new_text)
    return True


def process_doc_content(doc: DocxDocument, mapping: Mapping[str, Any]) -> None:
    for part in iter_document_parts(doc):
        for paragraph in iter_paragraphs(part):
            replace_in_paragraph(paragraph, mapping)


def unresolved_placeholders(doc: DocxDocument) -> List[str]:
    found = set()
    for part in iter_document_parts(doc):
        for paragraph in iter_paragraphs(part):
            text = "".join(run.text for run in paragraph.runs) or paragraph.text
            for match in PLACEHOLDER_PATTERN.finditer(text):
                if normalize_key(match.group(1)) not in PLACEHOLDERS_PRESERVADOS:
                    found.add(match.group(0))
    return sorted(found)


def row_text(row: _Row) -> str:
    return " | ".join(cell.text for cell in row.cells)


def row_has_placeholder(row: _Row, placeholder: str) -> bool:
    target = normalize_key(placeholder)
    return any(normalize_key(m.group(1)) == target
               for m in PLACEHOLDER_PATTERN.finditer(row_text(row)))


def clone_row_before(table: Table, source_row: _Row, target_row: _Row) -> _Row:
    new_tr = copy.deepcopy(source_row._tr)
    target_row._tr.addprevious(new_tr)
    index = list(table._tbl.tr_lst).index(new_tr)
    return table.rows[index]


def remove_row_object(table: Table, row: _Row) -> None:
    table._tbl.remove(row._tr)


def replace_in_row(row: _Row, mapping: Mapping[str, Any]) -> None:
    for cell in row.cells:
        for paragraph in iter_paragraphs(cell):
            replace_in_paragraph(paragraph, mapping)


def find_dynamic_nt_tables(doc: DocxDocument) -> Tuple[Table, Table]:
    analysis = None
    values = None
    for table in doc.tables:
        if not table.rows:
            continue
        header = normalize_label(row_text(table.rows[0]))
        if not any(row_has_placeholder(row, "codigo_projeto") for row in table.rows):
            continue
        if all(term in header for term in ("tipologia", "energia", "demanda", "rcb")):
            analysis = table
        elif "reconhecido" in header and "glosado" in header:
            values = table
    if analysis is None:
        raise ValueError("Tabela dinamica de analise da NT nao encontrada.")
    if values is None:
        raise ValueError("Tabela dinamica de valores da NT nao encontrada.")
    return analysis, values


def find_model_row_index(table: Table) -> int:
    matches = [idx for idx, row in enumerate(table.rows)
               if row_has_placeholder(row, "codigo_projeto")]
    if len(matches) != 1:
        raise ValueError(f"Esperada 1 linha-modelo; encontradas {len(matches)}.")
    return matches[0]


def render_dynamic_table(table: Table, projetos: Sequence[Mapping[str, Any]], fields: Sequence[str]) -> None:
    model_index = find_model_row_index(table)
    model_row = table.rows[model_index]
    target_row = table.rows[model_index + 1] if model_index + 1 < len(table.rows) else None
    for projeto in projetos:
        if target_row is not None:
            new_row = clone_row_before(table, model_row, target_row)
        else:
            new_tr = copy.deepcopy(model_row._tr)
            table._tbl.append(new_tr)
            new_row = table.rows[-1]
        replace_in_row(new_row, {field: projeto.get(field, "") for field in fields})
    remove_row_object(table, model_row)


def render_nt_tables(doc: DocxDocument, projetos: Sequence[Mapping[str, Any]]) -> None:
    analysis, values = find_dynamic_nt_tables(doc)
    render_dynamic_table(analysis, projetos,
                         ("codigo_projeto", "tipologia", "EE", "DRP", "RCB", "recomenda"))
    render_dynamic_table(values, projetos,
                         ("codigo_projeto", "valor_reconhecido", "glosado"))

def find_dynamic_despacho_table(doc: DocxDocument) -> Table:
    """Localiza a tabela de valores do Despacho pela linha-modelo e cabecalho."""
    matches: List[Table] = []
    for table in doc.tables:
        if not table.rows:
            continue
        header = normalize_label(row_text(table.rows[0]))
        has_model = any(row_has_placeholder(row, "codigo_projeto")
                        for row in table.rows)
        if (has_model and "codigo do projeto" in header
                and "valor reconhecido" in header
                and "valor glosado" in header):
            matches.append(table)
    if len(matches) != 1:
        raise ValueError(
            "Esperada 1 tabela dinamica no Despacho; "
            f"encontradas {len(matches)}."
        )
    return matches[0]


def render_despacho_table(doc: DocxDocument,
                           projetos: Sequence[Mapping[str, Any]]) -> None:
    table = find_dynamic_despacho_table(doc)
    render_dynamic_table(
        table, projetos,
        ("codigo_projeto", "valor_reconhecido", "glosado")
    )


def montar_lista_codigos(projetos: Sequence[Mapping[str, Any]]) -> str:
    return "; ".join(display_text(p.get("codigo_projeto")) for p in projetos)


def build_global_mapping(dados: Mapping[str, Any], projetos: Sequence[Mapping[str, Any]]) -> Dict[str, str]:
    mapping = {
        str(key): display_text(value)
        for key, value in dados.items()
        if not PROJECT_BLOCK_PATTERN.fullmatch(str(key))
        and not isinstance(value, (dict, list))
        and normalize_key(key) not in PLACEHOLDERS_PRESERVADOS
    }
    mapping["lista_codigos"] = montar_lista_codigos(projetos)
    mapping["codigo_projeto_n"] = mapping["lista_codigos"]
    return mapping


def build_despacho_mapping(dados: Mapping[str, Any], projeto: Mapping[str, Any]) -> Dict[str, str]:
    mapping = build_global_mapping(dados, [projeto])
    mapping.update({str(k): display_text(v) for k, v in projeto.items()
                    if normalize_key(k) not in PLACEHOLDERS_PRESERVADOS})
    mapping["valor_reconhecido_extenso"] = (
        display_text(projeto.get("valor_reconhecido_extenso"))
        or valor_monetario_por_extenso(projeto.get("valor_reconhecido"))
    )
    mapping.setdefault("glosado", "R$ 0,00")
    mapping.pop("sei", None)
    mapping.pop("data", None)
    return mapping


def remover_negrito_segunda_pagina_despacho(doc: DocxDocument) -> None:
    """Remove negrito do corpo a partir da segunda repeticao do cabecalho da ANEEL."""
    ocorrencias = 0
    segunda_pagina = False
    for paragraph in doc.paragraphs:
        texto = normalize_label(paragraph.text).upper()
        if DESPACHO_PAGE_HEADER in texto:
            ocorrencias += 1
            if ocorrencias == 2:
                segunda_pagina = True
        if segunda_pagina:
            for run in paragraph.runs:
                run.bold = False
    if ocorrencias < 2:
        LOGGER.warning(
            "Nao foi possivel identificar a segunda pagina do Despacho pelo cabecalho; "
            "nenhuma alteracao global de negrito foi aplicada."
        )


def identificar_avaliador(dados: Mapping[str, Any], json_path: Path) -> str:
    """Obtem o nome do avaliador para compor os nomes dos arquivos.

    Ordem de busca:
    1. Campo "avaliador" na raiz do JSON (qualquer nome e aceito).
    2. Nome de um avaliador conhecido (Sheyla, Felipe) no nome do arquivo JSON,
       sem diferenciar maiusculas/minusculas nem acentos.
    Se nada for encontrado, usa "Avaliador" e registra um aviso.
    """
    for key, value in dados.items():
        if normalize_key(key) == "avaliador" and display_text(value).strip():
            return display_text(value).strip()

    nome_arquivo = normalize_label(json_path.stem)
    encontrados = [nome for nome in AVALIADORES_CONHECIDOS
                   if re.search(rf"(?<![a-z]){normalize_label(nome)}(?![a-z])", nome_arquivo)]
    if len(encontrados) == 1:
        return encontrados[0]
    if len(encontrados) > 1:
        LOGGER.warning("Mais de um avaliador no nome do JSON (%s); usando %r.",
                       ", ".join(encontrados), AVALIADOR_PADRAO)
    else:
        LOGGER.warning("Avaliador nao identificado no JSON nem no nome do arquivo; "
                       "usando %r.", AVALIADOR_PADRAO)
    return AVALIADOR_PADRAO


def nome_nt(sigla: str, avaliador: str) -> str:
    a = safe_filename(avaliador, AVALIADOR_PADRAO)
    return f"PE_Nota_Tecnica_0XXX_{a}_{safe_filename(sigla, 'SIGLA')}_48547.XXXXXXXX-00-PEE.docx"


def nome_oficio(sigla: str, avaliador: str) -> str:
    a = safe_filename(avaliador, AVALIADOR_PADRAO)
    return f"PE_Oficio_0XXX_{a}_{safe_filename(sigla, 'SIGLA')}_48547.XXXXXXXX2026-00-PEE.docx"


def nome_despacho(sigla: str, avaliador: str) -> str:
    a = safe_filename(avaliador, AVALIADOR_PADRAO)
    return f"PE_Despacho_0XXX_{a}_{safe_filename(sigla, 'SIGLA')}_48547.XXXXXXXX2026-00-PEE.docx"


def require_template(path: Path) -> None:
    if not path.exists():
        raise FileNotFoundError(f"Template nao encontrado: {path}")


def save_checked(doc: DocxDocument, output_path: Path, document_name: str) -> None:
    pending = unresolved_placeholders(doc)
    if pending:
        LOGGER.warning("%s contem placeholders nao preenchidos: %s",
                       document_name, ", ".join(pending))
    output_path.parent.mkdir(parents=True, exist_ok=True)
    doc.save(output_path)
    if not output_path.exists() or output_path.stat().st_size == 0:
        raise IOError(f"O arquivo nao foi gravado corretamente: {output_path}")
    # Reabre o DOCX para confirmar que o pacote gerado e legivel pelo python-docx.
    Document(output_path)
    LOGGER.info("Criado: %s", output_path)


def gerar_nota_tecnica(dados, projetos, template_path: Path, output_dir: Path,
                       avaliador: str = AVALIADOR_PADRAO) -> Path:
    require_template(template_path)
    doc = Document(template_path)
    render_nt_tables(doc, projetos)
    process_doc_content(doc, build_global_mapping(dados, projetos))
    path = output_dir / nome_nt(display_text(dados.get("sigla")), avaliador)
    save_checked(doc, path, "Nota Tecnica")
    return path


def gerar_oficio(dados, projetos, template_path: Path, output_dir: Path,
                 avaliador: str = AVALIADOR_PADRAO) -> Path:
    require_template(template_path)
    doc = Document(template_path)
    process_doc_content(doc, build_global_mapping(dados, projetos))
    path = output_dir / nome_oficio(display_text(dados.get("sigla")), avaliador)
    save_checked(doc, path, "Oficio")
    return path


def gerar_despacho(dados, projetos, template_path: Path, output_dir: Path,
                   avaliador: str = AVALIADOR_PADRAO) -> Path:
    """Gera um unico Despacho com uma linha de tabela para cada projeto."""
    require_template(template_path)
    if not projetos:
        raise ValueError("Nao ha projetos para gerar o Despacho.")

    doc = Document(template_path)

    # Replica somente a linha-modelo da tabela, uma vez para cada projeto.
    render_despacho_table(doc, projetos)

    # Preenche lista_codigos, totais e demais campos globais em todo o documento,
    # inclusive nas linhas clonadas da tabela.
    mapping = build_global_mapping(dados, projetos)
    mapping.setdefault("glosado", display_text(dados.get("glosado_total")) or "R$ 0,00")
    process_doc_content(doc, mapping)

    remover_negrito_segunda_pagina_despacho(doc)
    path = output_dir / nome_despacho(display_text(dados.get("sigla")), avaliador)
    save_checked(doc, path, "Despacho consolidado")
    return path

def gerar_todos(json_path: Path, templates_dir: Path, output_dir: Path) -> Dict[str, Any]:
    dados = carregar_json(json_path)
    projetos = extrair_projetos(dados)
    LOGGER.info("JSON carregado: %s", json_path)
    LOGGER.info("Projetos identificados: %d", len(projetos))
    avaliador = identificar_avaliador(dados, json_path)
    LOGGER.info("Avaliador: %s", avaliador)
    nt = gerar_nota_tecnica(dados, projetos, templates_dir / NT_TEMPLATE, output_dir, avaliador)
    oficio = gerar_oficio(dados, projetos, templates_dir / OFICIO_TEMPLATE, output_dir, avaliador)
    despacho = gerar_despacho(dados, projetos, templates_dir / DESPACHO_TEMPLATE, output_dir, avaliador)
    return {"nota_tecnica": nt, "oficio": oficio, "despacho": despacho}


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=f"GeraDocs Lote v{VERSAO}")
    parser.add_argument(
        "--json", type=Path, default=None,
        help="JSON especifico. Se omitido, usa o unico arquivo .json existente em data/."
    )
    parser.add_argument("--templates", type=Path, default=DEFAULT_TEMPLATES_DIR)
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT_DIR)
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    try:
        json_path = args.json if args.json is not None else localizar_json_consolidado(DEFAULT_DATA_DIR)
        result = gerar_todos(json_path, args.templates, args.output)
    except Exception as exc:
        LOGGER.exception("Falha na geracao: %s", exc)
        return 1
    LOGGER.info("Concluido: 1 NT, 1 Oficio e 1 Despacho consolidado.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
