from pathlib import Path

# ==============================================================================
# 1. CAMINHOS DO ONEDRIVE (Altere para o caminho real da sua pasta sincronizada)
# ==============================================================================
# Dica: No Windows Explorer, clique na barra de endereço da sua pasta do OneDrive
# e copie o caminho completo. O prefixo 'r' antes das aspas evita erros com '\'.
ONEDRIVE_BASE_DIR = Path(r"C:\Users\Dubi Special Super\Desktop\_Automação OPEE\automacao_opee_v1.5")

# Subpastas de controle de estado do Pipeline
DIR_BAIXADOS = ONEDRIVE_BASE_DIR / "01_Baixados_OPEE"
DIR_EM_ANALISE = ONEDRIVE_BASE_DIR / "02_Em_Analise"
DIR_CONCLUIDOS = ONEDRIVE_BASE_DIR / "03_Concluidos"
DIR_ERROS = ONEDRIVE_BASE_DIR / "04_Erros"

# Histórico permanente: uma linha por código de projeto concluído.
ARQUIVO_HISTORICO = ONEDRIVE_BASE_DIR / "projetos_baixados.txt"

# ==============================================================================
# 2. SESSÃO DO NAVEGADOR E TEMPLATES LOCAIS
# ==============================================================================
PROJECT_ROOT = Path(__file__).parent.resolve()
BROWSER_SESSION_DIR = PROJECT_ROOT / "sessao_navegador"
TEMPLATES_DIR = PROJECT_ROOT / "templates"

# ==============================================================================
# 3. URLS DO SISTEMA
# ==============================================================================
OPEE_URL = "https://siase.aneel.gov.br/webOpee/"
COPILOT_AGENT_URL = "https://copilot.cloud.microsoft/chat?fromcode=cmmlf487uwy&es=Click&redirfrom=cosmicRingCookie"
NOME_AGENTE_COPILOT = "AvaliaPEE_CHECKLIST_5.4v270926"
# Quantidade esperada de arquivos dentro da subpasta "Relatório Final"
QTD_ARQUIVOS_ESPERADOS = 5
ARQUIVO_SENTINELA = "_PRONTO.flag"

# Meta padrão de NOVOS projetos concluídos por execução.
# Pode ser substituída pela opção: python scraper_opee.py --quantidade 5
MAX_PROJETOS_POR_EXECUCAO = 5


def inicializar_pastas():
    """Garante que todas as pastas de controle existam no OneDrive e no projeto."""
    for pasta in [
        DIR_BAIXADOS,
        DIR_EM_ANALISE,
        DIR_CONCLUIDOS,
        DIR_ERROS,
        BROWSER_SESSION_DIR,
        TEMPLATES_DIR,
    ]:
        pasta.mkdir(parents=True, exist_ok=True)
    print(f"[OK] Estrutura de pastas verificada em: {ONEDRIVE_BASE_DIR}")