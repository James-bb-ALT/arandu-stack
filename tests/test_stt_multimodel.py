"""Model management, real FFmpeg audio and authenticated HA/control contracts."""
import asyncio
import base64
import importlib.util
import io
import json
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
from types import SimpleNamespace, ModuleType
import unittest
from unittest.mock import AsyncMock, Mock, patch
import wave

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT/'arandu_stack/app'))
from modules import ModuleManager, validate_options
from stt_lab import STTLab, decode_audio, SOURCE
import model_catalog as catalog
from stt_processing import correct, evaluate


def wav(seconds=.5, rate=44100, channels=2):
    output = io.BytesIO()
    with wave.open(output, 'wb') as file:
        file.setnchannels(channels)
        file.setsampwidth(2)
        file.setframerate(rate)
        file.writeframes(b'\0\0' * int(rate*seconds)*channels)
    return output.getvalue()


class Configuration(unittest.IsolatedAsyncioTestCase):
    async def test_legacy_variant_migrates_and_selection_survives_reload(self):
        for variant in ('adapted', 'original'):
            with tempfile.TemporaryDirectory() as folder:
                root=Path(folder)
                (root/'modules.json').write_text(json.dumps({'version':2,'modules':{'stt':{'model_variant':variant}}}))
                manager=ModuleManager(root,local=True)
                self.assertEqual(manager.config['stt']['stt_model'],'fastconformer')
                self.assertEqual(manager.config['stt']['model_variant'],variant)
                await manager.configure('stt',{'stt_model':'whisper_base'})
                self.assertEqual(ModuleManager(root,local=True).config['stt']['stt_model'],'whisper_base')
        with self.assertRaises(ValueError):
            validate_options('stt',{'stt_model':'../../arbitrary'})

    async def test_apply_requires_confirmation_and_restores_previous_on_failure(self):
        with tempfile.TemporaryDirectory() as folder:
            manager=ModuleManager(Path(folder),local=True)
            manager.stop=AsyncMock()
            manager.start=AsyncMock(side_effect=ValueError('fixture load failure'))
            lab=STTLab(manager)
            with self.assertRaisesRegex(ValueError,'Confirme'):
                await lab.apply({'model':'whisper_base'})
            before=dict(manager.config['stt'])
            with patch.object(lab,'worker',AsyncMock(return_value={'state':'ready'})):
                with self.assertRaisesRegex(ValueError,'fixture'):
                    await lab.apply({'model':'whisper_base','confirm':True})
            self.assertEqual(manager.config['stt'],before)


    async def test_running_service_is_restarted_with_previous_config_after_failed_switch(self):
        with tempfile.TemporaryDirectory() as folder:
            manager=ModuleManager(Path(folder),local=True)
            manager.config['stt']['enabled']=True
            manager.stop=AsyncMock()
            manager.start=AsyncMock(side_effect=[ValueError('fixture startup failed'),None])
            lab=STTLab(manager)
            with patch.object(lab,'active',AsyncMock(return_value=None)),patch.object(lab,'worker',AsyncMock(return_value={'state':'ready'})):
                with self.assertRaisesRegex(ValueError,'fixture'):
                    await lab.apply({'model':'whisper_base','confirm':True})
            self.assertEqual(manager.config['stt']['stt_model'],'orukeet_int8')
            self.assertTrue(manager.config['stt']['enabled'])
            self.assertEqual(manager.start.await_count,2)


class Installation(unittest.TestCase):
    def test_atomic_download_pins_revision_and_offline_cache_checks_integrity(self):
        fake=ModuleType('huggingface_hub')
        fake.HfApi=lambda **kwargs:SimpleNamespace(model_info=lambda **kw:SimpleNamespace(sha='a'*40))
        def download(**kwargs):
            self.assertEqual(kwargs['revision'],'a'*40)
            self.assertIs(kwargs['token'],False)
            self.assertEqual(set(kwargs['allow_patterns']),set(catalog.WHISPER_FILES))
            for name in catalog.WHISPER_FILES:
                (Path(kwargs['local_dir'])/name).write_bytes(b'fixture '+name.encode())
        fake.snapshot_download=Mock(side_effect=download)
        with tempfile.TemporaryDirectory() as folder, patch.dict(sys.modules,{'huggingface_hub':fake}):
            root=Path(folder)
            installed=catalog.install(root,'whisper_base')
            self.assertEqual(installed['revision'],'a'*40)
            fake.HfApi=Mock(side_effect=AssertionError('offline must not access network'))
            self.assertEqual(catalog.install(root,'whisper_base'),installed)
            directory=catalog.location(root,'whisper_base')
            (directory/'model.bin').write_bytes(b'corrupted fixture')
            with self.assertRaises(ValueError):
                catalog.verify(root,'whisper_base')
            with self.assertRaises(AssertionError):
                catalog.install(root,'whisper_base')
            self.assertEqual((directory/'model.bin').read_bytes(),b'corrupted fixture')
            self.assertEqual(list(root.glob('.staging-*')),[])


