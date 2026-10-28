use std::panic::{AssertUnwindSafe, catch_unwind};

use protocol::{ProtocolError, detect_request_version, v2};

fn exercise(bytes: &[u8]) {
    let result = catch_unwind(AssertUnwindSafe(|| {
        let _ = detect_request_version(bytes);
        v2::decode_request(bytes)
    }))
    .expect("FIXTURE_TECNICA hostile input must never panic");
    if let Err(error) = result {
        let encoded =
            v2::encode_protocol_error(error).expect("FIXTURE_TECNICA bounded protocol error");
        assert!(encoded.len() <= protocol::MAX_PROTOCOL_ERROR_BYTES);
        assert!(
            !String::from_utf8_lossy(&encoded).contains("FIXTURE_TECNICA_PRIVATE_CANARY"),
            "hostile input is never echoed"
        );
    }
}

#[test]
fn fixed_hostile_byte_and_structural_corpus_is_bounded_and_panic_free() {
    for byte in 0_u8..=u8::MAX {
        exercise(&[byte]);
    }

    let valid = br#"{"version":2,"request":{"type":"health"}}"#;
    for end in 0..=valid.len() {
        exercise(&valid[..end]);
    }
    for index in 0..valid.len() {
        for replacement in [0_u8, b'"', b'{', b'}', b'[', b']', b'\\', 0x7f, 0xff] {
            let mut mutation = valid.to_vec();
            mutation[index] = replacement;
            exercise(&mutation);
        }
    }

    for hostile in [
        br#"{"version":2,"version":2,"request":{"type":"health"}}"#.as_slice(),
        br#"{"version":2,"\u0076ersion":2,"request":{"type":"health"}}"#,
        br#"{"version":2,"request":{"type":"health","FIXTURE_TECNICA_PRIVATE_CANARY":"FIXTURE_TECNICA_PRIVATE_CANARY"}}"#,
        br#"{"version":2,"request":{"type":"confirm","session_id":"ffffffffffffffffffffffffffffffffffffffffffffffffffffffffffffffff","confirmation_id":1}}"#,
        br#"{"version":18446744073709551615,"request":{"type":"health"}}"#,
        b"\xef\xbb\xbf{\"version\":2,\"request\":{\"type\":\"health\"}}",
    ] {
        exercise(hostile);
    }

    let exact_wire = vec![b' '; v2::MAX_WIRE_BYTES];
    exercise(&exact_wire);
    let oversized_wire = vec![b' '; v2::MAX_WIRE_BYTES + 1];
    assert_eq!(
        v2::decode_request(&oversized_wire),
        Err(ProtocolError::InputTooLarge)
    );

    let exact_number = format!(
        r#"{{"version":2,"request":{{"type":"health","FIXTURE_TECNICA":{}0{}}}}}"#,
        "[".repeat(v2::MAX_NESTING_DEPTH - 2),
        "]".repeat(v2::MAX_NESTING_DEPTH - 2)
    );
    exercise(exact_number.as_bytes());
    let over_depth = format!(
        r#"{{"version":2,"request":{{"type":"health","FIXTURE_TECNICA":{}0{}}}}}"#,
        "[".repeat(v2::MAX_NESTING_DEPTH - 1),
        "]".repeat(v2::MAX_NESTING_DEPTH - 1)
    );
    assert_eq!(
        v2::decode_request(over_depth.as_bytes()),
        Err(ProtocolError::NestingTooDeep)
    );
}
