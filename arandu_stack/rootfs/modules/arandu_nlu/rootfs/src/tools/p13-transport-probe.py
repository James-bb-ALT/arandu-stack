#!/usr/bin/env python3.14
"""Exact CPython/OpenSSL TLS 1.3 external-PSK admission probe."""

from __future__ import annotations

import _ssl
import hashlib
import platform
import ssl
import struct
import sys
import time
from dataclasses import dataclass, field
from pathlib import Path


class ProbeFailure(RuntimeError):
    """A deterministic transport-profile assertion failed."""


FIXTURE_TECNICA_IDENTITY = "FIXTURE_TECNICA_P13_CLIENT_IDENTITY"
FIXTURE_TECNICA_WRONG_IDENTITY = "FIXTURE_TECNICA_P13_WRONG_IDENTITY"
FIXTURE_TECNICA_ALPN = "FIXTURE_TECNICA_P13_PSK_DHE_1"
FIXTURE_TECNICA_WRONG_ALPN = "FIXTURE_TECNICA_P13_WRONG_ALPN"
FIXTURE_TECNICA_PSK = hashlib.sha256(
    b"FIXTURE_TECNICA_P13_EXTERNAL_PSK"
).digest()
FIXTURE_TECNICA_WRONG_PSK = hashlib.sha256(
    b"FIXTURE_TECNICA_P13_WRONG_EXTERNAL_PSK"
).digest()
FIXTURE_TECNICA_APPLICATION_DATA = b"FIXTURE_TECNICA_P13_CHANNEL_PROBE"
FIXTURE_TECNICA_APPLICATION_ACK = b"FIXTURE_TECNICA_P13_CHANNEL_ACK"

MAX_HANDSHAKE_STEPS = 256
MAX_HANDSHAKE_BYTES = 1_048_576
MAX_HANDSHAKE_SECONDS = 2.0

TLS_HANDSHAKE = 22
CLIENT_HELLO = 1
SERVER_HELLO = 2
NEW_SESSION_TICKET = 4
END_OF_EARLY_DATA = 5
CERTIFICATE = 11
CERTIFICATE_REQUEST = 13
CERTIFICATE_VERIFY = 15
EXT_PRE_SHARED_KEY = 41
EXT_EARLY_DATA = 42
EXT_PSK_KEY_EXCHANGE_MODES = 45
EXT_KEY_SHARE = 51

CPYTHON_CERT_FIXTURE = Path(
    "/private/tmp/Python-3.14.6/Lib/test/certdata/keycert.pem"
)
CPYTHON_CERT_FIXTURE_SHA256 = (
    "7b5b8ea34c11ccb76e0b10e8ab01252d7f835a4f3e7c8bfe93d1c43b0b133fda"
)

HOMEBREW_HASHES = {
    Path("/opt/homebrew/bin/python3.14"):
        "4f00ea2ad53d62437a6a3946b73c73614a97e8accdc5b96dc095ea1a0d9c6a56",
    Path(
        "/opt/homebrew/Cellar/python@3.14/3.14.6/Frameworks/"
        "Python.framework/Versions/3.14/Python"
    ): "15436055aa2c02ed0218ac0e02b3a27a92102f88cd59ab5094896b9306317336",
    Path(
        "/opt/homebrew/Cellar/python@3.14/3.14.6/Frameworks/"
        "Python.framework/Versions/3.14/lib/python3.14/lib-dynload/"
        "_ssl.cpython-314-darwin.so"
    ): "791e62e4a25231581b1ac0f1ac4cb66b64da1ec3ed13959b177c2422856fe8de",
    Path("/opt/homebrew/Cellar/openssl@3/3.6.3/lib/libssl.3.dylib"):
        "ffd8ac6981000def0928367924b6cb1e7a98712efbc06e2a2f3f750138bd89ca",
    Path("/opt/homebrew/Cellar/openssl@3/3.6.3/lib/libcrypto.3.dylib"):
        "a12805a18cd5e4f733fa8727b91afa08b587f9da5a760517cd79cb508a3a3f71",
}

