"""Opt-in actual Linux stack checks against an isolated validation container.

The container must mount installed models and isolated data, publish only to
127.0.0.1 and set ARANDU_LOCAL_CONTAINER=1. Never point this at production.
"""
import asyncio
import base64
import json
import os
from pathlib import Path
import sys

from aiohttp import ClientSession, ClientTimeout
from playwright.async_api import async_playwright
from wyoming.asr import Transcribe, Transcript
from wyoming.audio import AudioChunk, AudioStop
from wyoming.client import AsyncClient
from wyoming.info import Describe, Info

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT/'arandu_stack/app'))
from stt_lab import decode_audio
from model_catalog import CATALOG


async def main():
    audio=Path(os.environ['ARANDU_STT_TEST_WAV']).read_bytes()
    pcm,_=await decode_audio(audio)
    async with ClientSession(timeout=ClientTimeout(total=780)) as session:
        async with session.get('http://127.0.0.1:8770/api/state') as response:
            state=await response.json()
        assert state['version']=='5.0.0',state
        headers={'X-Arandu-CSRF':state['csrf']}
        async def request(path,body,expected=200):
            async with session.post('http://127.0.0.1:8770/api/'+path,json=body,headers=headers) as response:
                result=await response.json()
                assert response.status==expected,(response.status,result)
                return result
        # Exercise the actual dashboard selector and confirmed apply, including a stopped STT.
        async with async_playwright() as playwright:
            browser=await playwright.chromium.launch()
            page=await browser.new_page()
            page.on('dialog',lambda dialog:dialog.accept())
            await page.goto('http://127.0.0.1:8770/')
            await page.locator('nav [data-tab="modules"]').click()
            await page.locator('#cards .card').nth(1).get_by_role('button',name='Configurar',exact=True).click()
            await page.locator('#setting-stt-stt_model').select_option('whisper_base')
            await page.locator('#saveRestart').click()
            await page.locator('#configDialog').wait_for(state='hidden',timeout=180000)
            await page.wait_for_function("() => document.querySelectorAll('#cards .card')[1]?.textContent.includes('whisper_base')")
            await browser.close()
        async def wyoming(port):
            async with AsyncClient.from_uri(f'tcp://127.0.0.1:{port}') as client:
                await client.write_event(Describe().event())
                info=Info.from_event(await asyncio.wait_for(client.read_event(),10))
                assert info.asr[0].models[0].name=='arandu-fastconformer-ptbr-int8'
                await client.write_event(Transcribe(name='arandu-fastconformer-ptbr-int8').event())
                await client.write_event(AudioChunk(rate=16000,width=2,channels=1,audio=pcm).event())
                await client.write_event(AudioStop().event())
                response=await asyncio.wait_for(client.read_event(),60)
                assert Transcript.is_type(response.type),response
                assert Transcript.from_event(response).text
        inventory=await (await session.get('http://127.0.0.1:8770/api/stt/models')).json()
        assert len(inventory['models'])==8
        await request('stt/install',{'model':'orukeet_int8'})
        for _ in range(60):
            cached=await (await session.get('http://127.0.0.1:8770/api/stt/models')).json()
            row=next(item for item in cached['models'] if item['id']=='orukeet_int8')
            if row['state']=='installed':break
            assert row['state'] in {'downloading','verifying'},row
            await asyncio.sleep(.5)
        else:raise AssertionError('cached install timeout')
        await wyoming(10350)
        switched=await request('stt/apply',{'model':'orukeet_int8','confirm':True})
        assert switched['active']['model']=='orukeet_int8'
        await wyoming(10212)
        await request('stt/apply',{'model':'whisper_base','confirm':True})
        result,_=await asyncio.gather(request('stt/compare',{'model':'whisper_base','models':list(CATALOG),
                    'audio':base64.b64encode(audio).decode(),'reference':'Liga a luz da sala'}),wyoming(10212))
        installed={'fastconformer','whisper_tiny','whisper_base','whisper_small','orukeet_int8'}
        assert len(result['results'])==8 and result['sequential'] and result['dry_run']
        assert all(row['state']==('ready' if row['model'] in installed else 'error') for row in result['results']),result
        assert len({row['audio_sha256'] for row in result['results']})==1
        # Original variant is deliberately absent in this fixture. Preflight must not disrupt Base.
        await request('stt/apply',{'model':'fastconformer','variant':'original','confirm':True},422)
        await wyoming(10212)
        switched=await request('stt/apply',{'model':'whisper_tiny','confirm':True})
        assert switched['active']['model']=='whisper_tiny'
        switched=await request('stt/apply',{'model':'whisper_base','confirm':True})
        assert switched['active']['model']=='whisper_base'
        await wyoming(10212)
        output=ROOT/'.local-data/stt-validation/extended-linux-result.json'
        output.write_text(json.dumps(result,ensure_ascii=False,indent=2),encoding='utf-8')
        print('PASS: dashboard Base selection, actual apply, 8-model sequential comparison (5 real, 3 absent) + cached install + Orukeet apply, concurrent production, Wyoming 10350/10212, missing-model recovery, Tiny/Base switch')


if __name__=='__main__':
    asyncio.run(main())
