"""Manual discovery-flow regression with fake HA and no inference/network."""
import importlib
from pathlib import Path
import sys
import types
import unittest
from unittest.mock import AsyncMock, Mock, patch


class FakeFlow:
    def __init_subclass__(cls, **kwargs):
        pass
    def __init__(self):
        self.context, self.entries, self.hass = {}, [], Mock()
    def _async_current_entries(self):
        return self.entries
    async def async_set_unique_id(self, value):
        self.unique_id = value
    def _abort_if_unique_id_configured(self):
        pass
    def async_abort(self, **kwargs):
        return {'type': 'abort', **kwargs}
    def async_show_form(self, **kwargs):
        return {'type': 'form', **kwargs}
    def async_create_entry(self, **kwargs):
        return {'type': 'create_entry', **kwargs}


class NluDiscoveryFlow(unittest.IsolatedAsyncioTestCase):
    @classmethod
    def setUpClass(cls):
        namespace = types.ModuleType('_arandu_flow_fixture')
        namespace.__path__ = [str(Path(__file__).parents[1] / 'custom_components/local_nlu')]
        entries = types.ModuleType('homeassistant.config_entries')
        entries.ConfigFlow = entries.OptionsFlow = FakeFlow
        core = types.ModuleType('homeassistant.core')
        core.callback = lambda function: function
        aiohttp = types.ModuleType('homeassistant.helpers.aiohttp_client')
        aiohttp.async_get_clientsession = lambda hass: Mock()
        ha = types.ModuleType('homeassistant')
        ha.config_entries = entries
        helpers = types.ModuleType('homeassistant.helpers')
        helpers.selector = types.ModuleType('homeassistant.helpers.selector')
        vol = types.ModuleType('voluptuous')
        vol.Schema = lambda value: value
        modules = {'_arandu_flow_fixture': namespace, 'homeassistant': ha,
                   'homeassistant.config_entries': entries, 'homeassistant.core': core,
                   'homeassistant.helpers': helpers, 'homeassistant.helpers.selector': helpers.selector,
                   'homeassistant.helpers.aiohttp_client': aiohttp, 'voluptuous': vol}
        with patch.dict(sys.modules, modules):
            cls.module = importlib.import_module('_arandu_flow_fixture.config_flow')

    async def test_supervisor_discovery_offers_confirmation_with_filled_addresses(self):
        flow = self.module.LocalNluConfigFlow()
        info = types.SimpleNamespace(slug='abcd1234_arandu_stack', config={'host': 'abcd1234-arandu-stack'})
        result = await flow.async_step_hassio(info)
        self.assertEqual(result['step_id'], 'hassio_confirm')
        self.assertEqual(flow.unique_id, 'local_nlu')
        with patch.object(self.module.LocalNluClient, 'async_health', AsyncMock()):
            result = await flow.async_step_hassio_confirm({})
        self.assertEqual(result['data'], {'endpoint': 'http://abcd1234-arandu-stack:11555', 'stack_endpoint': 'http://abcd1234-arandu-stack:8099'})

    async def test_existing_entry_is_never_duplicated(self):
        flow = self.module.LocalNluConfigFlow()
        flow.entries = [Mock(entry_id='keep-existing')]
        result = await flow.async_step_hassio(Mock())
        self.assertEqual(result, {'type': 'abort', 'reason': 'already_configured'})

    async def test_unrelated_app_discovery_is_rejected(self):
        flow = self.module.LocalNluConfigFlow()
        result = await flow.async_step_hassio(types.SimpleNamespace(slug='abcd1234_unrelated', config={}))
        self.assertEqual(result['reason'], 'invalid_discovery')


if __name__ == '__main__':
    unittest.main()