SOURCE_BUILD_HASHES = {
    Path("/private/tmp/p13-python-install/bin/python3.14"):
        "f7147fb3d4be5bfe55816013a05f9b206a9ad440b7084d56957a40372aa0d7a1",
    Path(
        "/private/tmp/p13-python-install/lib/python3.14/lib-dynload/"
        "_ssl.cpython-314-darwin.so"
    ): "8e266297d8811b2bfb6cafe7cfb31deed6aac3af4d3ece71efed5e09ffc6fbef",
    Path("/private/tmp/p13-openssl-install/lib/libssl.3.dylib"):
        "489ea0e86d8eb1be81919ec12cd15ca8de8d10942a0a91d7eb520bbbbce7aa05",
    Path("/private/tmp/p13-openssl-install/lib/libcrypto.3.dylib"):
        "567bddef1ceb4deb1ba222bc5e105cbd20820dd71030ce108e04c94a5b4b0f22",
}


@dataclass
class CaseConfig:
    """One bounded handshake configuration."""

    client_identity: str = FIXTURE_TECNICA_IDENTITY
    client_key: bytes = FIXTURE_TECNICA_PSK
    server_key: bytes = FIXTURE_TECNICA_PSK
    client_callback: bool = True
    server_callback: bool = True
    client_alpn: str = FIXTURE_TECNICA_ALPN
    server_alpn: str = FIXTURE_TECNICA_ALPN
    client_minimum: ssl.TLSVersion = ssl.TLSVersion.TLSv1_3
    client_maximum: ssl.TLSVersion = ssl.TLSVersion.TLSv1_3
    server_minimum: ssl.TLSVersion = ssl.TLSVersion.TLSv1_3
    server_maximum: ssl.TLSVersion = ssl.TLSVersion.TLSv1_3
    load_server_certificate: bool = False
    server_num_tickets: int = 0


@dataclass
class CallbackFacts:
    """PSK callback observations without retaining callback buffers."""

    client_hints: list[str | None] = field(default_factory=list)
    server_identities: list[str | None] = field(default_factory=list)


@dataclass
class HandshakeResult:
    """Bounded raw TLS handshake result."""

    success: bool
    client: ssl.SSLObject | None
    server: ssl.SSLObject | None
    client_context: ssl.SSLContext
    server_context: ssl.SSLContext
    callback_facts: CallbackFacts
    trace: list[tuple[int, int, bytes]]
    failure_kind: str | None = None
    steps: int = 0
    transferred: int = 0


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def verify_runtime() -> str:
    if platform.python_version() != "3.14.6":
        raise ProbeFailure("runtime CPython version differs")
    if platform.machine() != "arm64":
        raise ProbeFailure("runtime architecture differs")
    if ssl.OPENSSL_VERSION != "OpenSSL 3.6.3 9 Jun 2026":
        raise ProbeFailure("runtime OpenSSL version differs")
    if not ssl.HAS_PSK:
        raise ProbeFailure("runtime PSK support is absent")
    for name in ("set_psk_client_callback", "set_psk_server_callback"):
        if not hasattr(ssl.SSLContext, name):
            raise ProbeFailure(f"runtime API is absent: {name}")

    ssl_path = Path(_ssl.__file__).resolve()
    if str(ssl_path).startswith("/opt/homebrew/"):
        profile = "homebrew"
        expected = HOMEBREW_HASHES
    elif str(ssl_path).startswith("/private/tmp/p13-python-install/"):
        profile = "source_build"
        expected = SOURCE_BUILD_HASHES
    else:
        raise ProbeFailure("runtime is outside both exact admitted local profiles")

    for path, expected_hash in expected.items():
        if not path.is_file() or sha256(path) != expected_hash:
            raise ProbeFailure(f"runtime file identity differs: {path}")
    return profile


def verify_certificate_fixture() -> None:
    if (
        not CPYTHON_CERT_FIXTURE.is_file()
        or sha256(CPYTHON_CERT_FIXTURE) != CPYTHON_CERT_FIXTURE_SHA256
    ):
        raise ProbeFailure("upstream CPython certificate fixture differs")


