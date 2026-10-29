use std::cell::RefCell;
use std::io::{Cursor, Read, Write};
use std::rc::Rc;

use addon_runtime::{
    MAX_SERVER_FRAME_BYTES, READ_ONLY_COMMAND_ALLOWLIST, SupervisorClient, SupervisorCommand,
    SupervisorError,
};
use serde_json::{Value, json};

const FIXTURE_TECNICA_NONCE: [u8; 16] = *b"the sample nonce";
const FIXTURE_TECNICA_TOKEN: &str = "FIXTURE_TECNICA_SUPERVISOR_TOKEN";
const FIXTURE_TECNICA_ACCEPT: &str = "s3pPLMBiTxaQ9kYGzzhZRbK+xOo=";
const FIXTURE_TECNICA_REGISTRY_LIGHT: &str = "22222222222222222222222222222222";
const FIXTURE_TECNICA_REGISTRY_SWITCH: &str = "11111111111111111111111111111111";
const FIXTURE_TECNICA_LIGHT: &str = "light.fixture_tecnica_z";
const FIXTURE_TECNICA_SWITCH: &str = "switch.fixture_tecnica_a";

#[derive(Clone)]
struct WriteCapture(Rc<RefCell<Vec<u8>>>);

struct ScriptedIo {
    input: Cursor<Vec<u8>>,
    output: WriteCapture,
}

impl ScriptedIo {
    fn new(input: Vec<u8>) -> (Self, WriteCapture) {
        let output = WriteCapture(Rc::new(RefCell::new(Vec::new())));
        (
            Self {
                input: Cursor::new(input),
                output: output.clone(),
            },
            output,
        )
    }
}

impl Read for ScriptedIo {
    fn read(&mut self, buffer: &mut [u8]) -> std::io::Result<usize> {
        self.input.read(buffer)
    }
}

impl Write for ScriptedIo {
    fn write(&mut self, bytes: &[u8]) -> std::io::Result<usize> {
        self.output.0.borrow_mut().extend_from_slice(bytes);
        Ok(bytes.len())
    }

    fn flush(&mut self) -> std::io::Result<()> {
        Ok(())
    }
}

fn fixture_tecnica_handshake(accept: &str, extra_headers: &str) -> Vec<u8> {
    format!(
        "HTTP/1.1 101 Switching Protocols\r\n\
         Upgrade: websocket\r\n\
         Connection: keep-alive, Upgrade\r\n\
         Sec-WebSocket-Accept: {accept}\r\n\
         {extra_headers}\
         \r\n"
    )
    .into_bytes()
}

fn fixture_tecnica_server_frame(opcode: u8, payload: &[u8]) -> Vec<u8> {
    let mut frame = vec![0x80 | opcode];
    match payload.len() {
        0..=125 => frame.push(u8::try_from(payload.len()).expect("FIXTURE_TECNICA short frame")),
        126..=65_535 => {
            frame.push(126);
            frame.extend_from_slice(
                &u16::try_from(payload.len())
                    .expect("FIXTURE_TECNICA medium frame")
                    .to_be_bytes(),
            );
        }
        _ => {
            frame.push(127);
            frame.extend_from_slice(
                &u64::try_from(payload.len())
                    .expect("FIXTURE_TECNICA long frame")
                    .to_be_bytes(),
            );
        }
    }
    frame.extend_from_slice(payload);
    frame
}

fn fixture_tecnica_text(value: &Value) -> Vec<u8> {
    fixture_tecnica_server_frame(
        0x1,
        &serde_json::to_vec(value).expect("FIXTURE_TECNICA JSON"),
    )
}

fn fixture_tecnica_authenticated_script(extra: &[u8]) -> Vec<u8> {
    let mut script = fixture_tecnica_handshake(FIXTURE_TECNICA_ACCEPT, "");
    script.extend_from_slice(&fixture_tecnica_text(&json!({
        "ha_version": "2026.8.3",
        "type": "auth_required",
    })));
    script.extend_from_slice(&fixture_tecnica_text(&json!({
        "ha_version": "2026.8.3",
        "type": "auth_ok",
    })));
    script.extend_from_slice(extra);
    script
}