@unittest.skipUnless(shutil.which('ffmpeg'),'FFmpeg required for audio integration')
class Audio(unittest.IsolatedAsyncioTestCase):
    async def test_real_wav_conversion_and_duration(self):
        pcm,elapsed=await decode_audio(wav())
        self.assertEqual(len(pcm),16000)
        self.assertGreaterEqual(elapsed,0)

    async def test_mediarecorder_containers_are_decoded_not_treated_as_pcm(self):
        for suffix,codec in [('webm','libopus'),('m4a','aac'),('ogg','libopus')]:
            with tempfile.TemporaryDirectory() as folder:
                source=Path(folder)/'input.wav';source.write_bytes(wav())
                target=Path(folder)/('input.'+suffix)
                subprocess.run(['ffmpeg','-v','error','-i',str(source),'-c:a',codec,str(target)],check=True)
                pcm,_=await decode_audio(target.read_bytes())
                self.assertLess(abs(len(pcm)-16000),2000)

    async def test_malformed_empty_oversize_and_long_inputs_are_rejected(self):
        for value in (b'', b'garbage', b'RIFFbroken', b'x'*1_400_001, wav(12.5,16000,1)):
            with self.assertRaises(ValueError):
                await decode_audio(value)

    async def test_decoder_timeout_kills_child(self):
        process=SimpleNamespace(returncode=None,kill=Mock(),wait=AsyncMock(),communicate=AsyncMock())
        async def stalled():
            await asyncio.sleep(60)
        process.communicate=stalled
        original=asyncio.wait_for
        async def short_wait(awaitable,timeout):
            return await original(awaitable,.001)
        with patch('stt_lab.asyncio.create_subprocess_exec',AsyncMock(return_value=process)),patch('stt_lab.asyncio.wait_for',short_wait):
            with self.assertRaises(TimeoutError):
                await decode_audio(wav())
        process.kill.assert_called_once()


class Comparison(unittest.IsolatedAsyncioTestCase):
    async def test_same_decoded_audio_sequential_without_production_changes(self):
        with tempfile.TemporaryDirectory() as folder:
            manager=ModuleManager(Path(folder),local=True)
            before=json.dumps(manager.config,sort_keys=True)
            lab=STTLab(manager)
            running=0
            inputs=[]
            async def infer(body,timeout=90):
                nonlocal running
                running+=1
                self.assertEqual(running,1)
                inputs.append(body['pcm'])
                await asyncio.sleep(0)
                running-=1
                return {'model':body['model'],'state':'ready','raw_transcript':'teste','final_transcript':'teste'}
            with patch.object(lab,'worker',side_effect=infer),patch('stt_lab.decode_audio',AsyncMock(return_value=(b'\0\0'*8000,1))):
                result=await lab.test({'audio':base64.b64encode(wav()).decode(),'models':list(catalog.CATALOG)},True)
            self.assertEqual(len(result['results']),len(catalog.CATALOG))
            self.assertEqual(len(set(inputs)),1)
            self.assertEqual(json.dumps(manager.config,sort_keys=True),before)
            self.assertTrue(result['dry_run'])

    async def test_busy_requests_fail_without_growing_a_queue(self):
        with tempfile.TemporaryDirectory() as folder:
            lab=STTLab(ModuleManager(Path(folder),local=True))
            async with lab.busy:
                with self.assertRaisesRegex(ValueError,'ocupado'):
                    await lab.test({'audio':base64.b64encode(wav()).decode()})


class LexicalSafety(unittest.TestCase):
    def test_negation_and_action_cannot_be_rewritten(self):
        resolver=SimpleNamespace(apply=lambda raw,profile:{'text':'liga a luz','changes':[1]})
        self.assertEqual(correct(resolver,'não desliga a luz','full')['text'],'não desliga a luz')
        self.assertEqual(correct(resolver,'desliga a luz','full')['text'],'desliga a luz')
        self.assertFalse(evaluate('','teste','teste')['evaluated'])
        result=evaluate('Não desliga a luz','Desliga a luz','Não desliga a luz')
        self.assertGreater(result['wer_raw'],0)
        self.assertEqual(result['wer_final'],0)
        self.assertFalse(result['negation_preserved_raw'])


