"""Constants for the Local NLU companion integration."""

DOMAIN = "local_nlu"
PLATFORMS = ("conversation",)

CONF_ENDPOINT = "endpoint"
CONF_V2_ENABLED = "v2_enabled"
CONF_CONTEXTUAL_ENABLED = "contextual_enabled"
CONF_FALLBACK_USER_ID = "fallback_user_id"
CONF_VOICE_IDENTITY_BINDINGS = "voice_identity_bindings"
CONF_DEVICE_IDENTITY_BINDINGS = "device_identity_bindings"
CONF_SHADOW_ENABLED = "shadow_enabled"
CONF_DECISION_ENABLED = "decision_enabled"
CONF_LLM_ENABLED = "llm_enabled"
DEFAULT_ENDPOINT = "http://local-arandu-stack:11555"

PROTOCOL_VERSION = 1
RESOLVE_VERSION = 2
ER_CATALOG_ID = "ha-entity-resolution-v1"
MAX_REQUEST_BYTES = 65_536
MAX_RESPONSE_BYTES = 65_536
MAX_CATALOG_ENTITIES = 1_024
MAX_CATALOG_AREAS = 256
MAX_NAMES = 8
MAX_NAME_BYTES = 128
MAX_STATE_BYTES = 256
MAX_UNIT_BYTES = 64
MAX_QUERY_RESPONSE_BYTES = 8_192
REQUEST_TIMEOUT_SECONDS = 3.0

SUPPORTED_DOMAINS = frozenset(
    ("binary_sensor", "fan", "light", "sensor", "switch")
)
EFFECT_DOMAINS = frozenset(("fan", "light", "switch"))

# Home Assistant fan.FanEntityFeature values in the supported public contract.
FAN_FEATURE_SET_SPEED = 1
FAN_FEATURE_TURN_OFF = 16
FAN_FEATURE_TURN_ON = 32