def make_contexts(
    config: CaseConfig,
) -> tuple[ssl.SSLContext, ssl.SSLContext, CallbackFacts, list]:
    facts = CallbackFacts()
    trace: list[tuple[int, int, bytes]] = []

    client = ssl.SSLContext(ssl.PROTOCOL_TLS_CLIENT)
    client.check_hostname = False
    client.verify_mode = ssl.CERT_NONE
    client.minimum_version = config.client_minimum
    client.maximum_version = config.client_maximum
    client.options |= ssl.OP_NO_TICKET
    client.set_alpn_protocols([config.client_alpn])

    server = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
    server.minimum_version = config.server_minimum
    server.maximum_version = config.server_maximum
    server.options |= ssl.OP_NO_TICKET
    server.num_tickets = config.server_num_tickets
    server.set_alpn_protocols([config.server_alpn])

    if config.client_maximum <= ssl.TLSVersion.TLSv1_2:
        client.set_ciphers("PSK")
    if config.server_maximum <= ssl.TLSVersion.TLSv1_2:
        server.set_ciphers("PSK")

    if config.client_callback:
        def client_callback(
            hint: str | None,
        ) -> tuple[str, bytes]:
            facts.client_hints.append(hint)
            return config.client_identity, config.client_key

        client.set_psk_client_callback(client_callback)

    if config.server_callback:
        def server_callback(identity: str | None) -> bytes:
            facts.server_identities.append(identity)
            if identity != FIXTURE_TECNICA_IDENTITY:
                return b""
            return config.server_key

        server.set_psk_server_callback(server_callback)

    if config.load_server_certificate:
        verify_certificate_fixture()
        server.load_cert_chain(CPYTHON_CERT_FIXTURE)

    def message_callback(
        _connection: ssl.SSLObject,
        _direction: str,
        _version: ssl.TLSVersion,
        content_type: int,
        message_type: int,
        data: bytes,
    ) -> None:
        if int(content_type) == TLS_HANDSHAKE:
            trace.append((int(content_type), int(message_type), bytes(data)))

    client._msg_callback = message_callback
    return client, server, facts, trace


def transfer(source: ssl.MemoryBIO, destination: ssl.MemoryBIO) -> int:
    transferred = 0
    while source.pending:
        chunk = source.read()
        destination.write(chunk)
        transferred += len(chunk)
    return transferred


def handshake(config: CaseConfig, session: ssl.SSLSession | None = None) -> HandshakeResult:
    client_context, server_context, facts, trace = make_contexts(config)
    client_in = ssl.MemoryBIO()
    client_out = ssl.MemoryBIO()
    server_in = ssl.MemoryBIO()
    server_out = ssl.MemoryBIO()

    try:
        client = client_context.wrap_bio(
            client_in,
            client_out,
            server_side=False,
            server_hostname=None,
            session=session,
        )
        server = server_context.wrap_bio(
            server_in,
            server_out,
            server_side=True,
        )
    except (ValueError, ssl.SSLError):
        return HandshakeResult(
            False,
            None,
            None,
            client_context,
            server_context,
            facts,
            trace,
            "setup_rejected",
        )

    client_done = False
    server_done = False
    transferred = 0
    started = time.monotonic()
    for step in range(1, MAX_HANDSHAKE_STEPS + 1):
        if time.monotonic() - started > MAX_HANDSHAKE_SECONDS:
            return HandshakeResult(
                False,
                client,
                server,
                client_context,
                server_context,
                facts,
                trace,
                "deadline",
                step,
                transferred,
            )
        progress = False
        for endpoint, done in ((client, client_done), (server, server_done)):
            if done:
                continue
            try:
                endpoint.do_handshake()
                if endpoint is client:
                    client_done = True
                else:
                    server_done = True
                progress = True
            except (ssl.SSLWantReadError, ssl.SSLWantWriteError):
                pass
            except ssl.SSLError:
                return HandshakeResult(
                    False,
                    client,
                    server,
                    client_context,
                    server_context,
                    facts,
                    trace,
                    "tls_rejected",
                    step,
                    transferred,
                )

        moved = transfer(client_out, server_in)
        moved += transfer(server_out, client_in)
        transferred += moved
        progress = progress or moved > 0
        if transferred > MAX_HANDSHAKE_BYTES:
            return HandshakeResult(
                False,
                client,
                server,
                client_context,
                server_context,
                facts,
                trace,
                "byte_limit",
                step,
                transferred,
            )
        if client_done and server_done:
            result = HandshakeResult(
                True,
                client,
                server,
                client_context,
                server_context,
                facts,
                trace,
                None,
                step,
                transferred,
            )
            result._bios = (client_in, client_out, server_in, server_out)
            return result
        if not progress:
            return HandshakeResult(
                False,
                client,
                server,
                client_context,
                server_context,
                facts,
                trace,
                "stalled",
                step,
                transferred,
            )

    return HandshakeResult(
        False,
        client,
        server,
        client_context,
        server_context,
        facts,
        trace,
        "step_limit",
        MAX_HANDSHAKE_STEPS,
        transferred,
    )


