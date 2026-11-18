import argparse
import asyncio
import sys
import time
from pathlib import Path
from playwright.async_api import async_playwright

from config import (
    DIR_BAIXADOS,
    DIR_CONCLUIDOS,
    DIR_ERROS,
    ARQUIVO_SENTINELA,
    MAX_PROJETOS_POR_EXECUCAO,
    inicializar_pastas,
)
from controle_planilha import (
    CAMINHO_PLANILHA_PADRAO,
    selecionar_lote_planilha_interativo,
    sincronizar_concluidos_planilha,
)
from scraper_opee import executar_scraper, inteiro_positivo
from worker_copilot import (
    LOCK_FILA_GLOBAL,
    executar_pool_copilot_async,
    motivo_parada,
    parada_solicitada,
    recuperar_pastas_com_erro,
)
from worker_chatgpt import executar_pool_chatgpt_async


def inteiro_nao_negativo(valor) -> int:
    try:
        n = int(valor)
    except (ValueError, TypeError) as erro:
        raise argparse.ArgumentTypeError("Informe um número inteiro maior ou igual a zero.") from erro
    if n < 0:
        raise argparse.ArgumentTypeError("Informe um número inteiro maior ou igual a zero.")
    return n


def contar_pendentes_na_fila() -> int:
    if not DIR_BAIXADOS.exists():
        return 0
    return sum(
        1 for pasta in DIR_BAIXADOS.iterdir()
        if pasta.is_dir() and (pasta / ARQUIVO_SENTINELA).is_file()
    )


async def _executar_pools_combinados_async(agentes_copilot: int, agentes_gpt: int) -> dict:
    """
    Executa simultaneamente os agentes do Copilot (-a) e do ChatGPT (-g)
    compartilhando o mesmo navegador CDP e a mesma fila de projetos (01_Baixados_OPEE).
    """
    if parada_solicitada() or contar_pendentes_na_fila() == 0:
        return {
            "sucessos_copilot": 0,
            "falhas_copilot": 0,
            "sucessos_gpt": 0,
            "falhas_gpt": 0,
        }

    async with async_playwright() as p:
        browser = await p.chromium.connect_over_cdp("http://localhost:9222")
        context = browser.contexts[0]

        # CRIAMOS O LOCK AQUI DENTRO, GARANTINDO QUE ELE PERTENÇA AO LOOP ATUAL
        lock_fila_seguro = asyncio.Lock()

        corrotinas = []
        if agentes_copilot > 0:
            corrotinas.append(
                executar_pool_copilot_async(
                    agentes=agentes_copilot,
                    context_externo=context,
                    lock_fila_externo=lock_fila_seguro, # Passa o lock seguro
                )
            )
        else:
            corrotinas.append(asyncio.sleep(0, result={"sucessos": 0, "falhas": 0}))

        if agentes_gpt > 0:
            corrotinas.append(
                executar_pool_chatgpt_async(
                    agentes=agentes_gpt,
                    context_externo=context,
                    lock_fila_externo=lock_fila_seguro, # Passa o lock seguro
                )
            )
        else:
            corrotinas.append(asyncio.sleep(0, result={"sucessos": 0, "falhas": 0}))

        res_copilot, res_gpt = await asyncio.gather(*corrotinas)

    return {
        "sucessos_copilot": res_copilot["sucessos"],
        "falhas_copilot": res_copilot["falhas"],
        "sucessos_gpt": res_gpt["sucessos"],
        "falhas_gpt": res_gpt["falhas"],
    }


def executar_pools_combinados(agentes_copilot: int, agentes_gpt: int) -> dict:
    return asyncio.run(_executar_pools_combinados_async(agentes_copilot, agentes_gpt))