fn fixture_tecnica_masks(count: usize) -> Cursor<Vec<u8>> {
    let mut masks = Vec::with_capacity(count * 4);
    for index in 0..count {
        let value = u8::try_from(index + 1).expect("FIXTURE_TECNICA mask count");
        masks.extend_from_slice(&[
            value,
            value.wrapping_add(1),
            value.wrapping_add(2),
            value.wrapping_add(3),
        ]);
    }
    Cursor::new(masks)
}

fn fixture_tecnica_registry_values() -> (Value, Value) {
    let slim = json!([
        {
            "disabled_by": null,
            "entity_id": FIXTURE_TECNICA_SWITCH,
            "id": FIXTURE_TECNICA_REGISTRY_SWITCH,
            "name": "FIXTURE_TECNICA_SWITCH"
        },
        {
            "disabled_by": null,
            "entity_id": FIXTURE_TECNICA_LIGHT,
            "id": FIXTURE_TECNICA_REGISTRY_LIGHT,
            "name": "FIXTURE_TECNICA_LIGHT"
        }
    ]);
    let extended = json!({
        "light.fixture_tecnica_z": {
            "aliases": [
                null,
                "FIXTURE_TECNICA_ALIAS_Z",
                "FIXTURE_TECNICA_ALIAS_A"
            ],
            "disabled_by": null,
            "entity_id": FIXTURE_TECNICA_LIGHT,
            "id": FIXTURE_TECNICA_REGISTRY_LIGHT,
            "name": "FIXTURE_TECNICA_LIGHT"
        },
        "switch.fixture_tecnica_a": {
            "aliases": [null],
            "disabled_by": null,
            "entity_id": FIXTURE_TECNICA_SWITCH,
            "id": FIXTURE_TECNICA_REGISTRY_SWITCH,
            "name": "FIXTURE_TECNICA_SWITCH"
        }
    });
    (slim, extended)
}

fn fixture_tecnica_synchronization_responses(extended: Value) -> Vec<u8> {
    let (slim, _) = fixture_tecnica_registry_values();
    let mut responses = Vec::new();
    let mut request_id = 1_u64;
    for command in SupervisorCommand::ALL {
        let result = if command == SupervisorCommand::EntityRegistryList {
            slim.clone()
        } else {
            json!({
                "command": command.as_str(),
                "fixture": "FIXTURE_TECNICA_RESULT",
                "numeric_attribute": 1.25,
            })
        };
        responses.extend_from_slice(&fixture_tecnica_text(&json!({
            "id": request_id,
            "result": result,
            "success": true,
            "type": "result",
        })));
        request_id += 1;
        if command == SupervisorCommand::EntityRegistryList {
            responses.extend_from_slice(&fixture_tecnica_text(&json!({
                "id": request_id,
                "result": extended.clone(),
                "success": true,
                "type": "result",
            })));
            request_id += 1;
        }
    }
    responses
}

