# -*- coding: utf-8 -*-
"""
GeraDocs Lote v1.0.0

Entrada:
- Um JSON consolidado com dados globais e blocos projeto_1, projeto_2, ...

Saidas:
- Uma Nota Tecnica consolidada, com duas tabelas dinamicas.
- Um Oficio consolidado, com {{ lista_codigos }}.
- Um Despacho individual para cada projeto.

Estrutura padrao:
    GeraDocs_Lote.py
    data/CONSOLIDADO.json
    templates/Modelo_NT.docx
    templates/Modelo_Oficio.docx
    templates/Modelo_Despacho.docx
    output/

Dependencias:
    pip install python-docx num2words
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
from docx.document import Document as _Document
from docx.table import Table, _Cell, _Row
from docx.text.paragraph import Paragraph
from docx.text.run import Run

try:
    from num2words import num2words
except ImportError:  # O programa continua, mas o extenso individual ficara vazio.
    num2words = None


VERSAO = "1.0.0"
BASE_DIR = Path(__file__).resolve().parent
DEFAULT_JSON = BASE_DIR / "data" / "CONSOLIDADO.json"
DEFAULT_TEMPLATES_DIR = BASE_DIR / "templates"
DEFAULT_OUTPUT_DIR = BASE_DIR / "output"

NT_TEMPLATE = "Modelo_NT.docx"
OFICIO_TEMPLATE = "Modelo_Oficio.docx"
DESPACHO_TEMPLATE = "Modelo_Despacho.docx"

PLACEHOLDER_PATTERN = re.compile(r"{{\s*(.*?)\s*}}")
PROJECT_BLOCK_PATTERN = re.compile(r"^projeto_(\d+)$", re.IGNORECASE)
TRAILING_INDEX_PATTERN = re.compile(r"_(\d+)$")
INVALID_FILENAME_CHARS = re.compile(r'[\\/*?:"<>|\r\n\t]+')

logging.basicConfig(level=logging.INFO, format="%(levelname)s: %(message)s")
LOGGER = logging.getLogger("GeraDocsLote")


# -----------------------------------------------------------------------------
# Normalizacao e validacao
# -----------------------------------------------------------------------------

def strip_accents(value: str) -> str:
    return "".join(
        char for char in unicodedata.normalize("NFD", value)
        if unicodedata.category(char) != "Mn"
    )


def normalize_key(key: Any) -> str:
    text = str(key).replace("{{", "").replace("}}", "")
    text = " ".join(text.strip().split()).lower()
    return strip_accents(text)


def normalize_label(value: Any) -> str:
    return strip_accents(" ".join(str(value or "").strip().lower().split()))


def display_text(value: Any) -> str:
    return "" if value is None else str(value)


def safe_filename(value: Any, fallback: str = "DOCUMENTO") -> str:
    text = INVALID_FILENAME_CHARS.sub("", display_text(value).strip())
    text = re.sub(r"\s+", "_", text)
    return text or fallback


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
    if not text:
        return None
    if "," in text and "." in text:
        text = text.replace(".", "").replace(",", ".") if text.rfind(",") > text.rfind(".") else text.replace(",", "")
    elif "," in text:
        text = text.replace(".", "").replace(",", ".")
    try:
        return Decimal(text)
    except InvalidOperation:
        return None


def valor_monetario_por_extenso(value: Any) -> str:
    """Converte valor monetario em portugues. Requer num2words."""
    decimal_value = parse_decimal_br(value)
    if decimal_value is None:
        return ""
    if num2words is None:
        LOGGER.warning("Biblioteca 'num2words' ausente; valor individual por extenso ficou vazio.")
        return ""

    decimal_value = decimal_value.quantize(Decimal("0.01"))
    reais = int(decimal_value)
    centavos = int((decimal_value - Decimal(reais)) * 100)
    partes: List[str] = []

    if reais:
        unidade = "real" if reais == 1 else "reais"
        partes.append(f"{num2words(reais, lang='pt_BR')} {unidade}")
    if centavos:
        unidade = "centavo" if centavos == 1 else "centavos"
        partes.append(f"{num2words(centavos, lang='pt_BR')} {unidade}")
    return " e ".join(partes) if partes else "zero reais"


def carregar_json(path: Path) -> Dict[str, Any]:
    if not path.exists():
        raise FileNotFoundError(f"JSON nao encontrado: {path}")
    with path.open("r", encoding="utf-8-sig") as file:
        data = json.load(file)
    if not isinstance(data, dict):
        raise ValueError("A raiz do JSON consolidado deve ser um objeto.")
    return data


def remover_sufixo_numerico(key: str, expected_index: Optional[int] = None) -> str:
    match = TRAILING_INDEX_PATTERN.search(key)
    if not match:
        return key
    if expected_index is not None and int(match.group(1)) != expected_index:
        return key
    return key[:match.start()]


def extrair_projetos(dados: Mapping[str, Any]) -> List[Dict[str, Any]]:
    """
    Converte projeto_1, projeto_2, ... em lista normalizada.

    Exemplo:
      projeto_2.codigo_projeto_2 -> projeto["codigo_projeto"]
    """
    encontrados: List[Tuple[int, str, Mapping[str, Any]]] = []
    for block_key, block_value in dados.items():
        match = PROJECT_BLOCK_PATTERN.fullmatch(str(block_key).strip())
        if not match:
            continue
        if not isinstance(block_value, Mapping):
            raise ValueError(f"O bloco {block_key!r} deve ser um objeto JSON.")
        encontrados.append((int(match.group(1)), str(block_key), block_value))

    encontrados.sort(key=lambda item: item[0])
    projetos: List[Dict[str, Any]] = []
    codigos_vistos = set()

    for indice, block_key, block in encontrados:
        projeto: Dict[str, Any] = {"indice": indice, "bloco_origem": block_key}
        for raw_key, value in block.items():
            normalized = remover_sufixo_numerico(str(raw_key), indice)
            if normalized in projeto and projeto[normalized] != value:
                raise ValueError(f"Colisao de campos em {block_key}: {normalized!r}.")
            projeto[normalized] = value

        codigo = display_text(projeto.get("codigo_projeto")).strip()
        if not codigo:
            raise ValueError(f"Campo codigo_projeto_{indice} ausente em {block_key}.")
        if codigo in codigos_vistos:
            raise ValueError(f"Codigo de projeto duplicado: {codigo}.")
        codigos_vistos.add(codigo)
        projetos.append(projeto)

    if not projetos:
        raise ValueError("Nenhum bloco projeto_N foi encontrado no JSON.")
    return projetos


def validar_dados_globais(dados: Mapping[str, Any]) -> List[str]:
    avisos = []
    for key in ("empresa_proponente", "sigla", "cnpj_proponente"):
        if not display_text(dados.get(key)).strip():
            avisos.append(f"Campo global vazio: {key}")
    return avisos


# -----------------------------------------------------------------------------
# DOCX: iteracao e substituicao robusta de placeholders
# -----------------------------------------------------------------------------

def iter_block_paragraphs(parent: Any) -> Iterable[Paragraph]:
    """Percorre paragrafos, inclusive em tabelas aninhadas."""
    for paragraph in getattr(parent, "paragraphs", []):
        yield paragraph
    for table in getattr(parent, "tables", []):
        for row in table.rows:
            for cell in row.cells:
                yield from iter_block_paragraphs(cell)


def iter_document_parts(doc: _Document) -> Iterable[Any]:
    yield doc
    seen = set()
    for section in doc.sections:
        for part in (section.header, section.first_page_header, section.even_page_header,
                     section.footer, section.first_page_footer, section.even_page_footer):
            marker = id(part._element)
            if marker not in seen:
                seen.add(marker)
                yield part


def replace_in_paragraph(paragraph: Paragraph, mapping: Mapping[str, Any]) -> bool:
    """Substitui placeholders mesmo quando fragmentados em varios runs."""
    full_text = "".join(run.text for run in paragraph.runs)
    if not full_text or "{{" not in full_text:
        return False

    normalized_mapping = {normalize_key(key): display_text(value) for key, value in mapping.items()}

    def replace_match(match: re.Match[str]) -> str:
        key = normalize_key(match.group(1))
        return normalized_mapping.get(key, match.group(0))

    new_text = PLACEHOLDER_PATTERN.sub(replace_match, full_text)
    if new_text == full_text:
        return False

    if paragraph.runs:
        paragraph.runs[0].text = new_text
        for run in paragraph.runs[1:]:
            run.text = ""
    else:
        paragraph.add_run(new_text)
    return True


def process_doc_content(doc: _Document, mapping: Mapping[str, Any]) -> None:
    for part in iter_document_parts(doc):
        for paragraph in iter_block_paragraphs(part):
            replace_in_paragraph(paragraph, mapping)


def unresolved_placeholders(doc: _Document) -> List[str]:
    found = set()
    for part in iter_document_parts(doc):
        for paragraph in iter_block_paragraphs(part):
            text = "".join(run.text for run in paragraph.runs) or paragraph.text
            for match in PLACEHOLDER_PATTERN.finditer(text):
                found.add(match.group(0))
    return sorted(found)


# -----------------------------------------------------------------------------
# DOCX: tabelas dinamicas da Nota Tecnica
# -----------------------------------------------------------------------------

def row_text(row: _Row) -> str:
    return " | ".join(cell.text for cell in row.cells)


def row_has_placeholder(row: _Row, placeholder: str) -> bool:
    target = normalize_key(placeholder)
    return any(target in normalize_key(match.group(1))
               for match in PLACEHOLDER_PATTERN.finditer(row_text(row)))


def clone_row_before(table: Table, source_row: _Row, target_row: _Row) -> _Row:
    """Clona source_row antes de target_row, preservando formatacao XML."""
    new_tr = copy.deepcopy(source_row._tr)
    target_row._tr.addprevious(new_tr)
    inserted_index = list(table._tbl.tr_lst).index(new_tr)
    return table.rows[inserted_index]


def remove_row_object(table: Table, row: _Row) -> None:
    table._tbl.remove(row._tr)


def replace_in_row(row: _Row, mapping: Mapping[str, Any]) -> None:
    for cell in row.cells:
        for paragraph in iter_block_paragraphs(cell):
            replace_in_paragraph(paragraph, mapping)


def find_dynamic_nt_tables(doc: _Document) -> Tuple[Table, Table]:
    analysis_table: Optional[Table] = None
    values_table: Optional[Table] = None

    for table in doc.tables:
        header = normalize_label(row_text(table.rows[0])) if table.rows else ""
        has_model = any(row_has_placeholder(row, "codigo_projeto") for row in table.rows)
        if not has_model:
            continue
        if all(term in header for term in ("tipologia", "energia", "demanda", "rcb")):
            analysis_table = table
        elif "reconhecido" in header and "glosado" in header:
            values_table = table

    if analysis_table is None:
        raise ValueError("Tabela dinamica de analise da NT nao encontrada.")
    if values_table is None:
        raise ValueError("Tabela dinamica de valores da NT nao encontrada.")
    return analysis_table, values_table


def find_model_row_index(table):
    """
    Retorna o índice da linha modelo.
    """

    for idx, row in enumerate(table.rows):

        if row_has_placeholder(
            row,
            "codigo_projeto"
        ):
            return idx

    raise ValueError(
        "Linha modelo não encontrada."
    )

def render_dynamic_table(
    table,
    projetos,
    fields
):

    model_index = find_model_row_index(table)

    model_row = table.rows[model_index]

    insert_position = model_index + 1

    for projeto in projetos:

        if insert_position < len(table.rows):

            target_row = table.rows[
                insert_position
            ]

            new_row = clone_row_before(
                table,
                model_row,
                target_row
            )

        else:

            new_tr = copy.deepcopy(
                model_row._tr
            )

            table._tbl.append(
                new_tr
            )

            new_row = table.rows[-1]

        mapping = {
            field: projeto.get(
                field,
                ""
            )
            for field in fields
        }

        replace_in_row(
            new_row,
            mapping
        )

        insert_position += 1

    remove_row_object(
        table,
        model_row
    )    


def render_nt_tables(doc: _Document, projetos: Sequence[Mapping[str, Any]]) -> None:
    analysis_table, values_table = find_dynamic_nt_tables(doc)
    render_dynamic_table(
        analysis_table,
        projetos,
        ("codigo_projeto", "tipologia", "EE", "DRP", "RCB", "recomenda"),
    )
    render_dynamic_table(
        values_table,
        projetos,
        ("codigo_projeto", "valor_reconhecido", "glosado"),
    )


# -----------------------------------------------------------------------------
# Mapeamentos e nomes de arquivo
# -----------------------------------------------------------------------------

def montar_lista_codigos(projetos: Sequence[Mapping[str, Any]]) -> str:
    return "; ".join(display_text(item.get("codigo_projeto")) for item in projetos)


def build_global_mapping(dados: Mapping[str, Any], projetos: Sequence[Mapping[str, Any]]) -> Dict[str, str]:
    mapping: Dict[str, str] = {
        key: display_text(value)
        for key, value in dados.items()
        if not PROJECT_BLOCK_PATTERN.fullmatch(str(key)) and not isinstance(value, (dict, list))
    }
    mapping.setdefault("data", "")
    mapping.setdefault("sei", "")
    mapping["lista_codigos"] = montar_lista_codigos(projetos)
    # Compatibilidade com eventual placeholder antigo fora das tabelas da NT.
    mapping["codigo_projeto_n"] = mapping["lista_codigos"]
    return mapping


def build_despacho_mapping(dados: Mapping[str, Any], projeto: Mapping[str, Any]) -> Dict[str, str]:
    mapping = build_global_mapping(dados, [projeto])
    mapping.update({key: display_text(value) for key, value in projeto.items()})
    mapping["valor_reconhecido_extenso"] = display_text(
        projeto.get("valor_reconhecido_extenso")
    ) or valor_monetario_por_extenso(projeto.get("valor_reconhecido"))
    mapping.setdefault("glosado", "R$ 0,00")
    return mapping


def nome_nt(sigla: str) -> str:
    return f"PE_Consolidado_Nota_Tecnica_0XXX_Avaliador_{safe_filename(sigla, 'SIGLA')}_48547.XXXXXXXXXXX-00-PEE.docx"


def nome_oficio(sigla: str) -> str:
    s = safe_filename(sigla, "SIGLA")
    return f"PE_Consolidado_Oficio_0XXX_{s}_Avaliador_{s}_48547.XXXXXXXXXXX2026-00-PEE.docx"


def nome_despacho(codigo_projeto: str, sigla: str) -> str:
    codigo = codigo_para_nome(codigo_projeto)
    s = safe_filename(sigla, "SIGLA")
    return f"{codigo}_Despacho_0XXX_Avaliador_{s}_48547.XXXXXXXXXXX2026-00-PEE.docx"


# -----------------------------------------------------------------------------
# Geracao dos documentos
# -----------------------------------------------------------------------------

def require_template(path: Path) -> None:
    if not path.exists():
        raise FileNotFoundError(f"Template nao encontrado: {path}")


def save_checked(doc: _Document, output_path: Path, document_name: str) -> None:
    pending = unresolved_placeholders(doc)
    if pending:
        LOGGER.warning("%s contem placeholders nao preenchidos: %s", document_name, ", ".join(pending))
    output_path.parent.mkdir(parents=True, exist_ok=True)
    doc.save(output_path)
    LOGGER.info("Criado: %s", output_path)


def gerar_nota_tecnica(
    dados: Mapping[str, Any],
    projetos: Sequence[Mapping[str, Any]],
    template_path: Path,
    output_dir: Path,
) -> Path:
    require_template(template_path)
    doc = Document(template_path)
    # Primeiro expande tabelas; so depois substitui os placeholders globais.
    render_nt_tables(doc, projetos)
    process_doc_content(doc, build_global_mapping(dados, projetos))
    output_path = output_dir / "Nota_Tecnica" / nome_nt(display_text(dados.get("sigla")))
    save_checked(doc, output_path, "Nota Tecnica")
    return output_path


def gerar_oficio(
    dados: Mapping[str, Any],
    projetos: Sequence[Mapping[str, Any]],
    template_path: Path,
    output_dir: Path,
) -> Path:
    require_template(template_path)
    doc = Document(template_path)
    process_doc_content(doc, build_global_mapping(dados, projetos))
    output_path = output_dir / "Oficio" / nome_oficio(display_text(dados.get("sigla")))
    save_checked(doc, output_path, "Oficio")
    return output_path


def gerar_despachos(
    dados: Mapping[str, Any],
    projetos: Sequence[Mapping[str, Any]],
    template_path: Path,
    output_dir: Path,
) -> List[Path]:
    require_template(template_path)
    paths: List[Path] = []
    sigla = display_text(dados.get("sigla"))

    for projeto in projetos:
        doc = Document(template_path)
        process_doc_content(doc, build_despacho_mapping(dados, projeto))
        output_path = output_dir / "Despachos" / nome_despacho(
            display_text(projeto.get("codigo_projeto")), sigla
        )
        save_checked(doc, output_path, f"Despacho {projeto.get('codigo_projeto', '')}")
        paths.append(output_path)
    return paths


def gerar_todos(
    json_path: Path,
    templates_dir: Path,
    output_dir: Path,
) -> Dict[str, Any]:
    dados = carregar_json(json_path)
    projetos = extrair_projetos(dados)

    for aviso in validar_dados_globais(dados):
        LOGGER.warning(aviso)

    LOGGER.info("JSON carregado: %s", json_path)
    LOGGER.info("Projetos identificados: %d", len(projetos))

    nt = gerar_nota_tecnica(dados, projetos, templates_dir / NT_TEMPLATE, output_dir)
    oficio = gerar_oficio(dados, projetos, templates_dir / OFICIO_TEMPLATE, output_dir)
    despachos = gerar_despachos(dados, projetos, templates_dir / DESPACHO_TEMPLATE, output_dir)

    return {"nota_tecnica": nt, "oficio": oficio, "despachos": despachos}


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=f"GeraDocs Lote v{VERSAO}")
    parser.add_argument("--json", type=Path, default=DEFAULT_JSON, help="JSON consolidado")
    parser.add_argument("--templates", type=Path, default=DEFAULT_TEMPLATES_DIR, help="Pasta dos templates")
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT_DIR, help="Pasta de saida")
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    try:
        result = gerar_todos(args.json, args.templates, args.output)
    except Exception as exc:
        LOGGER.exception("Falha na geracao: %s", exc)
        return 1

    LOGGER.info(
        "Concluido: 1 NT, 1 Oficio e %d Despacho(s).",
        len(result["despachos"]),
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
