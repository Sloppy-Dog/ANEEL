
# -*- coding: utf-8 -*-
import os
import re
import json
from typing import Dict, List, Any

from docx import Document
from docx.shared import Pt
from docx.text.paragraph import Paragraph
from docx.text.run import Run
import yaml

# ------------------------------------
# Caminhos (ajuste se preferir via args)
BASE_DIR = os.path.dirname(os.path.abspath(__file__))
DATA_JSON = os.path.join(BASE_DIR, "data", "dados_nt.json")
TEMPLATES_DIR = os.path.join(BASE_DIR, "templates")
OUTPUT_DIR = os.path.join(BASE_DIR, "output")
CONFIG_YML = os.path.join(BASE_DIR, "config", "modelos.yml")

# ------------------------------------
# Placeholders no formato {{ ... }}
PLACEHOLDER_PATTERN = re.compile(r"\{\{\s*(.*?)\s*\}\}")

def set_run_font_calibri(run: Run, size_pt: int = 12):
    """Força fonte Calibri (Corpo) 12 em um run, preservando negrito/itálico do modelo."""
    font = run.font
    font.name = "Calibri"
    font.size = Pt(size_pt)

def set_document_default_font(doc: Document, size_pt: int = 12):
    """Define estilo Normal para Calibri 12; não mexe em estilos específicos do template."""
    style = doc.styles['Normal']
    style.font.name = "Calibri"
    style.font.size = Pt(size_pt)

def normalize_key(k: str) -> str:
    """Normaliza chaves para aumentar a chance de casar com o placeholder do modelo."""
    repl = (
        ("<<", ""), (">>", ""), ("{{", ""), ("}}", ""),
    )
    for a, b in repl:
        k = k.replace(a, b)
    return " ".join(k.strip().split())  # remove espaços extras internos

def build_mapping(record: Dict[str, Any]) -> Dict[str, str]:
    """
    Cria um dicionário de mapeamento a partir de um objeto JSON.
    Gera variações de chaves (com espaço/underscore, com/sem acentos simples).
    """
    def strip_accents(s: str) -> str:
        import unicodedata
        return "".join(
            c for c in unicodedata.normalize("NFD", s)
            if unicodedata.category(c) != 'Mn'
        )

    mapping: Dict[str, str] = {}
    for key, val in record.items():
        if val is None:
            continue
        text = str(val)
        k0 = normalize_key(key)

        # variações
        variants = {k0,
                    k0.replace("_", " "),
                    k0.replace(" ", "_"),
                    strip_accents(k0),
                    strip_accents(k0).replace("_", " "),
                    strip_accents(k0).replace(" ", "_")}

        for v in variants:
            mapping[v] = text

    return mapping

def replace_in_paragraph(paragraph: Paragraph, mapping: Dict[str, str]) -> bool:
    """
    Substitui placeholders {{Campo}} no parágrafo, mesmo se quebrados em múltiplos runs.
    Retorna True se houve alteração.
    """
    full_text = "".join(run.text for run in paragraph.runs)
    if not PLACEHOLDER_PATTERN.search(full_text):
        return False

    def repl(match):
        raw_key = match.group(1)        # conteúdo interno
        key_norm = normalize_key(raw_key)
        return mapping.get(key_norm, match.group(0))

    new_text = PLACEHOLDER_PATTERN.sub(repl, full_text)
    if new_text == full_text:
        return False

    # Limpa e recria um único run com o texto substituído
    for _ in range(len(paragraph.runs)):
        paragraph.runs[0].clear()
        paragraph._element.remove(paragraph._element.r_lst[0])
    paragraph.add_run(new_text)
    return True

def replace_in_table(table, mapping: Dict[str, str]) -> int:
    changes = 0
    for row in table.rows:
        for cell in row.cells:
            for p in cell.paragraphs:
                if replace_in_paragraph(p, mapping):
                    changes += 1
    return changes

def replace_placeholders(doc: Document, mapping: Dict[str, str]) -> int:
    changes = 0
    for p in doc.paragraphs:
        if replace_in_paragraph(p, mapping):
            changes += 1
    for tbl in doc.tables:
        changes += replace_in_table(tbl, mapping)
    return changes

def carregar_config(caminho_yml: str) -> Dict[str, Any]:
    with open(caminho_yml, "r", encoding="utf-8") as f:
        return yaml.safe_load(f) or {}

def carregar_json(caminho_json: str) -> List[Dict[str, Any]]:
    with open(caminho_json, "r", encoding="utf-8") as f:
        data = json.load(f)
    # Accept single-object or list
    if isinstance(data, dict):
        return [data]
    if isinstance(data, list):
        return data
    raise ValueError("JSON deve ser objeto ou lista de objetos.")

def gerar_documento(template_path: str, output_path: str, mapping: Dict[str, str], force_calibri12: bool = True):
    doc = Document(template_path)
    if force_calibri12:
        set_document_default_font(doc, 12)

    _changes = replace_placeholders(doc, mapping)

    if force_calibri12:
        # Garante Calibri 12 nos runs resultantes (sem afetar negritos do modelo)
        for p in doc.paragraphs:
            for r in p.runs:
                set_run_font_calibri(r, 12)
        for tbl in doc.tables:
            for row in tbl.rows:
                for cell in row.cells:
                    for p in cell.paragraphs:
                        for r in p.runs:
                            set_run_font_calibri(r, 12)

    os.makedirs(os.path.dirname(output_path), exist_ok=True)
    doc.save(output_path)
    return _changes

def main():
    cfg = carregar_config(CONFIG_YML)
    modelos = cfg.get("modelos", [])
    opcoes = cfg.get("opcoes", {})
    force_calibri12 = bool(opcoes.get("forcar_calibri_12", True))
    chave_id = opcoes.get("chave_id_para_nome", "Codigo do projeto")

    registros = carregar_json(DATA_JSON)
    if not registros:
        print("[ERRO] Nenhum registro no JSON.")
        return

    for i, registro in enumerate(registros, start=1):
        mapping = build_mapping(registro)
        # Identificador para nome de arquivo
        id_doc = (
            str(registro.get(chave_id))
            or str(registro.get("Codigo do projeto"))
            or f"linha{i:03d}"
        )

        for modelo in modelos:
            template_file = modelo["arquivo_template"]
            template_path = os.path.join(TEMPLATES_DIR, template_file)
            if not os.path.exists(template_path):
                print(f"[AVISO] Template não encontrado: {template_path}")
                continue

            prefixo = modelo.get("prefixo_saida", modelo.get("nome", "DOC"))
            nome_saida = f"{prefixo}_{id_doc}.docx"
            output_path = os.path.join(OUTPUT_DIR, nome_saida)

            try:
                changes = gerar_documento(template_path, output_path, mapping, force_calibri12)
                print(f"[OK] {output_path} (substituições: {changes})")
            except Exception as e:
                print(f"[ERRO] Falha ao gerar '{output_path}': {e}")

if __name__ == "__main__":
    main()