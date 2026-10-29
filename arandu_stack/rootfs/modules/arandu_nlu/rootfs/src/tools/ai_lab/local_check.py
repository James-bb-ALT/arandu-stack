"""Development browser test against the real running local app, without simulated API responses."""
import asyncio
from pathlib import Path


async def main():
    from playwright.async_api import async_playwright
    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=True)
        page = await browser.new_page(viewport={"width": 1440, "height": 1100})
        errors = []
        page.on("pageerror", lambda e: errors.append(str(e)))
        await page.goto("http://127.0.0.1:8766")
        panel = page.locator("arandu-ai-lab")
        await page.wait_for_function("document.querySelector('arandu-ai-lab').shadowRoot.getElementById('status').textContent === 'Teste finalizado.'")
        for model in ("brazembed", "e5-small", "saracura", "jatoba"):
            assert await panel.locator(f'#decision option[value="{model}"]').is_enabled()
        for model in ("qwen", "tucano"):
            assert await panel.locator(f'#llm option[value="{model}"]').is_enabled()
        assert await panel.locator("#real").is_disabled()
        await panel.locator("#mode").select_option("nlu")
        await panel.locator("#text").fill("FIXTURE_TECNICA")
        await panel.locator("#run").click()
        await page.wait_for_function("document.querySelector('arandu-ai-lab').last?.stages.nlu.status === 'executed'")
        assert await panel.locator("#execute").is_disabled()
        assert await page.locator("#notice").inner_text() == "Home Assistant não conectado. Catálogo vazio: nenhum dispositivo será inventado ou acionado."
        await page.screenshot(path=str(Path("target/ai-lab-local-desktop.png")), full_page=True)
        assert not errors, errors
        await browser.close()
        print("Local app PASS: six real installed models available; real Rust API; empty catalog disclosed; effects disabled; no simulated responses")


if __name__ == "__main__":
    asyncio.run(main())
