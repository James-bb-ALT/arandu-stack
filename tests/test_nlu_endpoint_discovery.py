"""Manual migration regressions; mocked metadata and health, no HA or inference."""
import importlib
from pathlib import Path
import sys
import types
import unittest
from unittest.mock import AsyncMock, Mock, patch


class EndpointDiscovery(unittest.IsolatedAsyncioTestCase):
    @classmethod
    def setUpClass(cls):
        namespace = types.ModuleType('_arandu_discovery_fixture')
        namespace.__path__ = [str(Path(__file__).parents[1] / 'custom_components/local_nlu')]
        with patch.dict(sys.modules, {'_arandu_discovery_fixture': namespace}):
            cls.module = importlib.import_module('_arandu_discovery_fixture.endpoint_discovery')

    def test_actual_installed_slug_becomes_both_endpoints(self):
        self.assertEqual(self.module.from_addons({'abcd1234_arandu_stack': {}}),
                         ('http://abcd1234-arandu-stack:11555', 'http://abcd1234-arandu-stack:8099'))

    def test_multiple_stacks_are_not_guessed(self):
        with self.assertRaises(self.module.ClientError):
            self.module.from_addons({'abcd1234_arandu_stack': {}, 'local_arandu_stack': {}})

    async def test_blank_options_are_filled_without_losing_identity_or_policies(self):
        entry = types.SimpleNamespace(data={'endpoint': '', 'other': 'keep'},
                                      options={'endpoint': '', 'fallback_user_id': 'fixture-user'},
                                      title='Arandu NLU', entry_id='fixture-entry')
        hass = Mock()
        def update(entry, **kwargs):
            for name, value in kwargs.items():
                setattr(entry, name, value)
        hass.config_entries.async_update_entry.side_effect = update
        health = AsyncMock()
        with patch.object(self.module, 'installed_endpoints', return_value=('http://abcd1234-arandu-stack:11555', 'http://abcd1234-arandu-stack:8099')), patch.object(self.module.LocalNluClient, 'async_health', health):
            await self.module.prepare_entry(hass, entry, Mock())
            await self.module.prepare_entry(hass, entry, Mock())
        self.assertEqual(entry.entry_id, 'fixture-entry')
        self.assertEqual(entry.title, 'Arandu Assistant')
        self.assertEqual(entry.data['other'], 'keep')
        self.assertEqual(entry.options['fallback_user_id'], 'fixture-user')
        self.assertEqual(entry.options['endpoint'], 'http://abcd1234-arandu-stack:11555')
        self.assertEqual(entry.data['stack_endpoint'], 'http://abcd1234-arandu-stack:8099')
        self.assertEqual(entry.data['endpoint_migration_backup']['endpoint']['options'], '')
        self.assertEqual(hass.config_entries.async_update_entry.call_count, 1)
        health.assert_awaited_once()

    async def test_explicit_custom_address_is_preserved(self):
        with patch.object(self.module, 'installed_endpoints') as discovery:
            result = await self.module.resolve_endpoints(Mock(), Mock(), 'http://192.168.1.2:11555')
        self.assertEqual(result, ('http://192.168.1.2:11555', '', False))
        discovery.assert_not_called()

    async def test_failed_health_never_saves_discovered_endpoint(self):
        entry = types.SimpleNamespace(data={'endpoint': ''}, options={}, title='Arandu NLU')
        hass = Mock()
        with patch.object(self.module, 'installed_endpoints', return_value=('http://local-arandu-stack:11555', 'http://local-arandu-stack:8099')), patch.object(self.module.LocalNluClient, 'async_health', AsyncMock(side_effect=self.module.ClientError('transport'))):
            with self.assertRaises(self.module.ClientError):
                await self.module.prepare_entry(hass, entry, Mock())
        hass.config_entries.async_update_entry.assert_not_called()

    async def test_unreachable_legacy_endpoint_can_migrate_after_new_health_check(self):
        health = AsyncMock(side_effect=[self.module.ClientError('transport'), None])
        expected = ('http://abcd1234-arandu-stack:11555', 'http://abcd1234-arandu-stack:8099', True)
        with patch.object(self.module, 'installed_endpoints', return_value=expected[:2]), patch.object(self.module.LocalNluClient, 'async_health', health):
            result = await self.module.resolve_endpoints(Mock(), Mock(), 'http://local-ptbr-nlu:11555')
        self.assertEqual(result, expected)
        self.assertEqual(health.await_count, 2)


if __name__ == '__main__':
    unittest.main()
