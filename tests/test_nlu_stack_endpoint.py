"""Manual regressions for local endpoint compatibility; no HA or model required."""
import importlib
from pathlib import Path
import sys
import types
import unittest
from unittest.mock import patch


class StackEndpoint(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        namespace = types.ModuleType('_arandu_client_fixture')
        namespace.__path__ = [str(Path(__file__).parents[1] / 'custom_components/local_nlu')]
        with patch.dict(sys.modules, {'_arandu_client_fixture': namespace}):
            cls.client = importlib.import_module('_arandu_client_fixture.client')

    def test_unified_store_hostname_and_both_service_ports(self):
        for port in (11555, 8099):
            endpoint = f'http://1234abcd-arandu-stack:{port}'
            self.assertEqual(self.client.normalize_endpoint(endpoint), endpoint)

    def test_legacy_and_local_install_remain_compatible(self):
        for host in ('1234abcd-ptbr-nlu', 'local-arandu-stack', 'local-ptbr-nlu'):
            endpoint = f'http://{host}:11555'
            self.assertEqual(self.client.normalize_endpoint(endpoint), endpoint)

    def test_does_not_allow_unrelated_or_malformed_store_names(self):
        for host in ('1234abcd-unrelated', 'not-a-hash-arandu-stack', '1234abcd-arandu-stack.evil.example', '8.8.8.8'):
            with self.assertRaises(self.client.ClientError):
                self.client.normalize_endpoint(f'http://{host}:11555')


if __name__ == '__main__':
    unittest.main()