def read_u8_vector(data: bytes, offset: int) -> tuple[bytes, int]:
    if offset >= len(data):
        raise ProbeFailure("truncated one-byte vector")
    length = data[offset]
    start = offset + 1
    end = start + length
    if end > len(data):
        raise ProbeFailure("truncated one-byte vector body")
    return data[start:end], end


def read_u16_vector(data: bytes, offset: int) -> tuple[bytes, int]:
    if offset + 2 > len(data):
        raise ProbeFailure("truncated two-byte vector")
    length = struct.unpack_from("!H", data, offset)[0]
    start = offset + 2
    end = start + length
    if end > len(data):
        raise ProbeFailure("truncated two-byte vector body")
    return data[start:end], end


def handshake_body(data: bytes, expected_type: int) -> bytes:
    if len(data) < 4 or data[0] != expected_type:
        raise ProbeFailure("unexpected handshake message")
    length = int.from_bytes(data[1:4], "big")
    if length != len(data) - 4:
        raise ProbeFailure("handshake message length differs")
    return data[4:]


def parse_extensions(data: bytes) -> dict[int, bytes]:
    extensions: dict[int, bytes] = {}
    offset = 0
    while offset < len(data):
        if offset + 4 > len(data):
            raise ProbeFailure("truncated extension header")
        extension_type, length = struct.unpack_from("!HH", data, offset)
        offset += 4
        end = offset + length
        if end > len(data) or extension_type in extensions:
            raise ProbeFailure("invalid or duplicate extension")
        extensions[extension_type] = data[offset:end]
        offset = end
    return extensions


def client_hello_extensions(message: bytes) -> dict[int, bytes]:
    body = handshake_body(message, CLIENT_HELLO)
    offset = 2 + 32
    _, offset = read_u8_vector(body, offset)
    _, offset = read_u16_vector(body, offset)
    _, offset = read_u8_vector(body, offset)
    extension_bytes, offset = read_u16_vector(body, offset)
    if offset != len(body):
        raise ProbeFailure("ClientHello trailing bytes")
    return parse_extensions(extension_bytes)


def server_hello_extensions(message: bytes) -> dict[int, bytes]:
    body = handshake_body(message, SERVER_HELLO)
    offset = 2 + 32
    _, offset = read_u8_vector(body, offset)
    offset += 2 + 1
    if offset > len(body):
        raise ProbeFailure("truncated ServerHello")
    extension_bytes, offset = read_u16_vector(body, offset)
    if offset != len(body):
        raise ProbeFailure("ServerHello trailing bytes")
    return parse_extensions(extension_bytes)


def offered_psk_identities(extension: bytes) -> list[bytes]:
    identities_data, offset = read_u16_vector(extension, 0)
    identities: list[bytes] = []
    identity_offset = 0
    while identity_offset < len(identities_data):
        identity, identity_offset = read_u16_vector(identities_data, identity_offset)
        if identity_offset + 4 > len(identities_data):
            raise ProbeFailure("truncated PSK ticket age")
        identity_offset += 4
        identities.append(identity)
    _, offset = read_u16_vector(extension, offset)
    if offset != len(extension):
        raise ProbeFailure("pre_shared_key extension trailing bytes")
    return identities


def validate_key_shares(
    client_extension: bytes,
    server_extension: bytes,
) -> None:
    client_shares, client_end = read_u16_vector(client_extension, 0)
    if client_end != len(client_extension) or len(client_shares) < 4:
        raise ProbeFailure("ClientHello key share differs")
    if len(server_extension) < 4:
        raise ProbeFailure("ServerHello key share is truncated")
    server_share, server_end = read_u16_vector(server_extension, 2)
    if server_end != len(server_extension) or not server_share:
        raise ProbeFailure("ServerHello key share differs")