def rodar_modo_intercalado(
    quantidade: int,
    agentes_copilot: int,
    agentes_gpt: int,
    termo_busca: str | None = None,
    projetos_planilha: list[str] | None = None,
    caminho_planilha: Path = CAMINHO_PLANILHA_PADRAO,
) -> dict:
    """
    Baixa em lotes do tamanho total de agentes (Copilot + ChatGPT) e
    dispara todos em paralelo sobre a fila.
    """
    total_agentes = agentes_copilot + agentes_gpt
    analisados_copilot = 0
    falhas_copilot = 0
    analisados_gpt = 0
    falhas_gpt = 0
    baixados_total = 0
    pintados_roxo_total = 0

    if contar_pendentes_na_fila() > 0:
        print(
            f"\n[FILA INICIAL] Processando {contar_pendentes_na_fila()} projeto(s) já disponíveis "
            f"com {total_agentes} agente(s) ({agentes_copilot} Copilot + {agentes_gpt} ChatGPT)..."
        )
        res_inicial = executar_pools_combinados(agentes_copilot, agentes_gpt)
        analisados_copilot += res_inicial["sucessos_copilot"]
        falhas_copilot += res_inicial["falhas_copilot"]
        analisados_gpt += res_inicial["sucessos_gpt"]
        falhas_gpt += res_inicial["falhas_gpt"]
        if projetos_planilha is not None:
            pintados_roxo_total += sincronizar_concluidos_planilha(caminho_planilha)

    if parada_solicitada() or (falhas_copilot + falhas_gpt) > 0:
        print("[PARADA] Erro na fila inicial. Nenhum projeto novo será baixado nem avaliado.")
        restantes_iniciais = 0
    else:
        restantes_iniciais = None

    fila_candidatos = list(projetos_planilha) if projetos_planilha is not None else None
    restantes = min(quantidade, len(fila_candidatos)) if fila_candidatos is not None else quantidade
    if restantes_iniciais is not None:
        restantes = restantes_iniciais
    ciclo = 1

    while restantes > 0:
        if fila_candidatos is not None and len(fila_candidatos) == 0:
            print("\n[FIM PLANILHA] Todos os projetos candidatos deste lote foram percorridos.")
            break

        tamanho_passo = min(restantes, total_agentes)
        if fila_candidatos is not None:
            info_filtro = f" [PLANILHA: {len(fila_candidatos)} restante(s) no lote]"
        elif termo_busca:
            info_filtro = f" [FILTRO: '{termo_busca}']"
        else:
            info_filtro = " [AUTOMÁTICO]"

        print("\n" + "=" * 75)
        print(
            f" CICLO {ciclo}{info_filtro} — BAIXANDO {tamanho_passo} PROJETO(S) -> "
            f"{total_agentes} AGENTE(S) ({agentes_copilot} COPILOT + {agentes_gpt} CHATGPT)"
        )
        print("=" * 75)

        resumo_scraper = executar_scraper(
            quantidade=tamanho_passo,
            termo_busca=termo_busca,
            projetos_planilha=fila_candidatos,
            caminho_planilha=caminho_planilha,
        )
        if fila_candidatos is not None:
            consumidos = resumo_scraper.get("consumidos_da_lista", 0)
            fila_candidatos = fila_candidatos[consumidos:]

        novos = resumo_scraper.get("concluidos", 0)
        baixados_total += novos
        restantes -= novos

        if novos == 0 and contar_pendentes_na_fila() == 0:
            print("\n[FIM] Não há mais projetos novos disponíveis no OPEE para este ciclo.")
            break

        res_ciclo = executar_pools_combinados(agentes_copilot, agentes_gpt)
        analisados_copilot += res_ciclo["sucessos_copilot"]
        falhas_copilot += res_ciclo["falhas_copilot"]
        analisados_gpt += res_ciclo["sucessos_gpt"]
        falhas_gpt += res_ciclo["falhas_gpt"]

        if projetos_planilha is not None:
            pintados_roxo_total += sincronizar_concluidos_planilha(caminho_planilha)

        if parada_solicitada() or (res_ciclo["falhas_copilot"] + res_ciclo["falhas_gpt"]) > 0:
            print("[PARADA] Ocorreu falha em pelo menos um agente. Nenhum projeto novo será baixado nem avaliado.")
            break

        ciclo += 1

    return {
        "baixados": baixados_total,
        "analisados_copilot": analisados_copilot,
        "falhas_copilot": falhas_copilot,
        "analisados_gpt": analisados_gpt,
        "falhas_gpt": falhas_gpt,
        "pintados_roxo": pintados_roxo_total,
    }


def listar_pastas_sem_sentinela() -> list[Path]:
    """Pastas de 01_Baixados_OPEE com arquivos, mas sem o marcador de pronto (_PRONTO.flag)."""
    if not DIR_BAIXADOS.exists():
        return []
    return sorted(
        pasta for pasta in DIR_BAIXADOS.iterdir()
        if pasta.is_dir()
        and not (pasta / ARQUIVO_SENTINELA).is_file()
        and any(item.is_file() for item in pasta.iterdir())
    )


