# -*- coding: utf-8 -*-
"""GeraDocs Lote v1.1.0.

Mudancas da v1.1:
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

VERSAO = "1.1.0"
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
    """Localiza exatamente um arquivo CONSOLIDADO_*.json, sem diferenciar maiusculas."""
    if not data_dir.exists():
        raise FileNotFoundError(f"Pasta data nao encontrada: {data_dir}")
    arquivos = sorted(
        (p for p in data_dir.iterdir()
         if p.is_file() and p.suffix.lower() == ".json"
         and p.name.upper().startswith("CONSOLIDADO_")),
        key=lambda p: p.name.lower(),
    )
    if not arquivos:
        raise FileNotFoundError(
            f"Nenhum arquivo CONSOLIDADO_*.json encontrado em: {data_dir}"
        )
    if len(arquivos) > 1:
        nomes = "\n  - ".join(p.name for p in arquivos)
        raise ValueError(
            "Mais de um arquivo CONSOLIDADO_*.json foi encontrado. "
            "Mantenha apenas o consolidado que sera processado ou use --json."
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


def nome_nt(sigla: str) -> str:
    return f"PE_Consolidado_Nota_Tecnica_0XXX_Avaliador_{safe_filename(sigla, 'SIGLA')}_48547.XXXXXXXXXXX-00-PEE.docx"


def nome_oficio(sigla: str) -> str:
    s = safe_filename(sigla, "SIGLA")
    return f"PE_Consolidado_Oficio_0XXX_{s}_Avaliador_{s}_48547.XXXXXXXXXXX2026-00-PEE.docx"


def nome_despacho(codigo: str, sigla: str) -> str:
    return f"{codigo_para_nome(codigo)}_Despacho_0XXX_Avaliador_{safe_filename(sigla, 'SIGLA')}_48547.XXXXXXXXXXX2026-00-PEE.docx"


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
    LOGGER.info("Criado: %s", output_path)


def gerar_nota_tecnica(dados, projetos, template_path: Path, output_dir: Path) -> Path:
    require_template(template_path)
    doc = Document(template_path)
    render_nt_tables(doc, projetos)
    process_doc_content(doc, build_global_mapping(dados, projetos))
    path = output_dir / "Nota_Tecnica" / nome_nt(display_text(dados.get("sigla")))
    save_checked(doc, path, "Nota Tecnica")
    return path


def gerar_oficio(dados, projetos, template_path: Path, output_dir: Path) -> Path:
    require_template(template_path)
    doc = Document(template_path)
    process_doc_content(doc, build_global_mapping(dados, projetos))
    path = output_dir / "Oficio" / nome_oficio(display_text(dados.get("sigla")))
    save_checked(doc, path, "Oficio")
    return path


def gerar_despachos(dados, projetos, template_path: Path, output_dir: Path) -> List[Path]:
    require_template(template_path)
    paths = []
    sigla = display_text(dados.get("sigla"))
    for projeto in projetos:
        doc = Document(template_path)
        process_doc_content(doc, build_despacho_mapping(dados, projeto))
        remover_negrito_segunda_pagina_despacho(doc)
        path = output_dir / "Despachos" / nome_despacho(
            display_text(projeto.get("codigo_projeto")), sigla
        )
        save_checked(doc, path, f"Despacho {projeto.get('codigo_projeto', '')}")
        paths.append(path)
    return paths


def gerar_todos(json_path: Path, templates_dir: Path, output_dir: Path) -> Dict[str, Any]:
    dados = carregar_json(json_path)
    projetos = extrair_projetos(dados)
    LOGGER.info("JSON carregado: %s", json_path)
    LOGGER.info("Projetos identificados: %d", len(projetos))
    nt = gerar_nota_tecnica(dados, projetos, templates_dir / NT_TEMPLATE, output_dir)
    oficio = gerar_oficio(dados, projetos, templates_dir / OFICIO_TEMPLATE, output_dir)
    despachos = gerar_despachos(dados, projetos, templates_dir / DESPACHO_TEMPLATE, output_dir)
    return {"nota_tecnica": nt, "oficio": oficio, "despachos": despachos}


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=f"GeraDocs Lote v{VERSAO}")
    parser.add_argument(
        "--json", type=Path, default=None,
        help="JSON especifico. Se omitido, procura data/CONSOLIDADO_*.json."
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
    LOGGER.info("Concluido: 1 NT, 1 Oficio e %d Despacho(s).", len(result["despachos"]))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