def find_message(trace: list[tuple[int, int, bytes]], message_type: int) -> bytes:
    for _, observed_type, data in trace:
        if observed_type == message_type and data[:1] == bytes([message_type]):
            return data
    raise ProbeFailure(f"handshake message missing: {message_type}")


def profile_rejection(
    result: HandshakeResult,
    config: CaseConfig,
    *,
    supplied_session: bool = False,
) -> str | None:
    """Return the fail-closed profile rejection before application data."""
    if supplied_session:
        return "supplied_session"
    if config.server_num_tickets != 0:
        return "tickets_enabled"
    if (
        config.client_minimum != ssl.TLSVersion.TLSv1_3
        or config.client_maximum != ssl.TLSVersion.TLSv1_3
        or config.server_minimum != ssl.TLSVersion.TLSv1_3
        or config.server_maximum != ssl.TLSVersion.TLSv1_3
    ):
        return "version_profile"
    if not config.client_callback or not config.server_callback:
        return "psk_callbacks"
    if config.client_identity != FIXTURE_TECNICA_IDENTITY:
        return "identity"
    if (
        config.client_alpn != FIXTURE_TECNICA_ALPN
        or config.server_alpn != FIXTURE_TECNICA_ALPN
    ):
        return "alpn_configuration"
    if config.load_server_certificate:
        return "certificate_configured"
    if not result.success or result.client is None or result.server is None:
        return "tls_handshake"
    if result.callback_facts.client_hints != [None]:
        return "client_psk_callback"
    if result.callback_facts.server_identities != [FIXTURE_TECNICA_IDENTITY]:
        return "server_psk_callback"
    if (
        result.client.selected_alpn_protocol() != FIXTURE_TECNICA_ALPN
        or result.server.selected_alpn_protocol() != FIXTURE_TECNICA_ALPN
    ):
        return "negotiated_alpn"
    if result.client.getpeercert(binary_form=True) is not None:
        return "peer_certificate"
    if result.server_context.num_tickets != 0:
        return "runtime_tickets"
    return None


