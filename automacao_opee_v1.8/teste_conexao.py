from playwright.sync_api import sync_playwright

def assumir_navegador_aberto():
    with sync_playwright() as p:
        # Conecta no Edge que voce ja abriu e fez login
        browser = p.chromium.connect_over_cdp("http://localhost:9222")
        
        # Pega a janela atual do Edge
        context = browser.contexts[0]
        
        # Procura especificamente a aba onde o OPEE esta aberto
        page = context.pages[0]
        for aba in context.pages:
            if "copilot" in aba.url.lower() or aba.title():
                page = aba
        
        print("\n" + "="*50)
        print("[SUCESSO] O Python assumiu o controle do Edge!")
        print(f"Titulo da aba: {page.title()}")
        print(f"URL atual: {page.url}")
        print("="*50 + "\n")
        
        # Abre a janelinha do Playwright Inspector direto na sua aba ja logada!
        page.pause()

if __name__ == "__main__":
    assumir_navegador_aberto()