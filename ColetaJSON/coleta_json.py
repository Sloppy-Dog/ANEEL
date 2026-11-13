# -*- coding: utf-8 -*-
"""
ColetaJSON - Copia para a raiz da pasta principal os arquivos .json
encontrados em cada subpasta.

Uso:
    python coleta_json.py                      -> abre janela para escolher a pasta
    python coleta_json.py "C:\\caminho\\pasta"  -> usa a pasta informada
    python coleta_json.py PASTA --recursivo    -> procura também em sub-subpastas
    python coleta_json.py PASTA --simular      -> só mostra o que faria, sem copiar
    python coleta_json.py PASTA --sobrescrever -> substitui cópias já existentes

Regras:
  - Verifica cada subpasta e informa as que NÃO possuem .json.
  - Se dois arquivos tiverem o mesmo nome, a cópia recebe o nome da
    subpasta como prefixo (ex.: "Projeto_A__dados.json") para não perder dados.
  - Se a cópia já existir com conteúdo idêntico, é ignorada (pode rodar várias vezes).
"""
import argparse
import filecmp
import shutil
import sys
from pathlib import Path


def escolher_pasta() -> Path | None:
    """Abre uma janela para o usuário selecionar a pasta principal."""
    try:
        import tkinter as tk
        from tkinter import filedialog
    except ImportError:
        return None
    root = tk.Tk()
    root.withdraw()
    pasta = filedialog.askdirectory(title="Selecione a pasta principal")
    root.destroy()
    return Path(pasta) if pasta else None


def listar_jsons(subpasta: Path, recursivo: bool) -> list[Path]:
    padrao = "**/*" if recursivo else "*"
    return sorted(
        p for p in subpasta.glob(padrao)
        if p.is_file() and p.suffix.lower() == ".json"
    )


def nome_destino(raiz: Path, arq: Path, usados: set[str]) -> Path:
    """Define o nome da cópia na raiz, evitando colisão de nomes."""
    candidato = raiz / arq.name
    if arq.name.lower() not in usados and (
        not candidato.exists() or filecmp.cmp(arq, candidato, shallow=False)
    ):
        return candidato
    # Prefixa com o caminho relativo da subpasta (ex.: "Projeto_A__dados.json")
    prefixo = "__".join(arq.parent.relative_to(raiz).parts)
    return raiz / f"{prefixo}__{arq.name}"


def coletar(raiz: Path, recursivo=False, simular=False, sobrescrever=False) -> int:
    subpastas = sorted(p for p in raiz.iterdir() if p.is_dir() and not p.name.startswith("."))
    if not subpastas:
        print(f"Nenhuma subpasta encontrada em: {raiz}")
        return 0

    copiados, ignorados, sem_json = 0, 0, []
    usados: set[str] = set()

    print(f"Pasta principal: {raiz}")
    print(f"Subpastas encontradas: {len(subpastas)}\n")

    for sub in subpastas:
        jsons = listar_jsons(sub, recursivo)
        if not jsons:
            sem_json.append(sub.name)
            print(f"[SEM JSON] {sub.name}")
            continue

        for arq in jsons:
            destino = nome_destino(raiz, arq, usados)
            usados.add(destino.name.lower())
            origem_rel = arq.relative_to(raiz)

            if destino.exists() and not sobrescrever:
                if filecmp.cmp(arq, destino, shallow=False):
                    print(f"[JÁ EXISTE] {origem_rel} -> {destino.name}")
                    ignorados += 1
                    continue
                print(f"[CONFLITO] {destino.name} já existe com outro conteúdo; "
                      f"use --sobrescrever. ({origem_rel})")
                ignorados += 1
                continue

            if not simular:
                shutil.copy2(arq, destino)
            print(f"[{'SIMULADO' if simular else 'COPIADO'}] {origem_rel} -> {destino.name}")
            copiados += 1

    print("\n" + "=" * 50)
    print(f"Arquivos copiados : {copiados}{' (simulação)' if simular else ''}")
    print(f"Ignorados         : {ignorados}")
    print(f"Subpastas sem JSON: {len(sem_json)}")
    for nome in sem_json:
        print(f"   - {nome}")
    return copiados


def main():
    ap = argparse.ArgumentParser(description="Copia os .json das subpastas para a raiz da pasta principal.")
    ap.add_argument("pasta", nargs="?", help="Pasta principal (se omitida, abre janela de seleção)")
    ap.add_argument("--recursivo", action="store_true", help="Procura também em sub-subpastas")
    ap.add_argument("--simular", action="store_true", help="Mostra o que seria feito, sem copiar")
    ap.add_argument("--sobrescrever", action="store_true", help="Substitui arquivos já existentes na raiz")
    args = ap.parse_args()

    raiz = Path(args.pasta) if args.pasta else escolher_pasta()
    if raiz is None:
        print("Nenhuma pasta selecionada.")
        sys.exit(1)
    raiz = raiz.expanduser().resolve()
    if not raiz.is_dir():
        print(f"Pasta não encontrada: {raiz}")
        sys.exit(1)

    coletar(raiz, args.recursivo, args.simular, args.sobrescrever)

    if not args.pasta:  # executado com duplo clique: mantém a janela aberta
        input("\nPressione Enter para sair...")


if __name__ == "__main__":
    main()