def preparar_fila_offline(incluir_sem_marcador: bool | None) -> bool | None:
    """
    Verifica a fila de 01_Baixados_OPEE para o modo offline.

    Pastas copiadas manualmente para 01_Baixados_OPEE não têm o arquivo _PRONTO.flag
    (criado pelo scraper ao fim do download) e seriam ignoradas pelos agentes.
    Na primeira chamada (incluir_sem_marcador=None) pergunta se devem entrar na fila;
    a resposta é devolvida para ser reaproveitada nas varreduras do modo --continuo.
    """
    sem_marcador = listar_pastas_sem_sentinela()
    if sem_marcador:
        if incluir_sem_marcador is None:
            print(f"\n[OFFLINE] {len(sem_marcador)} pasta(s) em '{DIR_BAIXADOS.name}' sem o marcador {ARQUIVO_SENTINELA}:")
            for pasta in sem_marcador:
                print(f"   - {pasta.name}")
            print(" Elas podem ter sido copiadas manualmente ou ser um download interrompido do OPEE.")
            resposta = input(" Incluir essas pastas na avaliação? [s/N]: ").strip().lower()
            incluir_sem_marcador = resposta in ("s", "sim", "y", "yes")
        if incluir_sem_marcador:
            for pasta in sem_marcador:
                (pasta / ARQUIVO_SENTINELA).touch()
                print(f"[OFFLINE] {pasta.name} marcada como pronta para avaliação.")
        else:
            print(f"[OFFLINE] {len(sem_marcador)} pasta(s) sem marcador ficarão de fora desta avaliação.")
    return incluir_sem_marcador


def rodar_modo_offline(agentes_copilot: int, agentes_gpt: int) -> dict:
    """Avalia apenas os projetos já presentes em 01_Baixados_OPEE, sem acessar o OPEE."""
    total_agentes = agentes_copilot + agentes_gpt
    pendentes = contar_pendentes_na_fila()

    print("\n" + "=" * 75)
    print(
        f" MODO OFFLINE — AVALIAÇÃO DE {pendentes} PROJETO(S) DE '{DIR_BAIXADOS.name}' + POOL DE "
        f"{total_agentes} AGENTE(S) ({agentes_copilot} COPILOT + {agentes_gpt} CHATGPT)"
    )
    print("=" * 75)

    if pendentes == 0:
        print(f"[OFFLINE] Nenhum projeto pronto em: {DIR_BAIXADOS}")
        res = {"sucessos_copilot": 0, "falhas_copilot": 0, "sucessos_gpt": 0, "falhas_gpt": 0}
    else:
        res = executar_pools_combinados(agentes_copilot, agentes_gpt)

    return {
        "baixados": 0,
        "analisados_copilot": res["sucessos_copilot"],
        "falhas_copilot": res["falhas_copilot"],
        "analisados_gpt": res["sucessos_gpt"],
        "falhas_gpt": res["falhas_gpt"],
        "pintados_roxo": 0,
    }


def rodar_modo_lote(
    quantidade: int,
    agentes_copilot: int,
    agentes_gpt: int,
    termo_busca: str | None = None,
    projetos_planilha: list[str] | None = None,
    caminho_planilha: Path = CAMINHO_PLANILHA_PADRAO,
) -> dict:
    """Baixa os projetos sequencialmente no OPEE e depois dispara Copilot + ChatGPT em paralelo."""
    total_agentes = agentes_copilot + agentes_gpt
    if projetos_planilha is not None:
        info_filtro = f" | MODO PLANILHA ({len(projetos_planilha)} candidato(s))"
    elif termo_busca:
        info_filtro = f" | BUSCA: '{termo_busca}'"
    else:
        info_filtro = " | MODO AUTOMÁTICO"

    print("\n" + "=" * 75)
    print(
        f" MODO LOTE — COLETA DE ATÉ {quantidade} PROJETO(S){info_filtro} + POOL DE {total_agentes} AGENTE(S) "
        f"({agentes_copilot} COPILOT + {agentes_gpt} CHATGPT)"
    )
    print("=" * 75)

    print(f"\n>>> [ETAPA 1/2] Coletando até {quantidade} projeto(s) sequencialmente no OPEE...")
    resumo_scraper = executar_scraper(
        quantidade=quantidade,
        termo_busca=termo_busca,
        projetos_planilha=projetos_planilha,
        caminho_planilha=caminho_planilha,
    )
    baixados_total = resumo_scraper.get("concluidos", 0)

    print(
        f"\n>>> [ETAPA 2/2] Processando fila em paralelo com {total_agentes} aba(s) "
        f"({agentes_copilot} Copilot + {agentes_gpt} ChatGPT)..."
    )
    res = executar_pools_combinados(agentes_copilot, agentes_gpt)

    pintados_roxo = 0
    if projetos_planilha is not None:
        print("\n>>> [PLANILHA] Verificando pasta 03_Concluidos (.docx + .json) para pintar de ROXO...")
        pintados_roxo = sincronizar_concluidos_planilha(caminho_planilha)

    return {
        "baixados": baixados_total,
        "analisados_copilot": res["sucessos_copilot"],
        "falhas_copilot": res["falhas_copilot"],
        "analisados_gpt": res["sucessos_gpt"],
        "falhas_gpt": res["falhas_gpt"],
        "pintados_roxo": pintados_roxo,
    }


