import sys
from pathlib import Path
import unittest

HERE=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(HERE))
from lexical import AranduLexicalResolver


class TestPublicResolver(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.r=AranduLexicalResolver(HERE/'tests'/'fixture_inventory.json', HERE/'resources')

    def full(self, text):
        return self.r.apply(text, 'full')['text']

    def test_basic_ptbr_corrections(self):
        self.assertIn('TV da sala', self.full('liga a teve da sala'))
        self.assertIn('ventilador', self.full('desligua o ventulador').casefold())
        self.assertIn('luz do quarto', self.full('liga nos de quarto').casefold())

    def test_lava_lamp_and_dedup(self):
        self.assertEqual(self.full('liga lava lemp').casefold(), 'liga Lava Lamp'.casefold())
        self.assertEqual(self.full('desliga Lava Lamp lava lpe').casefold(), 'desliga Lava Lamp'.casefold())
        self.assertNotIn('Lava Lamp', self.full('liga lava-louças'))

    def test_unresolved_fragment_survives(self):
        self.assertIn('arcon', self.full('desliga o arcon').casefold())

    def test_long_stream(self):
        out=self.full('liga teve da sala desliga ventlador de rafael')
        self.assertIn('TV da sala'.casefold(), out.casefold())
        self.assertIn('ventilador de Rafael'.casefold(), out.casefold())


if __name__ == '__main__':
    unittest.main()