def validate_success(result: HandshakeResult, config: CaseConfig) -> None:
    if not result.success or result.client is None or result.server is None:
        raise ProbeFailure("expected handshake success")
    rejection = profile_rejection(result, config)
    if rejection is not None:
        raise ProbeFailure(f"successful case rejected by profile: {rejection}")
    if result.steps > MAX_HANDSHAKE_STEPS or result.transferred > MAX_HANDSHAKE_BYTES:
        raise ProbeFailure("handshake bound exceeded")

    client = result.client
    server = result.server
    facts = result.callback_facts
    if facts.client_hints != [None]:
        raise ProbeFailure("client PSK callback facts differ")
    if facts.server_identities != [FIXTURE_TECNICA_IDENTITY]:
        raise ProbeFailure("server PSK identity differs")
    if client.version() != "TLSv1.3" or server.version() != "TLSv1.3":
        raise ProbeFailure("TLS version differs")
    cipher_name, cipher_version, cipher_bits = client.cipher()
    if (
        cipher_version != "TLSv1.3"
        or cipher_bits < 128
        or not cipher_name.startswith("TLS_")
    ):
        raise ProbeFailure("TLS 1.3 AEAD cipher properties differ")
    if (
        client.selected_alpn_protocol() != FIXTURE_TECNICA_ALPN
        or server.selected_alpn_protocol() != FIXTURE_TECNICA_ALPN
    ):
        raise ProbeFailure("fixed ALPN differs")
    if client.getpeercert(binary_form=True) is not None:
        raise ProbeFailure("certificate unexpectedly present")

    handshake_types = [message_type for _, message_type, _ in result.trace]
    for forbidden in (
        NEW_SESSION_TICKET,
        END_OF_EARLY_DATA,
        CERTIFICATE,
        CERTIFICATE_REQUEST,
        CERTIFICATE_VERIFY,
    ):
        if forbidden in handshake_types:
            raise ProbeFailure(f"forbidden handshake message present: {forbidden}")
    if handshake_types.count(20) != 2:
        raise ProbeFailure("mutually authenticated Finished messages differ")

    client_extensions = client_hello_extensions(
        find_message(result.trace, CLIENT_HELLO)
    )
    server_extensions = server_hello_extensions(
        find_message(result.trace, SERVER_HELLO)
    )
    required_client = {
        EXT_PRE_SHARED_KEY,
        EXT_PSK_KEY_EXCHANGE_MODES,
        EXT_KEY_SHARE,
    }
    if not required_client.issubset(client_extensions):
        raise ProbeFailure("ClientHello PSK-DHE extensions differ")
    if client_extensions[EXT_PSK_KEY_EXCHANGE_MODES] != b"\x01\x01":
        raise ProbeFailure("ClientHello offers a non-DHE PSK mode")
    if offered_psk_identities(client_extensions[EXT_PRE_SHARED_KEY]) != [
        FIXTURE_TECNICA_IDENTITY.encode("utf-8")
    ]:
        raise ProbeFailure("ClientHello PSK identity differs")
    if EXT_EARLY_DATA in client_extensions:
        raise ProbeFailure("ClientHello offers early data")
    if not {EXT_PRE_SHARED_KEY, EXT_KEY_SHARE}.issubset(server_extensions):
        raise ProbeFailure("ServerHello did not select PSK-DHE")
    validate_key_shares(
        client_extensions[EXT_KEY_SHARE],
        server_extensions[EXT_KEY_SHARE],
    )
    if server_extensions[EXT_PRE_SHARED_KEY] != b"\x00\x00":
        raise ProbeFailure("ServerHello selected an unexpected PSK identity")

    if result.server_context.num_tickets != 0:
        raise ProbeFailure("server tickets are enabled")
    if not result.server_context.options & ssl.OP_NO_TICKET:
        raise ProbeFailure("server no-ticket option is absent")
    if client.session.has_ticket or server.session.has_ticket:
        raise ProbeFailure("external PSK session has a ticket")
    # OpenSSL documents external TLS 1.3 PSKs as session_reused=True.
    if not client.session_reused or not server.session_reused:
        raise ProbeFailure("external PSK session marker differs")
    for name in ("write_early_data", "read_early_data"):
        if hasattr(client, name) or hasattr(server, name):
            raise ProbeFailure("CPython unexpectedly exposes early-data API")


def exchange_application_data(result: HandshakeResult) -> None:
    if result.client is None or result.server is None:
        raise ProbeFailure("application exchange lacks endpoints")
    try:
        client_in, client_out, server_in, server_out = result._bios
    except AttributeError as error:
        raise ProbeFailure("application exchange lacks memory BIOs") from error

    written = result.client.write(FIXTURE_TECNICA_APPLICATION_DATA)
    if written != len(FIXTURE_TECNICA_APPLICATION_DATA):
        raise ProbeFailure("client application write was partial")
    transfer(client_out, server_in)
    observed = result.server.read(len(FIXTURE_TECNICA_APPLICATION_DATA))
    if observed != FIXTURE_TECNICA_APPLICATION_DATA:
        raise ProbeFailure("server application data differs")

    written = result.server.write(FIXTURE_TECNICA_APPLICATION_ACK)
    if written != len(FIXTURE_TECNICA_APPLICATION_ACK):
        raise ProbeFailure("server application write was partial")
    transfer(server_out, client_in)
    observed = result.client.read(len(FIXTURE_TECNICA_APPLICATION_ACK))
    if observed != FIXTURE_TECNICA_APPLICATION_ACK:
        raise ProbeFailure("client application acknowledgement differs")


def expect_tls_rejection(name: str, config: CaseConfig) -> None:
    result = handshake(config)
    if result.success:
        raise ProbeFailure(f"{name} unexpectedly completed TLS")
    if result.failure_kind not in {"tls_rejected", "setup_rejected"}:
        raise ProbeFailure(f"{name} did not fail closed")
    print(f"PASS {name}")


