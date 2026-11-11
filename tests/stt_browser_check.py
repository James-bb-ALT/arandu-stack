"""Opt-in native STT view + real control API + installed model, without HA Core.

Run with ARANDU_STT_TEST_DATA, ARANDU_STT_TEST_WAV and ARANDU_STT_PYTHON.
The HA authentication boundary is covered separately in test_stt_multimodel.py.
"""
import asyncio
import json
import os
from pathlib import Path
import sys

from aiohttp import web
from playwright.async_api import async_playwright

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT/'arandu_stack/app'))
from server import create_app


HTML='''<!doctype html><html lang="pt-BR"><meta charset="utf-8"><meta name="viewport" content="width=device-width">
<title>Integração STT local</title><style>body{font:16px system-ui;margin:24px;max-width:1200px;background:#122d31;color:#f5f1e8}button,input,select{font:inherit;padding:10px;margin:5px;max-width:100%;box-sizing:border-box}label{display:block}audio{width:100%}.row{display:flex;flex-wrap:wrap;gap:8px}.scroll{overflow:auto}table{border-collapse:collapse}td,th{padding:8px;border:1px solid #789}pre{white-space:pre-wrap;overflow-wrap:anywhere}fieldset{margin:12px 0}</style>
<p>FIXTURE DE INTEGRAÇÃO · API e pesos reais · áudio sintético, sem Home Assistant físico</p><section id="sttLab"></section>
<script type="module">
import {mountSTT} from '/fixture/stt.js';
const state=await (await fetch('/api/state')).json();
window.panel={$:id=>document.getElementById(id),duration:value=>Number.isFinite(value)?value.toFixed(1)+' ms':'Não avaliado',
call:async(command,payload)=>{const action=command.slice(4),response=await fetch('/api/stt/'+action+(action==='models'?'?variant='+payload.variant:''),{
method:action==='models'?'GET':'POST',headers:{'Content-Type':'application/json','X-Arandu-CSRF':state.csrf},
body:action==='models'?undefined:JSON.stringify({...payload,model:payload.stt_model})});
const result=await response.json();if(!response.ok)throw Error(result.error);return result;}};
const original=navigator.mediaDevices.getUserMedia.bind(navigator.mediaDevices);
navigator.mediaDevices.getUserMedia=async(...args)=>window.fixtureStream=await original(...args);
mountSTT(window.panel);
</script></html>'''


async def main():
    data=Path(os.environ['ARANDU_STT_TEST_DATA']).resolve()
    audio=Path(os.environ['ARANDU_STT_TEST_WAV']).resolve()
    app=create_app(data,local=True)
    async def fixture(request):
        return web.Response(text=HTML,content_type='text/html')
    app.router.add_get('/fixture',fixture)
    app.router.add_get('/fixture/stt.js',lambda request:web.FileResponse(ROOT/'custom_components/local_nlu/stt_lab_panel.js'))
    # Fixture HTML contains inline bootstrap; production CSP remains untouched.
    app.on_response_prepare.append(fixture_csp)
    runner=web.AppRunner(app,access_log=None)
    await runner.setup();await web.TCPSite(runner,'127.0.0.1',8770).start()
    try:
        async with async_playwright() as playwright:
            browser=await playwright.chromium.launch(args=['--use-fake-ui-for-media-stream','--use-fake-device-for-media-stream',f'--use-file-for-fake-audio-capture={audio}'])
            context=await browser.new_context(permissions=['microphone'],accept_downloads=True)
            page=await context.new_page();errors=[]
            page.on('pageerror',lambda error:errors.append(str(error)))
            await page.goto('http://127.0.0.1:8770/fixture')
            field=lambda name:page.locator(f'[data-stt="{name}"]')
            await field('status').filter(has_text='Modelos consultados').wait_for()
            assert await field('model').locator('option').count()==8
            assert await field('models').locator('input').count()==8
            assert '487 MB' in await field('inventory').inner_text()
            await field('file').set_input_files(str(audio))
            await field('player').evaluate('audio=>audio.play().then(()=>audio.pause())')
            await field('reference').fill('Liga a luz da sala')
            await field('entityReference').fill('luz da sala')
            await field('test').click()
            await field('status').filter(has_text='Teste concluído').wait_for(timeout=90000)
            assert 'Liga a luz da sala' in await field('results').inner_text()
            async with page.expect_download() as event:
                await field('json').click()
            download=await event.value
            exported=json.loads(Path(await download.path()).read_text(encoding='utf-8'))
            assert exported['last']['results'][0]['model']=='whisper_base'
            assert exported['last']['results'][0]['state']=='ready'
            for check in await field('models').locator('input').all():
                await check.set_checked(await check.input_value() in ('whisper_tiny','whisper_base','orukeet_int8'))
            await field('compare').click()
            await field('status').filter(has_text='Teste concluído').wait_for(timeout=90000)
            assert 'Whisper Tiny' in await field('results').inner_text()
            assert 'Orukeet INT8' in await field('results').inner_text()
            await field('model').select_option('orukeet_int8')
            async with page.expect_download() as event:
                await field('csv').click()
            csv=Path(await (await event.value).path()).read_text(encoding='utf-8')
            assert 'wer_raw' in csv and 'whisper_base' in csv
            await field('record').click()
            await field('status').filter(has_text='Gravando.').wait_for()
            await page.wait_for_timeout(3000)
            await field('stop').click()
            await field('capture').filter(has_text='Áudio pronto').wait_for()
            assert await page.evaluate("fixtureStream.getTracks().every(track=>track.readyState==='ended')")
            assert (await field('player').get_attribute('src')).startswith('blob:')
            await field('test').click()
            await field('status').filter(has_text='Teste concluído').wait_for(timeout=90000)
            await field('clear').click()
            assert 'Nenhum teste' in await field('summary').inner_text()
            await page.set_viewport_size({'width':390,'height':844})
            assert await page.evaluate('document.documentElement.scrollWidth<=innerWidth')
            await field('file').set_input_files({'name':'invalid.wav','mimeType':'audio/wav','buffer':b'RIFFinvalid'})
            await field('test').click()
            await field('status').filter(has_text='inválido').wait_for(timeout=20000)
            await page.evaluate('panel.sttDispose()')
            assert not errors,errors
            print('PASS: eight models, upload, Base real, Orukeet real, comparison, JSON/CSV, MediaRecorder, track release, responsive view, invalid audio')
            await context.close();await browser.close()
    finally:
        await runner.cleanup()


async def fixture_csp(request,response):
    if request.path=='/fixture':
        response.headers.pop('Content-Security-Policy',None)


if __name__=='__main__':
    asyncio.run(main())