class HomeAssistant(unittest.IsolatedAsyncioTestCase):
    async def test_admin_and_configured_stack_endpoint_only(self):
        from custom_components.local_nlu import ai_lab
        ha=ModuleType('homeassistant.helpers.aiohttp_client')
        ha.async_get_clientsession=lambda hass:object()
        entry=SimpleNamespace(entry_id='fixture',options={'stack_endpoint':'http://local-arandu-stack:8099'},data={})
        hass=SimpleNamespace(config_entries=SimpleNamespace(async_entries=lambda domain:[entry]))
        lab=ai_lab.Lab(hass)
        for user in (None,SimpleNamespace(is_active=True,is_admin=False)):
            with self.assertRaisesRegex(ValueError,'administrador'):
                await lab.handle(user,{'command':'stt_test'})
        admin=SimpleNamespace(is_active=True,is_admin=True)
        with patch.dict(sys.modules,{'homeassistant.helpers.aiohttp_client':ha}),patch.object(ai_lab,'local_request',AsyncMock(return_value={'results':[]})) as request:
            await lab.handle(admin,{'command':'stt_compare','stt_model':'whisper_base','models':list(catalog.CATALOG),
                'audio':base64.b64encode(wav()).decode(),'stt_endpoint':'http://8.8.8.8:8765'})
            self.assertEqual(request.call_args.args[1:3],('http://local-arandu-stack:8099','/api/stt/compare'))
            self.assertEqual(request.call_args.kwargs['payload']['models'],list(catalog.CATALOG))


    async def test_sidebar_registration_includes_native_module_and_blocks_nonadmin_socket(self):
        from custom_components.local_nlu import ai_lab
        registered=[]
        websocket=SimpleNamespace(websocket_command=lambda schema:lambda fn:fn,async_response=lambda fn:fn,
            async_register_command=lambda hass,fn:registered.append(fn))
        panels=SimpleNamespace(async_register_panel=AsyncMock())
        components=ModuleType('homeassistant.components')
        components.panel_custom=panels;components.websocket_api=websocket
        http=ModuleType('homeassistant.components.http')
        http.StaticPathConfig=lambda url,path,cache:SimpleNamespace(url=url,path=path,cache=cache)
        hass=SimpleNamespace(data={},http=SimpleNamespace(async_register_static_paths=AsyncMock()))
        with patch.dict(sys.modules,{'homeassistant.components':components,'homeassistant.components.http':http}):
            await ai_lab.async_register(hass)
        self.assertTrue(panels.async_register_panel.call_args.kwargs['require_admin'])
        paths=hass.http.async_register_static_paths.call_args.args[0]
        self.assertIn('/local_nlu_ai_lab/stt.js',[row.url for row in paths])
        connection=SimpleNamespace(user=SimpleNamespace(is_active=True,is_admin=False),send_error=Mock(),send_result=Mock())
        await registered[0](hass,connection,{'id':1,'payload':{'command':'stt_test'}})
        connection.send_error.assert_called_once()
        connection.send_result.assert_not_called()


class ControlBoundary(unittest.IsolatedAsyncioTestCase):
    async def test_stt_routes_preserve_csrf_and_internal_origin_checks(self):
        from aiohttp.test_utils import TestClient,TestServer
        spec=importlib.util.spec_from_file_location('stt_control_fixture',ROOT/'arandu_stack/app/server.py')
        control=importlib.util.module_from_spec(spec);spec.loader.exec_module(control)
        with tempfile.TemporaryDirectory() as folder,patch.object(control.LogStore,'configure_logging'):
            app=control.create_app(Path(folder),local=True)
            app.on_startup.clear();app.on_cleanup.clear()
            client=TestClient(TestServer(app));await client.start_server()
            try:
                headers={'Host':'127.0.0.1:8770'}
                rejected=await client.post('/api/stt/test',headers=headers,json={})
                self.assertEqual(rejected.status,403)
                rejected=await client.post('/api/stt/test',headers={**headers,'X-Arandu-Lab':'1','Origin':'https://evil.example'},json={})
                self.assertEqual(rejected.status,403)
                with patch.object(app['stt_lab'],'test',AsyncMock(return_value={'results':[],'dry_run':True})):
                    accepted=await client.post('/api/stt/test',headers={**headers,'X-Arandu-Lab':'1'},json={'model':'whisper_base'})
                    self.assertEqual(accepted.status,200)
                    state=await (await client.get('/api/state',headers=headers)).json()
                    accepted=await client.post('/api/stt/test',headers={**headers,'X-Arandu-CSRF':state['csrf']},json={'model':'whisper_base'})
                    self.assertEqual(accepted.status,200)
            finally:
                await client.close()
