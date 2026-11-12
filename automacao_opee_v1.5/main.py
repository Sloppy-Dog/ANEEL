import argparse
import asyncio
import time
from playwright.async_api import async_playwright

from config import (
    DIR_BAIXADOS,
    DIR_CONCLUIDOS,
    ARQUIVO_SENTINELA,
    MAX_PROJETOS_POR_EXECUCAO,
    inicializar_pastas,
)
from scraper_opee import executar_scraper, inteiro_positivo
from worker_copilot import (
    LOCK_FILA_GLOBAL,
    executar_pool_copilot_async,
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
    if contar_pendentes_na_fila() == 0:
        return {
            "sucessos_copilot": 0,
            "falhas_copilot": 0,
            "sucessos_gpt": 0,
            "falhas_gpt": 0,
        }

    async with async_playwright() as p:
        browser = await p.chromium.connect_over_cdp("http://localhost:9222")
        context = browser.contexts[0]

        corrotinas = []
        if agentes_copilot > 0:
            corrotinas.append(
                executar_pool_copilot_async(
                    agentes=agentes_copilot,
                    context_externo=context,
                    lock_fila_externo=LOCK_FILA_GLOBAL,
                )
            )
        else:
            corrotinas.append(asyncio.sleep(0, result={"sucessos": 0, "falhas": 0}))

        if agentes_gpt > 0:
            corrotinas.append(
                executar_pool_chatgpt_async(
                    agentes=agentes_gpt,
                    context_externo=context,
                    lock_fila_externo=LOCK_FILA_GLOBAL,
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

    restantes = quantidade
    ciclo = 1

    while restantes > 0:
        tamanho_passo = min(restantes, total_agentes)
        info_filtro = f" [FILTRO: '{termo_busca}']" if termo_busca else " [AUTOMÁTICO]"
        print("\n" + "=" * 75)
        print(
            f" CICLO {ciclo}{info_filtro} — BAIXANDO {tamanho_passo} PROJETO(S) -> "
            f"{total_agentes} AGENTE(S) ({agentes_copilot} COPILOT + {agentes_gpt} CHATGPT)"
        )
        print("=" * 75)

        resumo_scraper = executar_scraper(quantidade=tamanho_passo, termo_busca=termo_busca)
        novos = resumo_scraper.get("concluidos", 0)
        baixados_total += novos
        restantes -= novos

        if novos == 0 and contar_pendentes_na_fila() == 0:
            print("\n[FIM] Não há mais projetos novos disponíveis no OPEE.")
            break

        res_ciclo = executar_pools_combinados(agentes_copilot, agentes_gpt)
        analisados_copilot += res_ciclo["sucessos_copilot"]
        falhas_copilot += res_ciclo["falhas_copilot"]
        analisados_gpt += res_ciclo["sucessos_gpt"]
        falhas_gpt += res_ciclo["falhas_gpt"]

        if (res_ciclo["falhas_copilot"] + res_ciclo["falhas_gpt"]) > 0:
            print("[AVISO] Ocorreu falha em pelo menos um agente. Interrompendo ciclo por segurança.")
            break

        ciclo += 1

    return {
        "baixados": baixados_total,
        "analisados_copilot": analisados_copilot,
        "falhas_copilot": falhas_copilot,
        "analisados_gpt": analisados_gpt,
        "falhas_gpt": falhas_gpt,
    }


def rodar_modo_lote(
    quantidade: int,
    agentes_copilot: int,
    agentes_gpt: int,
    termo_busca: str | None = None,
) -> dict:
    """Baixa todos os N projetos sequencialmente no OPEE e depois dispara Copilot + ChatGPT em paralelo."""
    total_agentes = agentes_copilot + agentes_gpt
    info_filtro = f" | BUSCA: '{termo_busca}'" if termo_busca else " | MODO AUTOMÁTICO"
    print("\n" + "=" * 75)
    print(
        f" MODO LOTE — COLETA DE {quantidade} PROJETO(S){info_filtro} + POOL DE {total_agentes} AGENTE(S) "
        f"({agentes_copilot} COPILOT + {agentes_gpt} CHATGPT)"
    )
    print("=" * 75)

    print(f"\n>>> [ETAPA 1/2] Coletando {quantidade} projeto(s) sequencialmente no OPEE...")
    resumo_scraper = executar_scraper(quantidade=quantidade, termo_busca=termo_busca)
    baixados_total = resumo_scraper.get("concluidos", 0)

    print(
        f"\n>>> [ETAPA 2/2] Processando fila em paralelo com {total_agentes} aba(s) "
        f"({agentes_copilot} Copilot + {agentes_gpt} ChatGPT)..."
    )
    res = executar_pools_combinados(agentes_copilot, agentes_gpt)

    return {
        "baixados": baixados_total,
        "analisados_copilot": res["sucessos_copilot"],
        "falhas_copilot": res["falhas_copilot"],
        "analisados_gpt": res["sucessos_gpt"],
        "falhas_gpt": res["falhas_gpt"],
    }


def main():
    parser = argparse.ArgumentParser(
        description="Orquestrador completo: coleta sequencial no OPEE + processamento paralelo no Copilot e ChatGPT."
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
        help="Código ou nome de projeto para pesquisar no OPEE (ex: PE-00404). Se omitido, pergunta antes de iniciar.",
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

    inicializar_pastas()

    if args.recuperar:
        recuperar_pastas_com_erro()

    # Pergunta interativamente antes de começar a processar, caso não tenha sido passado via -p/--projeto
    termo_busca = args.projeto
    if termo_busca is None:
        resposta = input(
            "\n[OPÇÃO DE BUSCA] Digite o nome/código do projeto para pesquisar (ex: PE-00404) "
            "ou pressione ENTER para baixar no modo automático: "
        ).strip()
        termo_busca = resposta if resposta else None

    while True:
        inicio = time.monotonic()

        if args.modo == "intercalado":
            resumo = rodar_modo_intercalado(args.quantidade, args.agentes, args.gpt, termo_busca=termo_busca)
        else:
            resumo = rodar_modo_lote(args.quantidade, args.agentes, args.gpt, termo_busca=termo_busca)

        duracao_min = (time.monotonic() - inicio) / 60.0
        total_analisados = resumo["analisados_copilot"] + resumo["analisados_gpt"]
        total_falhas = resumo["falhas_copilot"] + resumo["falhas_gpt"]

        print("\n" + "=" * 75)
        print(" RESUMO DA EXECUÇÃO DO PIPELINE")
        print("=" * 75)
        print(f" - Modo de coleta no OPEE          : {'Busca (' + termo_busca + ')' if termo_busca else 'Automático'}")
        print(f" - Novos projetos baixados do OPEE : {resumo['baixados']}")
        print(f" - Analisados pelo Copilot         : {resumo['analisados_copilot']} (usando {args.agentes} agente(s))")
        print(f" - Analisados pelo ChatGPT         : {resumo['analisados_gpt']} (usando {args.gpt} agente(s))")
        print(f" - Total analisados com sucesso    : {total_analisados}")
        print(f" - Falhas (Copilot / ChatGPT)      : {resumo['falhas_copilot']} / {resumo['falhas_gpt']} (Total: {total_falhas})")
        print(f" - Tempo total decorrido           : {duracao_min:.1f} minuto(s)")
        print(f" - Pasta final dos concluídos      : {DIR_CONCLUIDOS}")
        print("=" * 75)

        if not args.continuo:
            break

        print(f"\n[MODO CONTÍNUO] Aguardando {args.intervalo} minuto(s) para a próxima varredura (Ctrl+C para parar)...")
        time.sleep(args.intervalo * 60)


if __name__ == "__main__":
    main()
