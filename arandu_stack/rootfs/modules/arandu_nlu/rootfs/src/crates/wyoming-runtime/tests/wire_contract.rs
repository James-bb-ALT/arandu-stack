use wyoming_runtime::{
    MAX_BUFFERED_BYTES, MAX_CONTEXT_VALUE_BYTES, MAX_DATA_BYTES, MAX_HEADER_BYTES,
    MAX_JSON_COLLECTION_ITEMS, MAX_JSON_DEPTH, MAX_RECOGNITION_TEXT_BYTES, RequestEvent,
    RuntimeError, WireDecoder,
};

const FIXTURE_TECNICA_DESCRIBE: &[u8] = b"{\"type\": \"describe\", \"version\": \"1.10.0\"}\n";
const FIXTURE_TECNICA_TRANSCRIPT: &[u8] = concat!(
    "{\"type\": \"transcript\", \"version\": \"1.10.0\", \"data_length\": 202}\n",
    "{\"text\": \"FIXTURE_TECNICA query\", \"language\": \"pt-BR\", \"context\": {",
    "\"conversation_id\": \"FIXTURE_TECNICA_CONVERSATION\", ",
    "\"device_id\": \"FIXTURE_TECNICA_DEVICE\", ",
    "\"satellite_id\": \"FIXTURE_TECNICA_SATELLITE\"}}"
)
.as_bytes();

