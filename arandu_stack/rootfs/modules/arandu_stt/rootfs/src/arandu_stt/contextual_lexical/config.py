import json
import math
from pathlib import Path
from .errors import LexicalError

DEFAULTS = {
    'enabled': True, 'closed_vocabulary': True, 'beam_width': 8,
    'candidates_per_token': 12, 'max_index_pool': 160, 'max_tokens': 256,
    'source_weights': {'home_assistant': 10.0, 'generic_lexicon': 7.0,
                       'household': 7.0, 'names': 7.0, 'commands': 7.0, 'functional': 0.0},
    'score_weights': {'orthographic': 0.48, 'phonetic': 0.32, 'edit': 0.20,
                      'source': 0.07, 'frequency': 0.025, 'bigram': 0.13,
                      'trigram': 0.16, 'lookahead': 0.08, 'dictionary': 0.015, 'semantic': 0.10},
    'wordlist': None,
}


def load_config(path: Path | None = None, overrides: dict | None = None) -> dict:
    cfg = {**DEFAULTS, 'source_weights': dict(DEFAULTS['source_weights']),
           'score_weights': dict(DEFAULTS['score_weights'])}
    try:
        supplied = json.loads(path.read_text(encoding='utf-8')) if path else {}
        supplied.update(overrides or {})
        if set(supplied) - set(cfg):
            raise ValueError('Opção lexical desconhecida')
        for key, value in supplied.items():
            if key.endswith('_weights'):
                if not isinstance(value, dict) or set(value) - set(cfg[key]):
                    raise ValueError('Peso desconhecido')
                cfg[key].update(value)
            else:
                cfg[key] = value
        for key, maximum in (('beam_width', 32), ('candidates_per_token', 32),
                             ('max_index_pool', 1024), ('max_tokens', 512)):
            if type(cfg[key]) is not int or not 1 <= cfg[key] <= maximum:
                raise ValueError('Limite inválido: ' + key)
        for key in ('enabled', 'closed_vocabulary'):
            if type(cfg[key]) is not bool:
                raise ValueError('Política inválida: ' + key)
        if cfg['wordlist'] is not None and not isinstance(cfg['wordlist'], str):
            raise ValueError('wordlist deve ser um caminho ou null')
        for group in ('source_weights', 'score_weights'):
            for value in cfg[group].values():
                if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value) or not 0 <= value <= 100:
                    raise ValueError('Peso deve ser finito, entre 0 e 100')
        cfg['source_weights']['functional'] = 0.0
        return cfg
    except (OSError, ValueError, TypeError) as exc:
        raise LexicalError('invalid_config', 'Configuração lexical inválida') from exc
