"""Histórico persistente de projetos concluídos (apenas biblioteca padrão)."""

import os
import re
import tempfile
from pathlib import Path


def chave_projeto(texto: str) -> str:
    """Ignora status, sigla da empresa e separador usado no nome da pasta."""
    match = re.search(r"\b((?:PE|PD|PG)-\d+-\d+)[/_-](\d{4})\b", texto, re.I)
    if not match:
        raise ValueError(f"Código de projeto não reconhecido: {texto!r}")
    return f"{match.group(1).upper()}/{match.group(2)}"


class HistoricoDownloads:
    """Carrega o TXT para um set e persiste cada inclusão atomicamente.

    Execute somente uma instância do scraper por vez para este histórico.
    Falhas na leitura ou gravação interrompem a execução, evitando ignorar
    silenciosamente o controle de duplicidade.
    """

    def __init__(self, caminho: Path):
        self.caminho = Path(caminho)
        self.projetos = set()
        if self.caminho.exists():
            for numero, linha in enumerate(self.caminho.read_text(encoding="utf-8-sig").splitlines(), 1):
                if linha.strip() and not linha.lstrip().startswith("#"):
                    try:
                        self.projetos.add(chave_projeto(linha.strip()))
                    except ValueError as erro:
                        raise ValueError(f"Histórico inválido na linha {numero}: {erro}") from erro

    def contem(self, nome: str) -> bool:
        return chave_projeto(nome) in self.projetos

    def registrar(self, nome: str):
        self.registrar_varios([nome])

    def registrar_varios(self, nomes):
        atualizados = self.projetos | {chave_projeto(nome) for nome in nomes}
        if atualizados == self.projetos:
            return
        self.caminho.parent.mkdir(parents=True, exist_ok=True)
        temporario = None
        try:
            with tempfile.NamedTemporaryFile(mode="w", encoding="utf-8", newline="\n",
                                             dir=self.caminho.parent, delete=False) as arquivo:
                temporario = Path(arquivo.name)
                arquivo.write("\n".join(sorted(atualizados)) + "\n")
                arquivo.flush()
                os.fsync(arquivo.fileno())
            os.replace(temporario, self.caminho)
            self.projetos = atualizados
        finally:
            if temporario is not None:
                temporario.unlink(missing_ok=True)

    def importar_pastas(self, baixados: Path, em_analise: Path,
                       concluidos: Path, sentinela: str) -> int:
        """Migra o controle anterior; pastas incompletas ficam disponíveis.

        Em análise/concluídos preservam a regra anterior: a própria pasta
        indica processamento. Em baixados, exige a sentinela existente.
        """
        nomes = []
        for diretorio in (baixados, em_analise, concluidos):
            if not diretorio.exists():
                continue
            for pasta in diretorio.iterdir():
                if pasta.is_dir() and (diretorio != baixados or (pasta / sentinela).is_file()):
                    try:
                        nomes.append(chave_projeto(pasta.name))
                    except ValueError:
                        print(f" [AVISO] Pasta sem código reconhecido, não importada: {pasta.name}")
        antes = len(self.projetos)
        self.registrar_varios(nomes)
        return len(self.projetos) - antes
