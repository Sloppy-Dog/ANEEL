# -*- coding: utf-8 -*-
"""
Identifica_Glosa - Marca as pastas de projetos com recomendação de glosa.

Lê a pasta principal com as avaliações de projetos. Em cada subpasta, procura
o arquivo .json da avaliação e o campo "recomenda". Se o valor for
"Reprovado" ou "Aprovado parcialmente", a subpasta é renomeada com o sufixo
" - Glosado".

Uso:
    python identifica_glosa.py                     -> abre janela para escolher a pasta
    python identifica_glosa.py "C:\\caminho\\pasta" -> usa a pasta informada
    python identifica_glosa.py PASTA --simular     -> só mostra o que faria, sem renomear
    python identifica_glosa.py PASTA --recursivo   -> procura o .json também em sub-subpastas

Detalhes:
  - Aceita o campo "recomenda" ou numerado ("recomenda_1", "recomenda_2"...),
    em qualquer nível do JSON. Maiúsculas e acentos são ignorados.
  - Se a subpasta tiver mais de um projeto ou JSON, basta UM com glosa para
    a pasta ser marcada.
  - Pastas que já terminam com " - Glosado" não são renomeadas de novo, então
    o programa pode ser rodado várias vezes.
  - Ao final, gera o relatório "relatorio_glosas.csv" na pasta principal.
"""
from __future__ import annotations

import argparse
import csv
import json
import re
import sys
import unicodedata
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path

SUFIXO = " - Glosado"
NOME_RELATORIO = "relatorio_glosas.csv"
RE_CAMPO = re.compile(r"^recomenda(_\d+)?$")


# --------------------------------------------------------------------------- #
# Leitura dos JSONs
# --------------------------------------------------------------------------- #
def normalizar(texto: str) -> str:
    """Minúsculas, sem acentos e com espaços simples."""
    sem_acento = "".join(c for c in unicodedata.normalize("NFD", str(texto))
                         if unicodedata.category(c) != "Mn")
    return re.sub(r"\s+", " ", sem_acento).strip().lower()


def eh_glosa(valor: str) -> bool:
    v = normalizar(valor)
    return v.startswith("reprovad") or ("aprovad" in v and "parcial" in v)


def carregar_json(caminho: Path):
    for codificacao in ("utf-8-sig", "cp1252"):
        try:
            return json.loads(caminho.read_text(encoding=codificacao))
        except UnicodeDecodeError:
            continue
    raise ValueError("codificação de texto não reconhecida")


def achar_recomendacoes(dado) -> list[str]:
    """Devolve todos os valores dos campos recomenda / recomenda_N do JSON."""
    achados: list[str] = []
    if isinstance(dado, dict):
        for chave, valor in dado.items():
            if RE_CAMPO.match(normalizar(chave)) and isinstance(valor, (str, int, float)):
                achados.append(str(valor).strip())
            else:
                achados += achar_recomendacoes(valor)
    elif isinstance(dado, list):
        for item in dado:
            achados += achar_recomendacoes(item)
    return achados


# --------------------------------------------------------------------------- #
# Análise de cada subpasta
# --------------------------------------------------------------------------- #
@dataclass
class Resultado:
    pasta: Path
    jsons: list[str] = field(default_factory=list)
    recomendacoes: list[str] = field(default_factory=list)
    situacao: str = ""   # GLOSADO, SEM GLOSA, JÁ MARCADO, SEM JSON, SEM CAMPO, ERRO
    detalhe: str = ""
    novo_nome: str = ""


def analisar(sub: Path, recursivo: bool) -> Resultado:
    r = Resultado(pasta=sub)
    padrao = "**/*" if recursivo else "*"
    arquivos = sorted(p for p in sub.glob(padrao)
                      if p.is_file() and p.suffix.lower() == ".json")
    if not arquivos:
        r.situacao = "SEM JSON"
        return r

    erros = []
    for arq in arquivos:
        r.jsons.append(str(arq.relative_to(sub)))
        try:
            r.recomendacoes += achar_recomendacoes(carregar_json(arq))
        except Exception as e:
            erros.append(f"{arq.name}: {e}")

    if not r.recomendacoes:
        r.situacao = "ERRO" if erros else "SEM CAMPO"
        r.detalhe = "; ".join(erros) or "campo 'recomenda' não encontrado"
        return r
    if erros:
        r.detalhe = "Arquivos com erro: " + "; ".join(erros)

    glosado = any(eh_glosa(v) for v in r.recomendacoes)
    if not glosado:
        r.situacao = "SEM GLOSA"
    elif sub.name.endswith(SUFIXO):
        r.situacao = "JÁ MARCADO"
    else:
        r.situacao = "GLOSADO"
        r.novo_nome = sub.name + SUFIXO
    return r


