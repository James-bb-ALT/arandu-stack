"""Opt-in real previous/current HTTP readers and the production HA client."""
import os
from pathlib import Path
import sys
import unittest

ROOT=Path(__file__).parents[1]
sys.path[:0]=[str(ROOT),str(ROOT/'arandu_stack/rootfs/modules/arandu_nlu/rootfs/src/tests/mlp')]
from test_contextual import hass_,input_
from aiohttp import ClientSession
from custom_components.local_nlu.client import LocalNluClient
from custom_components.local_nlu.contextual_runtime import ContextualRuntime


class Compatibility(unittest.IsolatedAsyncioTestCase):
    @unittest.skipUnless(os.environ.get('ARANDU_PHASE4_ENDPOINT'),'Opt-in previous passive server')
    async def test_old_reader_keeps_direct_commands_and_refuses_new_context(self):
        async with ClientSession() as http:
            client=LocalNluClient(http,os.environ['ARANDU_PHASE4_ENDPOINT'])
            self.assertFalse(await client.async_phase5_supported())
            hass=hass_();runtime=ContextualRuntime(hass,client,lambda:{})
            try:
                result=await runtime.process(input_('Liga a luz da sala'))
                self.assertEqual(result.code,'success',result)
                self.assertFalse(runtime.phase5_supported)
                before=len(hass.services.calls)
                result=await runtime.process(input_('Volta como estava'))
                self.assertEqual(result.reason,'phase5_not_supported')
                self.assertEqual(len(hass.services.calls),before)
            finally:await runtime.async_close()

    @unittest.skipUnless(os.environ.get('ARANDU_NLU_ENDPOINT'),'Opt-in new passive server')
    async def test_new_reader_negotiates_without_changing_diagnostics_schema(self):
        async with ClientSession() as http:
            client=LocalNluClient(http,os.environ['ARANDU_NLU_ENDPOINT'])
            await client.async_health()
            self.assertEqual(set(await client.async_diagnostics()),{'service_version','protocols','build_id','execution'})
            self.assertTrue(await client.async_phase5_supported())
