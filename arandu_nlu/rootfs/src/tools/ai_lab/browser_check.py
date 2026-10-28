"""Browser contract test with explicitly simulated HA websocket. Not an inference benchmark."""
import asyncio
import functools
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
import threading


async def main():
    from playwright.async_api import async_playwright
    root = Path(__file__).resolve().parents[2]
    class Quiet(SimpleHTTPRequestHandler):
        def log_message(self, *_): pass
        def guess_type(self, path):
            return "text/javascript; charset=utf-8" if path.endswith(".js") else super().guess_type(path)
    server = ThreadingHTTPServer(("127.0.0.1", 0), functools.partial(Quiet, directory=str(root)))
    threading.Thread(target=server.serve_forever, daemon=True).start()
    try:
        async with async_playwright() as p:
            browser = await p.chromium.launch(headless=True)
            page = await browser.new_page(viewport={"width": 1440, "height": 1100})
            errors = []
            page.on("pageerror", lambda e: errors.append(str(e)))
            await page.goto(f"http://127.0.0.1:{server.server_port}/custom_components/local_nlu/ai_lab_panel.js")
            await page.evaluate("document.body.replaceChildren()")
            await page.add_script_tag(url=f"http://127.0.0.1:{server.server_port}/custom_components/local_nlu/ai_lab_panel.js")
            await page.evaluate("""() => {
              window.calls=[];
              const el=document.createElement('arandu-ai-lab'); document.body.append(el);
              el.hass={callWS:async request=>{window.calls.push(request);const c=request.payload.command;
                if(c==='status')return {models:[{id:'brazembed',name:'BrazEmbed',kind:'decision',available:true},{id:'qwen',name:'Qwen',kind:'llm',available:true},{id:'jatoba',name:'JATOBÁ',kind:'decision',available:false,reason:'não confirmado'}]};
                if(c==='run')return {text:request.payload.text,model:'brazembed',source:'nlu',dry_run:true,operations_validated:true,execution_token:'fixture-only',total_ms:2,proposal:{status:'plan'},stages:Object.fromEntries(['vad','stt','nlu','decision','llm','ha','tts'].map(name=>[name,{status:name==='nlu'?'executed':'skipped',latency_ms:name==='nlu'?2:null}]))};
                if(c==='execute')return {code:'success',response_text:'FIXTURE_TECNICA'};
                if(c==='compare')return {models:{brazembed:{rows:[],metrics:{accuracy:null}}},dry_run:true,sequential:true};
                throw Error('fixture command unsupported '+c);
              }};
            }""")
            await page.wait_for_function("document.querySelector('arandu-ai-lab').shadowRoot.getElementById('status').textContent==='Teste finalizado.'")
            panel = page.locator("arandu-ai-lab")
            assert await panel.get_by_text("Comando em português", exact=True).count() == 1
            await panel.locator("#text").fill("FIXTURE_TECNICA <img src=x onerror=alert(1)>")
            await panel.locator("#run").click()
            await page.wait_for_function("window.calls.some(r=>r.payload.command==='run')")
            assert await panel.locator("#execute").is_disabled()
            assert await panel.locator("#history img").count() == 0
            assert "execution_token" not in await page.evaluate("localStorage.getItem('arandu-lab-history')")
            await panel.locator("#real").check()
            assert await panel.locator("#execute").is_enabled()
            await panel.locator("#text").fill("FIXTURE_TECNICA texto alterado")
            assert await panel.locator("#execute").is_disabled()
            await panel.locator("#run").click()
            await page.wait_for_function("document.querySelector('arandu-ai-lab').last?.execution_token === 'fixture-only'")
            assert await panel.locator("#execute").is_enabled()
            await panel.locator("#execute").click()
            await page.wait_for_function("window.calls.some(r=>r.payload.command==='execute')")
            assert await panel.locator("#execute").is_disabled()
            await page.screenshot(path=str(root / "target" / "ai-lab-desktop.png"), full_page=True)
            await page.set_viewport_size({"width": 390, "height": 844})
            assert await page.evaluate("document.querySelector('arandu-ai-lab').shadowRoot.querySelector('main').scrollWidth") <= 390
            await page.screenshot(path=str(root / "target" / "ai-lab-mobile.png"), full_page=True)
            assert not errors, errors
            await browser.close()
            print("Browser PASS: render, dry-run, explicit execution, one-shot token UI, local history, XSS text escaping, mobile width")
    finally:
        server.shutdown()
        server.server_close()


if __name__ == "__main__":
    asyncio.run(main())