def main():
    parser = argparse.ArgumentParser(
        description=(
            "Orquestrador completo: coleta no OPEE (Planilha ou Busca/Automático) + Copilot e ChatGPT, "
            "ou avaliação OFFLINE dos projetos já presentes em 01_Baixados_OPEE."
        )
    )
    parser.add_argument(
        "--quantidade", "-q",
        type=inteiro_positivo,
        default=MAX_PROJETOS_POR_EXECUCAO,
        help=f"Quantidade de novos projetos do OPEE a baixar e analisar (padrão: {MAX_PROJETOS_POR_EXECUCAO}).",
    )
    parser.add_argument(
        "--agentes", "-a",
        type=inteiro_nao_negativo,
        default=1,
        help="Quantidade de abas do Copilot rodando em paralelo (padrão: 1 | use 0 para desativar).",
    )
    parser.add_argument(
        "--gpt", "-g",
        type=inteiro_nao_negativo,
        default=0,
        help="Quantidade de abas do ChatGPT rodando em paralelo (padrão: 0).",
    )
    parser.add_argument(
        "--projeto", "-p",
        type=str,
        default=None,
        help="Código ou nome de projeto para pesquisar no OPEE (ex: PE-00404).",
    )
    parser.add_argument(
        "--planilha",
        action="store_true",
        help="Inicia diretamente no modo de extração via planilha.",
    )
    parser.add_argument(
        "--offline", "-o",
        action="store_true",
        help="Não acessa o OPEE: avalia apenas os projetos já presentes em 01_Baixados_OPEE.",
    )
    parser.add_argument(
        "--modo", "-m",
        choices=["intercalado", "lote"],
        default="lote",
        help="'lote' (padrão): baixa todos do OPEE primeiro e roda os pools depois. 'intercalado': baixa em blocos do tamanho de (-a + -g).",
    )
    parser.add_argument(
        "--recuperar", "-r",
        action="store_true",
        help="Devolve pastas de 02_Em_Analise e 04_Erros para 01_Baixados_OPEE antes de começar.",
    )
    parser.add_argument(
        "--continuo", "-c",
        action="store_true",
        help="Mantém o orquestrador rodando em loop contínuo a cada intervalo.",
    )
    parser.add_argument(
        "--intervalo", "-i",
        type=inteiro_positivo,
        default=15,
        help="Intervalo em minutos entre execuções no modo --continuo (padrão: 15).",
    )
    args = parser.parse_args()

    if (args.agentes + args.gpt) <= 0:
        parser.error("Informe pelo menos 1 agente ativo somando --agentes (-a) e --gpt (-g).")
    if args.offline and (args.planilha or args.projeto is not None):
        parser.error("--offline não pode ser combinado com --planilha ou --projeto (-p).")

    inicializar_pastas()

    if args.recuperar:
        recuperar_pastas_com_erro()

    # Menu inicial: Opção 1 (Planilha) vs Opção 2 (Pesquisa / Automático) vs Opção 3 (Offline)
    usar_planilha = args.planilha
    modo_offline = args.offline
    if not usar_planilha and not modo_offline and args.projeto is None:
        print("\n" + "=" * 75)
        print(" MODO DE EXTRAÇÃO DO PIPELINE")
        print("=" * 75)
        print(" [1] Extração COM Planilha ('Projeto Piloto Lotes')")
        print(" [2] Extração SEM Planilha (Opção Pesquisa / Automático)")
        print(f" [3] Avaliação OFFLINE (sem OPEE: usa só os projetos já em '{DIR_BAIXADOS.name}')")
        escolha_modo = input("\nEscolha uma opção [1/2/3] (Padrão=2): ").strip()
        usar_planilha = (escolha_modo == "1")
        modo_offline = (escolha_modo == "3")

    termo_busca = args.projeto
    incluir_sem_marcador = None
    if not usar_planilha and not modo_offline and termo_busca is None:
        resposta = input(
            "\n[OPÇÃO DE BUSCA] Digite o nome/código do projeto para pesquisar (ex: PE-00404) "
            "ou pressione ENTER para baixar no modo automático: "
        ).strip()
        termo_busca = resposta if resposta else None

    while True:
        projetos_planilha = None
        prefixo_planilha = None

        if usar_planilha:
            prefixo_planilha, projetos_planilha = selecionar_lote_planilha_interativo(
                CAMINHO_PLANILHA_PADRAO
            )
            if not projetos_planilha:
                print("[INFO] Encerrando execução (nenhum lote da planilha selecionado).")
                break

        inicio = time.monotonic()

        if modo_offline:
            incluir_sem_marcador = preparar_fila_offline(incluir_sem_marcador)
            resumo = rodar_modo_offline(args.agentes, args.gpt)
        elif args.modo == "intercalado":
            resumo = rodar_modo_intercalado(
                args.quantidade,
                args.agentes,
                args.gpt,
                termo_busca=termo_busca,
                projetos_planilha=projetos_planilha,
                caminho_planilha=CAMINHO_PLANILHA_PADRAO,
            )
        else:
            resumo = rodar_modo_lote(
                args.quantidade,
                args.agentes,
                args.gpt,
                termo_busca=termo_busca,
                projetos_planilha=projetos_planilha,
                caminho_planilha=CAMINHO_PLANILHA_PADRAO,
            )

        duracao_min = (time.monotonic() - inicio) / 60.0
        total_analisados = resumo["analisados_copilot"] + resumo["analisados_gpt"]
        total_falhas = resumo["falhas_copilot"] + resumo["falhas_gpt"]

        if modo_offline:
            desc_modo = "Offline (somente fila local)"
        elif usar_planilha:
            desc_modo = f"Planilha (Lote {prefixo_planilha})"
        elif termo_busca:
            desc_modo = f"Busca ({termo_busca})"
        else:
            desc_modo = "Automático"

        print("\n" + "=" * 75)
        print(" RESUMO DA EXECUÇÃO DO PIPELINE")
        print("=" * 75)
        print(f" - Modo de coleta no OPEE          : {desc_modo}")
        print(f" - Novos projetos baixados do OPEE : {resumo['baixados']}")
        print(f" - Analisados pelo Copilot         : {resumo['analisados_copilot']} (usando {args.agentes} agente(s))")
        print(f" - Analisados pelo ChatGPT         : {resumo['analisados_gpt']} (usando {args.gpt} agente(s))")
        print(f" - Total analisados com sucesso    : {total_analisados}")
        if usar_planilha:
            print(f" - Projetos pintados de ROXO (.xlsx): {resumo.get('pintados_roxo', 0)}")
        print(f" - Falhas (Copilot / ChatGPT)      : {resumo['falhas_copilot']} / {resumo['falhas_gpt']} (Total: {total_falhas})")
        print(f" - Tempo total decorrido           : {duracao_min:.1f} minuto(s)")
        print(f" - Pasta final dos concluídos      : {DIR_CONCLUIDOS}")
        print("=" * 75)

        if parada_solicitada() or total_falhas > 0:
            print("\n" + "!" * 75)
            print(" EXECUÇÃO INTERROMPIDA POR ERRO")
            print("!" * 75)
            if motivo_parada():
                print(f" Motivo: {motivo_parada()}")
            print(f" O projeto com erro está em: {DIR_ERROS}")
            print(" A aba do erro foi mantida aberta no navegador para conferência.")
            print(" Depois de verificar, rode novamente com  -r  para devolver o projeto à fila.")
            print("!" * 75)
            sys.exit(1)

        if not args.continuo:
            break

        if modo_offline:
            print(f"\n[MODO CONTÍNUO | OFFLINE] Aguardando {args.intervalo} minuto(s) para verificar novamente '{DIR_BAIXADOS.name}' (Ctrl+C para parar)...")
        else:
            print(f"\n[MODO CONTÍNUO] Aguardando {args.intervalo} minuto(s) para a próxima varredura (Ctrl+C para parar)...")
        time.sleep(args.intervalo * 60)


if __name__ == "__main__":
    main()