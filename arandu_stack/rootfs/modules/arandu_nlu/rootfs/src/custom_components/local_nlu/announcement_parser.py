"""Pure deterministic parser for Home Assistant voice announcements.

Zero HA dependencies, zero neural/model inferences.
Preserves original casing and punctuation for spoken text.
"""

from __future__ import annotations

from dataclasses import dataclass
import re
import unicodedata
from typing import Literal

AnnouncementKind = Literal["area", "satellite", "all", "reply", "negation", "conditional", "mixed"]

_MAX_MESSAGE_CHARS = 280
_MAX_MESSAGE_BYTES = 512
_MAX_DESTINATION_CHARS = 100

_INJECTION_PATTERNS = [
    re.compile(r"\{\{.*?\}\}", re.DOTALL),
    re.compile(r"\{%.*?%\}", re.DOTALL),
    re.compile(r"</?[a-zA-Z][^>]*>"),
    re.compile(r"(?:https?|ftp|file|ws|wss)://", re.IGNORECASE),
    re.compile(r"\$\{[^}]*\}"),
    re.compile(r"<%.*?%>"),
]

_BROADCAST_DESTINATIONS = {
    "casa toda",
    "toda a casa",
    "toda casa",
    "casa inteira",
    "todos os comodos",
    "todos comodos",
    "todos os quartos",
    "todas as areas",
    "todas as salas",
    "todos os dispositivos",
    "todos dispositivos",
    "todos os alto falantes",
    "todos os alto-falantes",
    "todos os satelites",
    "todos satelites",
    "todo mundo",
    "todos",
    "geral",
    "broadcast",
}

_SATELLITE_KEYWORDS = {
    "satelite",
    "satélite",
    "alto-falante",
    "alto falante",
    "caixa de som",
    "caixinha",
    "speaker",
    "assistente",
}

_VERB_PATTERNS = [
    r"avisa(?:r|m)?|avise(?:m)?",
    r"fala(?:r|m)?|fale(?:m)?",
    r"anuncia(?:r|m)?|anuncie(?:m)?",
    r"manda(?:r)?\s+(?:um\s+)?recado|mande\s+(?:um\s+)?recado",
    r"diz(?:er)?|diga(?:m)?",
    r"manda(?:r)?\s+(?:uma\s+)?mensagem|mande\s+(?:uma\s+)?mensagem",
]

_PREPOSITIONS = [
    r"na|no|nas|nos|em|pra|pro|pras|pros|para\s+a|para\s+o|para\s+as|para\s+os|para",
]

_SEPARATORS = [
    r"dizendo\s+que",
    r"falando\s+que",
    r"com\s+a\s+mensagem",
    r"que",
    r":",
]

# Action verbs commonly used in domestic commands that signal mixed requests when combined with 'e'
_ACTION_VERBS_PATTERN = r"(?:apaga|apague|liga|ligue|desliga|desligue|fecha|feche|abre|abra|tranca|tranque|destranca|destranque|toca|toque|para|pare|aumenta|diminui|coloca|coloque|executa|execute)"


@dataclass(frozen=True)
class AnnounceIntent:
    kind: AnnouncementKind
    destination: str | None
    message: str | None
    origin_text: str
    requires_confirmation: bool = False
    rejection_reason: str | None = None


def normalize_syntax(text: str) -> str:
    """Normalize text for syntax analysis only. Never used for spoken message output."""
    nfkc = unicodedata.normalize("NFKC", text).casefold()
    # Strip diacritics for robust grammar matching
    nfd = unicodedata.normalize("NFD", nfkc)
    stripped = "".join(c for c in nfd if unicodedata.category(c) != "Mn")
    return " ".join(stripped.split())


def validate_announcement_message(raw_msg: str) -> tuple[str | None, str | None]:
    """Validate message safety and size. Returns (clean_message, error_reason)."""
    if not isinstance(raw_msg, str):
        return None, "invalid_message"
    msg = raw_msg.strip()
    if not msg:
        return None, None

    if len(msg) > _MAX_MESSAGE_CHARS:
        return None, "message_too_long"

    if len(msg.encode("utf-8")) > _MAX_MESSAGE_BYTES:
        return None, "message_too_large"

    if any(ord(c) < 32 and c not in ("\t", "\n") for c in msg):
        return None, "control_characters"

    for pattern in _INJECTION_PATTERNS:
        if pattern.search(msg):
            return None, "injection_detected"

    return msg, None


_validate_message = validate_announcement_message