fn fixture_tecnica_output_frames(output: &[u8]) -> Vec<(u8, [u8; 4], Vec<u8>)> {
    let marker = b"\r\n\r\n";
    let start = output
        .windows(marker.len())
        .position(|window| window == marker)
        .map(|position| position + marker.len())
        .expect("FIXTURE_TECNICA HTTP request terminator");
    let mut position = start;
    let mut frames = Vec::new();
    while position < output.len() {
        let first = output[position];
        let second = output[position + 1];
        position += 2;
        assert_ne!(second & 0x80, 0, "FIXTURE_TECNICA client frame is masked");
        let length = match second & 0x7f {
            value @ 0..=125 => usize::from(value),
            126 => {
                let value = u16::from_be_bytes([output[position], output[position + 1]]);
                position += 2;
                usize::from(value)
            }
            127 => {
                let value = u64::from_be_bytes(
                    output[position..position + 8]
                        .try_into()
                        .expect("FIXTURE_TECNICA length"),
                );
                position += 8;
                usize::try_from(value).expect("FIXTURE_TECNICA platform length")
            }
            _ => unreachable!(),
        };
        let mask: [u8; 4] = output[position..position + 4]
            .try_into()
            .expect("FIXTURE_TECNICA mask");
        position += 4;
        let mut payload = output[position..position + length].to_vec();
        position += length;
        for (index, byte) in payload.iter_mut().enumerate() {
            *byte ^= mask[index % mask.len()];
        }
        frames.push((first & 0x0f, mask, payload));
    }
    frames
}

#[test]
fn handshake_authentication_and_allowlisted_synchronization_are_canonical_and_masked() {
    let runtime_contract: Value =
        serde_json::from_str(include_str!("../../../addon/runtime-contract.json"))
            .expect("FIXTURE_TECNICA runtime contract");
    let contract_allowlist =
        runtime_contract["adapter_home_assistant_api"]["websocket_command_allowlist"]
            .as_array()
            .expect("FIXTURE_TECNICA contract allowlist")
            .iter()
            .map(|value| value.as_str().expect("FIXTURE_TECNICA contract command"))
            .collect::<Vec<_>>();
    assert_eq!(contract_allowlist.as_slice(), READ_ONLY_COMMAND_ALLOWLIST);

    let (_, extended) = fixture_tecnica_registry_values();
    let responses = fixture_tecnica_synchronization_responses(extended);
    let (io, capture) = ScriptedIo::new(fixture_tecnica_authenticated_script(&responses));
    let mut client = SupervisorClient::connect(
        io,
        fixture_tecnica_masks(10),
        FIXTURE_TECNICA_TOKEN,
        FIXTURE_TECNICA_NONCE,
    )
    .expect("FIXTURE_TECNICA connect");
    let snapshot = client
        .synchronize_read_only()
        .expect("FIXTURE_TECNICA synchronize");

    assert_eq!(snapshot.entries().len(), SupervisorCommand::ALL.len());
    assert_eq!(
        snapshot
            .get(SupervisorCommand::GetStates)
            .and_then(|value| value.get("numeric_attribute")),
        Some(&json!(1.25))
    );
    assert_eq!(
        snapshot.get(SupervisorCommand::EntityRegistryList),
        Some(&json!([
            {
                "aliases": [],
                "disabled_by": null,
                "entity_id": FIXTURE_TECNICA_SWITCH,
                "id": FIXTURE_TECNICA_REGISTRY_SWITCH,
                "name": "FIXTURE_TECNICA_SWITCH"
            },
            {
                "aliases": [
                    "FIXTURE_TECNICA_ALIAS_A",
                    "FIXTURE_TECNICA_ALIAS_Z"
                ],
                "disabled_by": null,
                "entity_id": FIXTURE_TECNICA_LIGHT,
                "id": FIXTURE_TECNICA_REGISTRY_LIGHT,
                "name": "FIXTURE_TECNICA_LIGHT"
            }
        ]))
    );
    assert!(snapshot.wire_bytes() > 0);
    assert!(!format!("{snapshot:?}").contains("FIXTURE_TECNICA_RESULT"));
    assert!(!format!("{client:?}").contains(FIXTURE_TECNICA_TOKEN));

    let output = capture.0.borrow();
    let head_end = output
        .windows(4)
        .position(|window| window == b"\r\n\r\n")
        .expect("FIXTURE_TECNICA request head");
    let head = std::str::from_utf8(&output[..head_end]).expect("FIXTURE_TECNICA HTTP UTF-8");
    assert_eq!(
        head,
        "GET /core/websocket HTTP/1.1\r\n\
         Host: supervisor\r\n\
         Upgrade: websocket\r\n\
         Connection: Upgrade\r\n\
         Sec-WebSocket-Key: dGhlIHNhbXBsZSBub25jZQ==\r\n\
         Sec-WebSocket-Version: 13"
    );
    assert!(!head.contains(FIXTURE_TECNICA_TOKEN));
    assert!(!head.to_ascii_lowercase().contains("extension"));

    let frames = fixture_tecnica_output_frames(&output);
    assert_eq!(frames.len(), READ_ONLY_COMMAND_ALLOWLIST.len() + 1);
    assert_eq!(
        frames[0].2,
        br#"{"access_token":"FIXTURE_TECNICA_SUPERVISOR_TOKEN","type":"auth"}"#
    );
    let requests = frames[1..]
        .iter()
        .map(|frame| {
            assert_eq!(frame.0, 0x1);
            serde_json::from_slice::<Value>(&frame.2).expect("FIXTURE_TECNICA request JSON")
        })
        .collect::<Vec<_>>();
    assert_eq!(
        requests,
        [
            json!({"id": 1, "type": "config/area_registry/list"}),
            json!({"id": 2, "type": "config/device_registry/list"}),
            json!({"id": 3, "type": "config/entity_registry/list"}),
            json!({
                "entity_ids": [
                    FIXTURE_TECNICA_SWITCH,
                    FIXTURE_TECNICA_LIGHT
                ],
                "id": 4,
                "type": "config/entity_registry/get_entries"
            }),
            json!({"id": 5, "type": "config/floor_registry/list"}),
            json!({"id": 6, "type": "get_services"}),
            json!({"id": 7, "type": "get_states"}),
            json!({"id": 8, "type": "homeassistant/expose_entity/list"}),
        ]
    );
    assert_eq!(
        frames[4].2,
        br#"{"entity_ids":["switch.fixture_tecnica_a","light.fixture_tecnica_z"],"id":4,"type":"config/entity_registry/get_entries"}"#
    );
}