fn fixture_tecnica_data(text: &str, context_fields: &str) -> String {
    format!(r#"{{"text":"{text}","language":"pt-BR","context":{{{context_fields}}}}}"#)
}

fn fixture_tecnica_frame(data: &str) -> Vec<u8> {
    let mut frame = format!(
        "{{\"type\":\"transcript\",\"version\":\"1.10.0\",\"data_length\":{}}}\n",
        data.len()
    )
    .into_bytes();
    frame.extend_from_slice(data.as_bytes());
    frame
}

fn fixture_tecnica_minimal_frame() -> Vec<u8> {
    fixture_tecnica_frame(&fixture_tecnica_data(
        "FIXTURE_TECNICA query",
        r#""conversation_id":"FIXTURE_TECNICA_CONVERSATION""#,
    ))
}

fn decode_error(frame: &[u8]) -> RuntimeError {
    WireDecoder::new()
        .push(frame)
        .expect_err("FIXTURE_TECNICA frame must be rejected")
}

#[test]
fn exact_1_10_writer_fixtures_decode() {
    let mut decoder = WireDecoder::new();
    assert_eq!(
        decoder
            .push(FIXTURE_TECNICA_DESCRIBE)
            .expect("FIXTURE_TECNICA describe"),
        vec![RequestEvent::Describe]
    );

    let events = decoder
        .push(FIXTURE_TECNICA_TRANSCRIPT)
        .expect("FIXTURE_TECNICA transcript");
    let [RequestEvent::Transcript(input)] = events.as_slice() else {
        panic!("FIXTURE_TECNICA expected one recognition");
    };
    assert_eq!(input.text(), "FIXTURE_TECNICA query");
    assert_eq!(
        input.context().conversation_id(),
        "FIXTURE_TECNICA_CONVERSATION"
    );
    assert_eq!(input.context().device_id(), Some("FIXTURE_TECNICA_DEVICE"));
    assert_eq!(
        input.context().satellite_id(),
        Some("FIXTURE_TECNICA_SATELLITE")
    );
    assert!(decoder.is_idle());
    decoder.finish().expect("FIXTURE_TECNICA complete");
}

#[test]
fn every_byte_fragmentation_preserves_one_request() {
    let mut decoder = WireDecoder::new();
    let mut events = Vec::new();
    for byte in FIXTURE_TECNICA_TRANSCRIPT {
        events.extend(
            decoder
                .push(core::slice::from_ref(byte))
                .expect("FIXTURE_TECNICA fragment"),
        );
    }
    assert_eq!(events.len(), 1);
    assert!(matches!(events[0], RequestEvent::Transcript(_)));
    assert!(decoder.is_idle());
}

#[test]
fn coalesced_events_are_returned_in_wire_order() {
    let mut coalesced = FIXTURE_TECNICA_DESCRIBE.to_vec();
    coalesced.extend_from_slice(FIXTURE_TECNICA_TRANSCRIPT);

    let events = WireDecoder::new()
        .push(&coalesced)
        .expect("FIXTURE_TECNICA coalesced");
    assert_eq!(events.len(), 2);
    assert_eq!(events[0], RequestEvent::Describe);
    assert!(matches!(events[1], RequestEvent::Transcript(_)));
}

#[test]
fn headers_reject_duplicate_unknown_inline_and_payload_fields() {
    let cases = [
        (
            b"{\"type\":\"describe\",\"type\":\"describe\",\"version\":\"1.10.0\"}\n".as_slice(),
            RuntimeError::DuplicateJsonKey,
        ),
        (
            b"{\"type\":\"describe\",\"version\":\"1.10.0\",\"open\":true}\n".as_slice(),
            RuntimeError::OpenFields,
        ),
        (
            b"{\"type\":\"describe\",\"version\":\"1.10.0\",\"data\":{}}\n".as_slice(),
            RuntimeError::InlineData,
        ),
        (
            b"{\"type\":\"describe\",\"version\":\"1.10.0\",\"payload_length\":0}\n".as_slice(),
            RuntimeError::PayloadNotAllowed,
        ),
        (
            b"{\"type\":\"describe\",\"version\":\"1.9.0\"}\n".as_slice(),
            RuntimeError::UnsupportedVersion,
        ),
        (
            b"{\"type\":\"handle\",\"version\":\"1.10.0\"}\n".as_slice(),
            RuntimeError::UnsupportedEvent,
        ),
        (
            b"{\"type\":\"select-program\",\"version\":\"1.10.0\"}\n".as_slice(),
            RuntimeError::UnsupportedEvent,
        ),
        (
            b"{\"type\":\"recognize\",\"version\":\"1.10.0\",\"data_length\":1}\n".as_slice(),
            RuntimeError::UnsupportedEvent,
        ),
    ];

    for (frame, expected) in cases {
        assert_eq!(decode_error(frame), expected);
    }
}

#[test]
fn recognition_data_rejects_duplicate_open_and_raw_service_fields() {
    let duplicate_text = fixture_tecnica_frame(
        r#"{"text":"FIXTURE_TECNICA","text":"FIXTURE_TECNICA_DUPLICATE","context":{"conversation_id":"FIXTURE_TECNICA_CONVERSATION"}}"#,
    );
    assert_eq!(
        decode_error(&duplicate_text),
        RuntimeError::DuplicateJsonKey
    );

    let duplicate_context = fixture_tecnica_frame(
        r#"{"text":"FIXTURE_TECNICA","context":{"conversation_id":"FIXTURE_TECNICA_A","conversation_id":"FIXTURE_TECNICA_B"}}"#,
    );
    assert_eq!(
        decode_error(&duplicate_context),
        RuntimeError::DuplicateJsonKey
    );

    let open_data = fixture_tecnica_frame(
        r#"{"text":"FIXTURE_TECNICA","context":{"conversation_id":"FIXTURE_TECNICA_CONVERSATION"},"open":true}"#,
    );
    assert_eq!(decode_error(&open_data), RuntimeError::OpenFields);

    let open_context = fixture_tecnica_frame(
        r#"{"text":"FIXTURE_TECNICA","context":{"conversation_id":"FIXTURE_TECNICA_CONVERSATION","open":"FIXTURE_TECNICA"}}"#,
    );
    assert_eq!(decode_error(&open_context), RuntimeError::OpenFields);

    let raw_service = fixture_tecnica_frame(
        r#"{"text":"FIXTURE_TECNICA","context":{"conversation_id":"FIXTURE_TECNICA_CONVERSATION"},"service":"FIXTURE_TECNICA_SERVICE"}"#,
    );
    assert_eq!(decode_error(&raw_service), RuntimeError::OpenFields);
}

#[test]
fn malformed_shapes_and_lengths_fail_closed() {
    let invalid_shapes = [
        fixture_tecnica_frame("[]"),
        fixture_tecnica_frame(
            r#"{"text":true,"context":{"conversation_id":"FIXTURE_TECNICA_CONVERSATION"}}"#,
        ),
        fixture_tecnica_frame(r#"{"text":"FIXTURE_TECNICA","context":null}"#),
        fixture_tecnica_frame(
            r#"{"text":"   ","context":{"conversation_id":"FIXTURE_TECNICA_CONVERSATION"}}"#,
        ),
        fixture_tecnica_frame(
            r#"{"text":"FIXTURE_TECNICA\u0000","context":{"conversation_id":"FIXTURE_TECNICA_CONVERSATION"}}"#,
        ),
        fixture_tecnica_frame(
            r#"{"text":"FIXTURE_TECNICA","context":{"conversation_id":"FIXTURE TECNICA"}}"#,
        ),
    ];
    for frame in invalid_shapes {
        assert!(WireDecoder::new().push(&frame).is_err());
    }

    assert_eq!(
        decode_error(b"{\"type\":\"transcript\",\"version\":\"1.10.0\",\"data_length\":0}\n"),
        RuntimeError::InvalidDataLength
    );
    let oversized_length = format!(
        "{{\"type\":\"transcript\",\"version\":\"1.10.0\",\"data_length\":{}}}\n",
        MAX_DATA_BYTES + 1
    );
    assert_eq!(
        decode_error(oversized_length.as_bytes()),
        RuntimeError::InvalidDataLength
    );
    assert_eq!(
        decode_error(b"{\"type\":\"transcript\",\"version\":\"1.10.0\",\"data_length\":true}\n"),
        RuntimeError::InvalidDataLength
    );

    let wrong_language = fixture_tecnica_frame(
        r#"{"text":"FIXTURE_TECNICA","language":"en","context":{"conversation_id":"FIXTURE_TECNICA_CONVERSATION"}}"#,
    );
    assert_eq!(
        decode_error(&wrong_language),
        RuntimeError::InvalidRecognitionData
    );
}

#[test]
fn json_depth_collection_and_string_limits_are_enforced() {
    let deeply_nested = format!(
        "{{\"text\":\"FIXTURE_TECNICA\",\"context\":{{\"conversation_id\":\"FIXTURE_TECNICA_CONVERSATION\",\"open\":{}0{}}}}}",
        "[".repeat(MAX_JSON_DEPTH),
        "]".repeat(MAX_JSON_DEPTH)
    );
    assert_eq!(
        decode_error(&fixture_tecnica_frame(&deeply_nested)),
        RuntimeError::JsonDepthLimit
    );

    let collection = (0..=MAX_JSON_COLLECTION_ITEMS)
        .map(|_| "\"FIXTURE_TECNICA\"")
        .collect::<Vec<_>>()
        .join(",");
    let excessive_collection = format!(
        "{{\"text\":\"FIXTURE_TECNICA\",\"context\":{{\"conversation_id\":\"FIXTURE_TECNICA_CONVERSATION\",\"open\":[{collection}]}}}}"
    );
    assert_eq!(
        decode_error(&fixture_tecnica_frame(&excessive_collection)),
        RuntimeError::JsonCollectionLimit
    );

    let mut maximum_text = "FIXTURE_TECNICA".to_owned();
    maximum_text.push_str(&"X".repeat(MAX_RECOGNITION_TEXT_BYTES - maximum_text.len()));
    let maximum_frame = fixture_tecnica_frame(&fixture_tecnica_data(
        &maximum_text,
        r#""conversation_id":"FIXTURE_TECNICA_CONVERSATION""#,
    ));
    assert_eq!(
        WireDecoder::new()
            .push(&maximum_frame)
            .expect("FIXTURE_TECNICA maximum text")
            .len(),
        1
    );

    maximum_text.push('X');
    assert_eq!(
        decode_error(&fixture_tecnica_frame(&fixture_tecnica_data(
            &maximum_text,
            r#""conversation_id":"FIXTURE_TECNICA_CONVERSATION""#,
        ))),
        RuntimeError::JsonStringLimit
    );
}

#[test]
fn context_values_are_independently_bounded() {
    let mut maximum_id = "FIXTURE_TECNICA".to_owned();
    maximum_id.push_str(&"X".repeat(MAX_CONTEXT_VALUE_BYTES - maximum_id.len()));
    let frame = fixture_tecnica_frame(&fixture_tecnica_data(
        "FIXTURE_TECNICA",
        &format!(r#""conversation_id":"{maximum_id}""#),
    ));
    assert_eq!(
        WireDecoder::new()
            .push(&frame)
            .expect("FIXTURE_TECNICA maximum context")
            .len(),
        1
    );

    maximum_id.push('X');
    let frame = fixture_tecnica_frame(&fixture_tecnica_data(
        "FIXTURE_TECNICA",
        &format!(r#""conversation_id":"{maximum_id}""#),
    ));
    assert_eq!(decode_error(&frame), RuntimeError::InvalidContext);
}

#[test]
fn byte_buffers_and_partial_frames_are_bounded_and_poison_on_failure() {
    let mut header_decoder = WireDecoder::new();
    assert_eq!(
        header_decoder
            .push(&vec![b' '; MAX_HEADER_BYTES])
            .expect_err("FIXTURE_TECNICA oversized header"),
        RuntimeError::HeaderTooLarge
    );
    assert_eq!(
        header_decoder
            .push(FIXTURE_TECNICA_DESCRIBE)
            .expect_err("FIXTURE_TECNICA poisoned decoder"),
        RuntimeError::DecoderFailed
    );

    assert_eq!(
        WireDecoder::new()
            .push(&vec![b'X'; MAX_BUFFERED_BYTES + 1])
            .expect_err("FIXTURE_TECNICA buffer limit"),
        RuntimeError::BufferLimit
    );

    let mut partial = WireDecoder::new();
    partial
        .push(&fixture_tecnica_minimal_frame()[..20])
        .expect("FIXTURE_TECNICA partial input");
    assert_eq!(
        partial
            .finish()
            .expect_err("FIXTURE_TECNICA truncated frame"),
        RuntimeError::TruncatedFrame
    );
}