def expect_profile_rejection(
    name: str,
    config: CaseConfig,
    expected_reason: str,
) -> HandshakeResult:
    result = handshake(config)
    if not result.success or result.client is None or result.server is None:
        raise ProbeFailure(f"{name} did not reach the profile check")
    rejection = profile_rejection(result, config)
    if rejection != expected_reason:
        raise ProbeFailure(
            f"{name} profile reason differs: {rejection or 'accepted'}"
        )
    print(f"PASS {name}")
    return result


def probe() -> None:
    profile = verify_runtime()
    print(f"PASS FIXTURE_TECNICA_runtime_{profile}")

    success_config = CaseConfig()
    success = handshake(success_config)
    validate_success(success, success_config)
    exchange_application_data(success)
    print("PASS FIXTURE_TECNICA_tls13_external_psk_dhe")
    print("PASS FIXTURE_TECNICA_mutual_psk_callbacks")
    print("PASS FIXTURE_TECNICA_fixed_identity_and_alpn")
    print("PASS FIXTURE_TECNICA_no_certificate_messages")
    print("PASS FIXTURE_TECNICA_no_ticket_or_early_data_messages")
    print("PASS FIXTURE_TECNICA_bounded_memory_bio_handshake")

    expect_tls_rejection(
        "FIXTURE_TECNICA_wrong_identity_rejected",
        CaseConfig(client_identity=FIXTURE_TECNICA_WRONG_IDENTITY),
    )
    expect_tls_rejection(
        "FIXTURE_TECNICA_absent_identity_rejected",
        CaseConfig(client_identity=""),
    )
    expect_tls_rejection(
        "FIXTURE_TECNICA_wrong_key_rejected",
        CaseConfig(client_key=FIXTURE_TECNICA_WRONG_PSK),
    )
    expect_tls_rejection(
        "FIXTURE_TECNICA_absent_client_key_rejected",
        CaseConfig(client_key=b""),
    )
    expect_tls_rejection(
        "FIXTURE_TECNICA_absent_server_key_rejected",
        CaseConfig(server_key=b""),
    )
    expect_tls_rejection(
        "FIXTURE_TECNICA_absent_callbacks_rejected",
        CaseConfig(client_callback=False, server_callback=False),
    )
    expect_tls_rejection(
        "FIXTURE_TECNICA_tls12_rejected",
        CaseConfig(
            client_minimum=ssl.TLSVersion.TLSv1_2,
            client_maximum=ssl.TLSVersion.TLSv1_2,
        ),
    )

    expect_profile_rejection(
        "FIXTURE_TECNICA_wrong_alpn_rejected_before_application_data",
        CaseConfig(client_alpn=FIXTURE_TECNICA_WRONG_ALPN),
        "alpn_configuration",
    )
    expect_profile_rejection(
        "FIXTURE_TECNICA_certificate_fallback_rejected_before_application_data",
        CaseConfig(
            server_key=b"",
            load_server_certificate=True,
        ),
        "certificate_configured",
    )

    ticket_config = CaseConfig(server_num_tickets=2)
    ticket_result = handshake(ticket_config)
    if profile_rejection(ticket_result, ticket_config) != "tickets_enabled":
        raise ProbeFailure("ticket configuration was not rejected by profile")
    print("PASS FIXTURE_TECNICA_ticket_configuration_rejected_by_profile")

    if success.client is None:
        raise ProbeFailure("successful client session missing")
    resumption_config = CaseConfig()
    resumed = handshake(resumption_config, session=success.client.session)
    if (
        profile_rejection(
            resumed,
            resumption_config,
            supplied_session=True,
        )
        != "supplied_session"
    ):
        raise ProbeFailure("supplied session was not rejected by profile")
    print("PASS FIXTURE_TECNICA_supplied_session_rejected_by_profile")

    print("P13_TRANSPORT_PROBE_PASS")
    print("P13_SECRET_MEMORY_CONDITIONAL")


def main() -> int:
    if len(sys.argv) != 1:
        print("P13_TRANSPORT_PROBE_FAIL: usage: tools/p13-transport-probe", file=sys.stderr)
        return 2
    try:
        probe()
    except (ProbeFailure, ssl.SSLError, OSError, ValueError) as error:
        print(
            f"P13_TRANSPORT_PROBE_FAIL: {error.__class__.__name__}: {error}",
            file=sys.stderr,
        )
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