#[test]
fn entity_registry_alias_fetch_uses_exact_request_and_stable_identity_merge() {
    let (_, extended) = fixture_tecnica_registry_values();
    let responses = fixture_tecnica_synchronization_responses(extended);
    let (io, capture) = ScriptedIo::new(fixture_tecnica_authenticated_script(&responses));
    let mut client = SupervisorClient::connect(
        io,
        fixture_tecnica_masks(10),
        FIXTURE_TECNICA_TOKEN,
        FIXTURE_TECNICA_NONCE,
    )
    .expect("FIXTURE_TECNICA connect");
    let snapshot = client
        .synchronize_read_only()
        .expect("FIXTURE_TECNICA synchronize");

    assert_eq!(
        snapshot
            .get(SupervisorCommand::EntityRegistryList)
            .and_then(Value::as_array)
            .and_then(|entities| {
                entities
                    .iter()
                    .find(|entity| entity.get("entity_id") == Some(&json!(FIXTURE_TECNICA_LIGHT)))
            })
            .and_then(|entity| entity.get("aliases")),
        Some(&json!([
            "FIXTURE_TECNICA_ALIAS_A",
            "FIXTURE_TECNICA_ALIAS_Z"
        ]))
    );
    let frames = fixture_tecnica_output_frames(&capture.0.borrow());
    assert_eq!(
        frames[4].2,
        br#"{"entity_ids":["switch.fixture_tecnica_a","light.fixture_tecnica_z"],"id":4,"type":"config/entity_registry/get_entries"}"#
    );
}

