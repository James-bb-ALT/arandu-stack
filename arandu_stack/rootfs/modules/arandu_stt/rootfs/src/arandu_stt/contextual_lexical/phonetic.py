"""PT-BR approximate spelling feature, never an acoustic probability."""
from functools import lru_cache
import re
from .normalizer import norm


@lru_cache(maxsize=32768)
def phonetic(word: str) -> str:
    w = norm(word).replace('ph', 'f').replace('th', 't').replace('y', 'i').replace('w', 'v')
    for before, after in (('nh', 'N'), ('lh', 'L'), ('ch', 'X'), ('rr', 'R'), ('ss', 's')):
        w = w.replace(before, after)
    w = re.sub(r'qu(?=[ei])', 'k', w)
    w = re.sub(r'gu(?=[ei])', 'g', w)
    w = re.sub(r'c(?=[ei])', 's', w)
    w = re.sub(r'g(?=[ei])', 'j', w)
    w = w.replace('c', 'k').replace('q', 'k').replace('z', 's')
    w = re.sub(r'^h', '', w)
    w = re.sub(r'o$', 'u', w)
    w = re.sub(r'e$', 'i', w)
    w = re.sub(r'(.)\1+', r'\1', w)
    return re.sub(r'[^a-zA-Z0-9]', '', w).lower()