# --------------------------------------------------------------------------- #
# Programa principal
# --------------------------------------------------------------------------- #
def processar(raiz: Path, recursivo=False, simular=False) -> list[Resultado]:
    subpastas = sorted(p for p in raiz.iterdir() if p.is_dir() and not p.name.startswith("."))
    print(f"Pasta principal: {raiz}")
    print(f"Subpastas encontradas: {len(subpastas)}{'  (SIMULAÇÃO: nada será renomeado)' if simular else ''}\n")

    resultados = []
    for sub in subpastas:
        r = analisar(sub, recursivo)
        rec = ", ".join(dict.fromkeys(r.recomendacoes)) or "-"

        if r.situacao == "GLOSADO":
            destino = sub.with_name(r.novo_nome)
            if destino.exists():
                r.situacao, r.detalhe = "ERRO", f"já existe uma pasta chamada '{r.novo_nome}'"
            elif not simular:
                try:
                    sub.rename(destino)
                except OSError as e:
                    r.situacao = "ERRO"
                    r.detalhe = f"não foi possível renomear ({e}). Algum arquivo da pasta está aberto?"

        acao = {"GLOSADO": f"-> renomeada para '{r.novo_nome}'" + (" (simulado)" if simular else ""),
                "JÁ MARCADO": "(já estava marcada)"}.get(r.situacao, "")
        print(f"[{r.situacao:^10}] {sub.name}  | recomenda: {rec} {acao}".rstrip())
        if r.detalhe:
            print(f"             {r.detalhe}")
        resultados.append(r)
    return resultados


def salvar_relatorio(raiz: Path, resultados: list[Resultado], simular: bool) -> Path | None:
    caminho = raiz / NOME_RELATORIO
    try:
        with open(caminho, "w", newline="", encoding="utf-8-sig") as f:  # abre certo no Excel
            w = csv.writer(f, delimiter=";")
            w.writerow(["Pasta original", "Pasta final", "Situação", "Recomendação",
                        "Arquivos JSON", "Observação", "Data"])
            agora = datetime.now().strftime("%d/%m/%Y %H:%M")
            for r in resultados:
                final = r.novo_nome if r.situacao == "GLOSADO" and not simular else r.pasta.name
                w.writerow([r.pasta.name, final,
                            r.situacao + (" (simulado)" if simular and r.situacao == "GLOSADO" else ""),
                            " | ".join(r.recomendacoes), " | ".join(r.jsons), r.detalhe, agora])
        return caminho
    except PermissionError:
        print(f"\n[AVISO] Não foi possível salvar {NOME_RELATORIO}. Ele está aberto no Excel?")
        return None


def escolher_pasta() -> Path | None:
    try:
        import tkinter as tk
        from tkinter import filedialog
    except ImportError:
        return None
    root = tk.Tk()
    root.withdraw()
    pasta = filedialog.askdirectory(title="Selecione a pasta com as avaliações dos projetos")
    root.destroy()
    return Path(pasta) if pasta else None


def main() -> None:
    ap = argparse.ArgumentParser(
        description="Renomeia com ' - Glosado' as subpastas cujo JSON recomenda "
                    "'Reprovado' ou 'Aprovado parcialmente'.")
    ap.add_argument("pasta", nargs="?", help="Pasta principal (se omitida, abre janela de seleção)")
    ap.add_argument("--simular", action="store_true", help="Mostra o que seria feito, sem renomear")
    ap.add_argument("--recursivo", action="store_true", help="Procura o .json também em sub-subpastas")
    args = ap.parse_args()

    raiz = Path(args.pasta) if args.pasta else escolher_pasta()
    if raiz is None:
        print("Nenhuma pasta selecionada.")
    elif not raiz.expanduser().is_dir():
        print(f"Pasta não encontrada: {raiz}")
    else:
        raiz = raiz.expanduser().resolve()
        resultados = processar(raiz, args.recursivo, args.simular)
        cont = {s: sum(r.situacao == s for r in resultados)
                for s in ("GLOSADO", "JÁ MARCADO", "SEM GLOSA", "SEM JSON", "SEM CAMPO", "ERRO")}
        print("\n" + "=" * 60)
        print(f"Glosadas agora{' (simulado)' if args.simular else ''}: {cont['GLOSADO']}")
        print(f"Já marcadas antes      : {cont['JÁ MARCADO']}")
        print(f"Sem glosa              : {cont['SEM GLOSA']}")
        print(f"Sem JSON               : {cont['SEM JSON']}")
        print(f"JSON sem 'recomenda'   : {cont['SEM CAMPO']}")
        print(f"Com erro               : {cont['ERRO']}")
        if rel := salvar_relatorio(raiz, resultados, args.simular):
            print(f"\nRelatório: {rel}")

    if not args.pasta:  # executado com duplo clique: mantém a janela aberta
        input("\nPressione Enter para sair...")
    sys.exit(0)


if __name__ == "__main__":
    main()