#[test]
fn extended_entity_registry_mismatches_and_malformed_aliases_close_policy() {
    let (_, valid) = fixture_tecnica_registry_values();
    let mut missing = valid.clone();
    missing
        .as_object_mut()
        .expect("FIXTURE_TECNICA extended object")
        .remove(FIXTURE_TECNICA_LIGHT);
    let mut conflicting_id = valid.clone();
    conflicting_id[FIXTURE_TECNICA_LIGHT]["id"] = json!("33333333333333333333333333333333");
    let mut conflicting_field = valid.clone();
    conflicting_field[FIXTURE_TECNICA_LIGHT]["name"] = json!("FIXTURE_TECNICA_CONFLICT");
    let mut missing_aliases = valid.clone();
    missing_aliases[FIXTURE_TECNICA_LIGHT]
        .as_object_mut()
        .expect("FIXTURE_TECNICA extended entry")
        .remove("aliases");
    let mut duplicate_alias = valid;
    duplicate_alias[FIXTURE_TECNICA_LIGHT]["aliases"] =
        json!(["FIXTURE_TECNICA_DUPLICATE", "FIXTURE_TECNICA_DUPLICATE"]);

    for extended in [
        missing,
        conflicting_id,
        conflicting_field,
        missing_aliases,
        duplicate_alias,
    ] {
        let responses = fixture_tecnica_synchronization_responses(extended);
        let (io, capture) = ScriptedIo::new(fixture_tecnica_authenticated_script(&responses));
        let mut client = SupervisorClient::connect(
            io,
            fixture_tecnica_masks(12),
            FIXTURE_TECNICA_TOKEN,
            FIXTURE_TECNICA_NONCE,
        )
        .expect("FIXTURE_TECNICA connect");

        assert_eq!(
            client
                .synchronize_read_only()
                .expect_err("FIXTURE_TECNICA invalid extended registry"),
            SupervisorError::InvalidMessage
        );
        assert!(client.is_closed());
        let frames = fixture_tecnica_output_frames(&capture.0.borrow());
        let close = frames.last().expect("FIXTURE_TECNICA close frame");
        assert_eq!(close.0, 0x8);
        assert_eq!(u16::from_be_bytes([close.2[0], close.2[1]]), 1008);
    }
}

#[test]
fn ping_is_answered_and_application_rejection_does_not_reopen_or_close_connection() {
    let mut exchange = fixture_tecnica_server_frame(0x9, b"FIXTURE_TECNICA_PING");
    exchange.extend_from_slice(&fixture_tecnica_text(&json!({
        "error": {
            "code": "FIXTURE_TECNICA_REJECTED",
            "message": "FIXTURE_TECNICA_MESSAGE",
        },
        "id": 1,
        "success": false,
        "type": "result",
    })));
    let (io, capture) = ScriptedIo::new(fixture_tecnica_authenticated_script(&exchange));
    let mut client = SupervisorClient::connect(
        io,
        fixture_tecnica_masks(5),
        FIXTURE_TECNICA_TOKEN,
        FIXTURE_TECNICA_NONCE,
    )
    .expect("FIXTURE_TECNICA connect");

    assert_eq!(
        client
            .request(SupervisorCommand::GetStates)
            .expect_err("FIXTURE_TECNICA command rejection"),
        SupervisorError::CommandRejected
    );
    assert!(!client.is_closed());
    client.close().expect("FIXTURE_TECNICA close");
    assert!(client.is_closed());

    let frames = fixture_tecnica_output_frames(&capture.0.borrow());
    assert_eq!(frames[2].0, 0xa);
    assert_eq!(frames[2].2, b"FIXTURE_TECNICA_PING");
    assert_eq!(frames[3].0, 0x8);
    assert_eq!(u16::from_be_bytes([frames[3].2[0], frames[3].2[1]]), 1000);
}

