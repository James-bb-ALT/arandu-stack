"""Text spans and explicit numeric vocabulary; punctuation is never guessed."""
from dataclasses import dataclass
from functools import lru_cache
import re
import unicodedata

from .errors import LexicalError

PLACEHOLDERS = re.compile(r"\{[^{}]*\}")
TOKEN = re.compile(r"[+-]?\d{1,3}:\d{2}|[+-]?\d+(?:[.,]\d+)?(?:%|°[CcFf]?)?|°[CcFf]?|[^\W_]+(?:[-'][^\W_]+)*", re.UNICODE)
PUNCTUATION = re.compile(r"^[\s.,!?;:\"'“”‘’()\[\]…–—]*$")
NUMBER = re.compile(r"^[+-]?\d{1,12}(?:[.,]\d{1,6})?(?:%|°[CcFf]?)?$")
CLOCK = re.compile(r"^(\d{1,2}):(\d{2})$")


@lru_cache(maxsize=32768)
def norm(text: str) -> str:
    return ''.join(c for c in unicodedata.normalize('NFD', text.casefold())
                   if unicodedata.category(c) != 'Mn')


@dataclass(frozen=True)
class Span:
    original: str
    start: int
    end: int


def spans(text: str) -> list[Span]:
    text = unicodedata.normalize('NFC', text).replace('’', "'")
    for clock in re.finditer(r'(?<!\w)\d+:\d+(?!\w)', text):
        if not dynamic_allowed(clock.group()):
            raise LexicalError('invalid_time', 'Horário fora do intervalo 00:00–23:59')
    result = [Span(m.group(), m.start(), m.end()) for m in TOKEN.finditer(text)]
    cursor = 0
    for item in result:
        if not PUNCTUATION.fullmatch(text[cursor:item.start]):
            raise LexicalError('unsupported_text', 'Símbolo fora das regras de vocabulário permitido')
        if ':' in item.original and not dynamic_allowed(item.original):
            raise LexicalError('invalid_time', 'Horário fora do intervalo 00:00–23:59')
        cursor = item.end
    if not PUNCTUATION.fullmatch(text[cursor:]):
        raise LexicalError('unsupported_text', 'Símbolo fora das regras de vocabulário permitido')
    return result


def dynamic_allowed(token: str) -> bool:
    clock = CLOCK.fullmatch(token)
    if clock:
        return int(clock[1]) < 24 and int(clock[2]) < 60
    return bool(NUMBER.fullmatch(token) or re.fullmatch(r'°[CcFf]?', token))


def words(text: str) -> list[str]:
    return TOKEN.findall(PLACEHOLDERS.sub(' ', text))