def parse_announcement(text: str) -> AnnounceIntent | None:
    """Deterministic parser for PT-BR announcement utterances."""
    if not text or not isinstance(text, str):
        return None

    raw_text = text.strip()
    norm_text = normalize_syntax(raw_text)

    # 1. Non-announcement filters: conversational, queries, volume adjustments, reminders
    # e.g., "fala comigo", "me fala a temperatura", "fala mais alto", "anuncia o clima"
    if (
        norm_text.startswith(("fala comigo", "fale comigo", "falar comigo"))
        or norm_text.startswith(("me fala", "me fale", "me diz", "me diga", "me conta", "me conte"))
        or re.match(r"^(?:pode\s+)?fala(?:r)?\s+(?:mais\s+)?(?:alto|baixo)", norm_text)
        or norm_text.startswith(("anuncia o clima", "anuncie o clima", "anuncia as noticias", "anuncie as noticias"))
        or norm_text.startswith(("me lembra", "lembra de", "lembre de"))
    ):
        return None

    # 2. Negation: "não avisa na cozinha que...", "nunca anuncia..."
    if norm_text.startswith(("nao ", "nunca ")):
        sub_text = re.sub(r"^(?:nao|nunca)\s+", "", norm_text)
        sub_raw = re.sub(r"(?i)^(?:não|nao|nunca)\s+", "", raw_text)
        sub_intent = parse_announcement(sub_raw)
        if sub_intent is not None and sub_intent.kind in ("area", "satellite", "all", "reply"):
            return AnnounceIntent(
                kind="negation",
                destination=sub_intent.destination,
                message=sub_intent.message,
                origin_text=raw_text,
                requires_confirmation=False,
                rejection_reason="negation",
            )
        return None

    # 3. Conditional: "se eu chegar anuncia...", "quando eu entrar avisa..."
    if norm_text.startswith(("se ", "quando ", "caso ", "toda vez que ", "sempre que ")):
        # Check if it embeds an announcement
        if re.search(r"\b(?:avisa|avise|anuncia|anuncie|fala|fale|manda\s+recado)\b", norm_text):
            return AnnounceIntent(
                kind="conditional",
                destination=None,
                message=None,
                origin_text=raw_text,
                requires_confirmation=False,
                rejection_reason="conditional_not_supported",
            )
        return None

    # 4. Reply intent: "responde que já vou", "responda que cheguei"
    reply_match = re.match(
        r"^(?:responde|responda|responder|respondem)(?:\s+(?:que|com|dizendo\s+que|:))?\s*(.*)$",
        norm_text,
    )
    if reply_match:
        # Extract message from raw_text matching the tail
        tail_raw_match = re.match(
            r"(?i)^(?:responde|responda|responder|respondem)(?:\s+(?:que|com|dizendo\s+que|:))?\s*(.*)$",
            raw_text,
        )
        raw_msg = tail_raw_match.group(1) if tail_raw_match else ""
        clean_msg, err = _validate_message(raw_msg)
        if err:
            return AnnounceIntent(
                kind="reply",
                destination=None,
                message=None,
                origin_text=raw_text,
                rejection_reason=err,
            )
        return AnnounceIntent(
            kind="reply",
            destination=None,
            message=clean_msg,
            origin_text=raw_text,
            requires_confirmation=False,
        )

    # 5. Mixed command checks:
    # e.g., "avisa na cozinha e apaga a luz" (announcement + action before any message separator)
    # or "apaga a luz e avisa na cozinha que..."
    # Check if utterance begins with action verb and has ' e ' followed by announcement
    if re.match(rf"^{_ACTION_VERBS_PATTERN}\b.*\s+e\s+(?:avisa|avise|fala|fale|anuncia|anuncie)\b", norm_text):
        return AnnounceIntent(
            kind="mixed",
            destination=None,
            message=None,
            origin_text=raw_text,
            rejection_reason="mixed_plan_not_supported",
        )

    # 6. Primary Announcement matching
    # Build regex over normalized syntax to find boundaries, then slice raw_text
    verb_regex = rf"(?:{'|'.join(_VERB_PATTERNS)})"
    prep_regex = rf"(?:{'|'.join(_PREPOSITIONS)})"
    sep_regex = rf"(?:{'|'.join(_SEPARATORS)})"

    # Pattern A: Verb + Destination + Separator + Message
    # e.g. "avisa na cozinha que o almoço está pronto"
    # or "anuncia na sala: a reunião começou"
    pattern_with_sep = re.compile(
        rf"^{verb_regex}\s+{prep_regex}\s+([^:]+?)(?:\s+(?:{sep_regex})\b|:)\s*(.*)$",
        re.DOTALL,
    )

    # Pattern B: Verb + Separator without Prep (e.g., "anuncia que vamos sair" -> broadcast candidate)
    pattern_direct_sep = re.compile(
        rf"^{verb_regex}(?:\s+(?:{sep_regex})\b|:)\s*(.*)$",
        re.DOTALL,
    )

    # Pattern C: Verb + Prep + Destination ONLY (no message)
    # e.g. "avisa na cozinha", "fala no quarto", "anuncia na sala"
    pattern_no_msg = re.compile(
        rf"^{verb_regex}\s+{prep_regex}\s+(.+)$",
    )

    match_a = pattern_with_sep.match(norm_text)
    if match_a:
        dest_norm = match_a.group(1).strip()
        # Find exact separator in raw_text to extract raw destination and raw message
        raw_match = re.search(
            rf"(?i)(?::|\s+(?:{sep_regex})\b)\s*",
            raw_text,
        )
        if raw_match:
            sep_start = raw_match.start()
            sep_end = raw_match.end()
            raw_msg = raw_text[sep_end:].strip()
            # Extract raw destination from before sep_start
            raw_head = raw_text[:sep_start].strip().rstrip(":,")
            raw_dest_match = re.search(
                rf"(?i)^{verb_regex}\s+{prep_regex}\s+(.+)$",
                raw_head,
            )
            raw_dest = raw_dest_match.group(1).strip() if raw_dest_match else dest_norm
        else:
            raw_dest = dest_norm
            raw_msg = ""

        # Check if destination part had a mixed command like "cozinha e apaga a luz"
        if re.search(rf"\s+e\s+{_ACTION_VERBS_PATTERN}\b", dest_norm):
            return AnnounceIntent(
                kind="mixed",
                destination=None,
                message=None,
                origin_text=raw_text,
                rejection_reason="mixed_plan_not_supported",
            )

        if len(raw_dest) > _MAX_DESTINATION_CHARS:
            return AnnounceIntent(
                kind="area",
                destination=None,
                message=None,
                origin_text=raw_text,
                rejection_reason="destination_too_long",
            )

        clean_msg, err = _validate_message(raw_msg)
        if err:
            return AnnounceIntent(
                kind="area",
                destination=raw_dest,
                message=None,
                origin_text=raw_text,
                rejection_reason=err,
            )

        # Categorize destination
        if dest_norm in _BROADCAST_DESTINATIONS:
            return AnnounceIntent(
                kind="all",
                destination=raw_dest,
                message=clean_msg,
                origin_text=raw_text,
                requires_confirmation=True,
            )

        if any(kw in dest_norm for kw in _SATELLITE_KEYWORDS):
            return AnnounceIntent(
                kind="satellite",
                destination=raw_dest,
                message=clean_msg,
                origin_text=raw_text,
                requires_confirmation=False,
            )

        return AnnounceIntent(
            kind="area",
            destination=raw_dest,
            message=clean_msg,
            origin_text=raw_text,
            requires_confirmation=False,
        )

    # Check Pattern C (no message)
    match_c = pattern_no_msg.match(norm_text)
    if match_c:
        dest_norm = match_c.group(1).strip()
        raw_dest_match = re.search(
            rf"(?i)^{verb_regex}\s+{prep_regex}\s+(.+)$",
            raw_text,
        )
        raw_dest = raw_dest_match.group(1).strip() if raw_dest_match else dest_norm

        # Check if destination part had a mixed command like "cozinha e apaga a luz"
        if re.search(rf"\s+e\s+{_ACTION_VERBS_PATTERN}\b", dest_norm):
            return AnnounceIntent(
                kind="mixed",
                destination=None,
                message=None,
                origin_text=raw_text,
                rejection_reason="mixed_plan_not_supported",
            )

        if len(raw_dest) > _MAX_DESTINATION_CHARS:
            return AnnounceIntent(
                kind="area",
                destination=None,
                message=None,
                origin_text=raw_text,
                rejection_reason="destination_too_long",
            )

        if dest_norm in _BROADCAST_DESTINATIONS:
            return AnnounceIntent(
                kind="all",
                destination=raw_dest,
                message=None,
                origin_text=raw_text,
                requires_confirmation=True,
            )

        if any(kw in dest_norm for kw in _SATELLITE_KEYWORDS):
            return AnnounceIntent(
                kind="satellite",
                destination=raw_dest,
                message=None,
                origin_text=raw_text,
                requires_confirmation=False,
            )

        return AnnounceIntent(
            kind="area",
            destination=raw_dest,
            message=None,
            origin_text=raw_text,
            requires_confirmation=False,
        )

    # Check Pattern B (direct separator e.g. "anuncia na casa toda que..." might match A, but what about "avisa que vamos sair"?)
    # If "avisa que...", no destination specified -> broadcast candidate with confirmation
    match_b = pattern_direct_sep.match(norm_text)
    if match_b:
        raw_match = re.search(rf"(?i)(?::|\s+(?:{sep_regex})\b)\s*", raw_text)
        raw_msg = raw_text[raw_match.end():].strip() if raw_match else ""
        clean_msg, err = _validate_message(raw_msg)
        if err:
            return AnnounceIntent(
                kind="all",
                destination="casa toda",
                message=None,
                origin_text=raw_text,
                rejection_reason=err,
            )
        return AnnounceIntent(
            kind="all",
            destination="casa toda",
            message=clean_msg,
            origin_text=raw_text,
            requires_confirmation=True,
        )

    return None