#[test]
fn peer_close_is_echoed_and_marks_client_closed() {
    let close_payload = 1001_u16.to_be_bytes();
    let close = fixture_tecnica_server_frame(0x8, &close_payload);
    let (io, capture) = ScriptedIo::new(fixture_tecnica_authenticated_script(&close));
    let mut client = SupervisorClient::connect(
        io,
        fixture_tecnica_masks(4),
        FIXTURE_TECNICA_TOKEN,
        FIXTURE_TECNICA_NONCE,
    )
    .expect("FIXTURE_TECNICA connect");

    assert_eq!(
        client
            .request(SupervisorCommand::GetStates)
            .expect_err("FIXTURE_TECNICA peer close"),
        SupervisorError::PeerClosed
    );
    assert!(client.is_closed());
    let frames = fixture_tecnica_output_frames(&capture.0.borrow());
    assert_eq!(frames.last().map(|frame| frame.0), Some(0x8));
    assert_eq!(
        frames.last().map(|frame| frame.2.as_slice()),
        Some(close_payload.as_slice())
    );
}

#[test]
fn handshake_rejects_wrong_accept_extensions_duplicates_and_non_switching_status() {
    let cases = [
        fixture_tecnica_handshake("FIXTURE_TECNICA_WRONG", ""),
        fixture_tecnica_handshake(
            FIXTURE_TECNICA_ACCEPT,
            "Sec-WebSocket-Extensions: permessage-deflate\r\n",
        ),
        fixture_tecnica_handshake(
            FIXTURE_TECNICA_ACCEPT,
            &format!("Sec-WebSocket-Accept: {FIXTURE_TECNICA_ACCEPT}\r\n"),
        ),
        b"HTTP/1.1 200 OK\r\nContent-Length: 0\r\n\r\n".to_vec(),
    ];
    for script in cases {
        let (io, _) = ScriptedIo::new(script);
        assert_eq!(
            SupervisorClient::connect(
                io,
                fixture_tecnica_masks(1),
                FIXTURE_TECNICA_TOKEN,
                FIXTURE_TECNICA_NONCE,
            )
            .expect_err("FIXTURE_TECNICA rejected handshake"),
            SupervisorError::HandshakeRejected
        );
    }
}

#[test]
fn fragmented_binary_masked_reserved_and_nonminimal_server_frames_fail_closed() {
    let cases = [
        (vec![0x01, 0x00], SupervisorError::ProtocolViolation, 1002),
        (vec![0x82, 0x00], SupervisorError::ProtocolViolation, 1003),
        (vec![0x81, 0x80], SupervisorError::ProtocolViolation, 1002),
        (vec![0xc1, 0x00], SupervisorError::ProtocolViolation, 1002),
        (
            vec![0x81, 126, 0, 1, b'x'],
            SupervisorError::ProtocolViolation,
            1002,
        ),
    ];
    for (frame, expected_error, expected_close) in cases {
        let (io, capture) = ScriptedIo::new(fixture_tecnica_authenticated_script(&frame));
        let mut client = SupervisorClient::connect(
            io,
            fixture_tecnica_masks(4),
            FIXTURE_TECNICA_TOKEN,
            FIXTURE_TECNICA_NONCE,
        )
        .expect("FIXTURE_TECNICA connect");
        assert_eq!(
            client
                .request(SupervisorCommand::GetStates)
                .expect_err("FIXTURE_TECNICA protocol violation"),
            expected_error
        );
        assert!(client.is_closed());
        let frames = fixture_tecnica_output_frames(&capture.0.borrow());
        let close = frames.last().expect("FIXTURE_TECNICA close frame");
        assert_eq!(close.0, 0x8);
        assert_eq!(u16::from_be_bytes([close.2[0], close.2[1]]), expected_close);
    }
}

#[test]
fn oversized_server_frame_is_rejected_from_its_header_without_payload_allocation() {
    let mut oversized = vec![0x81, 127];
    oversized.extend_from_slice(
        &u64::try_from(MAX_SERVER_FRAME_BYTES + 1)
            .expect("FIXTURE_TECNICA length")
            .to_be_bytes(),
    );
    let (io, capture) = ScriptedIo::new(fixture_tecnica_authenticated_script(&oversized));
    let mut client = SupervisorClient::connect(
        io,
        fixture_tecnica_masks(4),
        FIXTURE_TECNICA_TOKEN,
        FIXTURE_TECNICA_NONCE,
    )
    .expect("FIXTURE_TECNICA connect");
    assert_eq!(
        client
            .request(SupervisorCommand::GetStates)
            .expect_err("FIXTURE_TECNICA oversized frame"),
        SupervisorError::FrameTooLarge
    );
    let frames = fixture_tecnica_output_frames(&capture.0.borrow());
    let close = frames.last().expect("FIXTURE_TECNICA close frame");
    assert_eq!(u16::from_be_bytes([close.2[0], close.2[1]]), 1009);
}

#[test]
fn duplicate_json_keys_and_response_id_substitution_fail_closed() {
    let duplicate = br#"{"id":1,"id":1,"result":[],"success":true,"type":"result"}"#;
    let wrong_id = br#"{"id":2,"result":[],"success":true,"type":"result"}"#;
    for payload in [duplicate.as_slice(), wrong_id.as_slice()] {
        let server_frame = fixture_tecnica_server_frame(0x1, payload);
        let (io, capture) = ScriptedIo::new(fixture_tecnica_authenticated_script(&server_frame));
        let mut client = SupervisorClient::connect(
            io,
            fixture_tecnica_masks(4),
            FIXTURE_TECNICA_TOKEN,
            FIXTURE_TECNICA_NONCE,
        )
        .expect("FIXTURE_TECNICA connect");
        assert_eq!(
            client
                .request(SupervisorCommand::GetStates)
                .expect_err("FIXTURE_TECNICA invalid response"),
            SupervisorError::InvalidMessage
        );
        assert!(client.is_closed());
        let frames = fixture_tecnica_output_frames(&capture.0.borrow());
        assert_eq!(frames.last().map(|frame| frame.0), Some(0x8));
    }
}

#[test]
fn invalid_credentials_entropy_and_authentication_are_closed_errors() {
    let (empty_token_io, _) = ScriptedIo::new(Vec::new());
    assert_eq!(
        SupervisorClient::connect(
            empty_token_io,
            fixture_tecnica_masks(1),
            "",
            FIXTURE_TECNICA_NONCE,
        )
        .expect_err("FIXTURE_TECNICA empty token"),
        SupervisorError::InvalidConfiguration
    );

    let mut invalid_auth = fixture_tecnica_handshake(FIXTURE_TECNICA_ACCEPT, "");
    invalid_auth.extend_from_slice(&fixture_tecnica_text(&json!({
        "ha_version": "2026.8.3",
        "type": "auth_required",
    })));
    invalid_auth.extend_from_slice(&fixture_tecnica_text(&json!({
        "message": "FIXTURE_TECNICA_INVALID",
        "type": "auth_invalid",
    })));
    let (invalid_auth_io, capture) = ScriptedIo::new(invalid_auth);
    assert_eq!(
        SupervisorClient::connect(
            invalid_auth_io,
            fixture_tecnica_masks(2),
            FIXTURE_TECNICA_TOKEN,
            FIXTURE_TECNICA_NONCE,
        )
        .expect_err("FIXTURE_TECNICA authentication rejection"),
        SupervisorError::AuthenticationRejected
    );
    let frames = fixture_tecnica_output_frames(&capture.0.borrow());
    assert_eq!(frames.last().map(|frame| frame.0), Some(0x8));

    let (entropy_io, _) = ScriptedIo::new(fixture_tecnica_authenticated_script(&[]));
    assert_eq!(
        SupervisorClient::connect(
            entropy_io,
            Cursor::new(Vec::new()),
            FIXTURE_TECNICA_TOKEN,
            FIXTURE_TECNICA_NONCE,
        )
        .expect_err("FIXTURE_TECNICA missing masking entropy"),
        SupervisorError::MaskingKeyUnavailable
    );
}
