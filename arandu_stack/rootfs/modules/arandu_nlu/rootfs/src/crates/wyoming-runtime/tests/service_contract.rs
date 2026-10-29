use serde_json::Value;
use wyoming_runtime::{
    CONNECTION_LIFETIME_MILLIS, MAX_REQUESTS_PER_CONNECTION, MAX_REVALIDATED_INTENTS,
    MAX_STATE_QUERY_NAME_BYTES, MonotonicMillis, RecognitionCompletion, RecognitionRequest,
    RevalidatedRecognition, RevalidatedStateQuery, RuntimeError, ServiceAction, ServiceConnection,
};

const FIXTURE_TECNICA_DESCRIBE: &[u8] = b"{\"type\": \"describe\", \"version\": \"1.10.0\"}\n";

fn fixture_tecnica_transcript() -> Vec<u8> {
    let data = concat!(
        "{\"text\":\"FIXTURE_TECNICA query\",\"language\":\"pt-BR\",\"context\":{",
        "\"conversation_id\":\"FIXTURE_TECNICA_CONVERSATION\",",
        "\"device_id\":\"FIXTURE_TECNICA_DEVICE\",",
        "\"satellite_id\":\"FIXTURE_TECNICA_SATELLITE\"}}"
    );
    let mut frame = format!(
        "{{\"type\":\"transcript\",\"version\":\"1.10.0\",\"data_length\":{}}}\n",
        data.len()
    )
    .into_bytes();
    frame.extend_from_slice(data.as_bytes());
    frame
}

fn one_action(actions: Vec<ServiceAction>) -> ServiceAction {
    let [action] = actions
        .try_into()
        .expect("FIXTURE_TECNICA expected one action");
    action
}

fn recognition_request(action: ServiceAction) -> RecognitionRequest {
    match action {
        ServiceAction::Recognize(request) => request,
        ServiceAction::Write(_) => panic!("FIXTURE_TECNICA expected recognition request"),
    }
}

fn outbound_bytes(action: ServiceAction) -> Vec<u8> {
    match action {
        ServiceAction::Write(outbound) => outbound.into_bytes(),
        ServiceAction::Recognize(_) => panic!("FIXTURE_TECNICA expected outbound bytes"),
    }
}

fn decode_outbound_frames(bytes: &[u8]) -> Vec<(Value, Option<Value>)> {
    let mut frames = Vec::new();
    let mut offset = 0_usize;
    while offset < bytes.len() {
        let newline = bytes[offset..]
            .iter()
            .position(|byte| *byte == b'\n')
            .map(|relative| offset + relative)
            .expect("FIXTURE_TECNICA response newline");
        let header: Value = serde_json::from_slice(&bytes[offset..newline])
            .expect("FIXTURE_TECNICA response header");
        offset = newline + 1;
        let data = header
            .get("data_length")
            .and_then(Value::as_u64)
            .map(|length| {
                let length = usize::try_from(length).expect("FIXTURE_TECNICA data length");
                let end = offset + length;
                let data = serde_json::from_slice(&bytes[offset..end])
                    .expect("FIXTURE_TECNICA response data");
                offset = end;
                data
            });
        frames.push((header, data));
    }
    frames
}

fn new_connection() -> ServiceConnection {
    ServiceConnection::new(MonotonicMillis::new(100)).expect("FIXTURE_TECNICA connection")
}

fn begin_recognition(connection: &mut ServiceConnection, now: u64) -> RecognitionRequest {
    recognition_request(one_action(
        connection
            .receive(&fixture_tecnica_transcript(), MonotonicMillis::new(now))
            .expect("FIXTURE_TECNICA transcript"),
    ))
}

#[test]
fn discovery_is_canonical_and_advertises_only_one_pt_br_intent_program() {
    let mut first = new_connection();
    let first_bytes = outbound_bytes(one_action(
        first
            .receive(FIXTURE_TECNICA_DESCRIBE, MonotonicMillis::new(101))
            .expect("FIXTURE_TECNICA describe"),
    ));
    let mut second = new_connection();
    let second_bytes = outbound_bytes(one_action(
        second
            .receive(FIXTURE_TECNICA_DESCRIBE, MonotonicMillis::new(101))
            .expect("FIXTURE_TECNICA describe"),
    ));
    assert_eq!(first_bytes, second_bytes);

    let frames = decode_outbound_frames(&first_bytes);
    let [(header, Some(info))] = frames.as_slice() else {
        panic!("FIXTURE_TECNICA expected one info frame");
    };
    assert_eq!(header["type"], "info");
    assert_eq!(header["version"], "1.10.0");
    assert!(header.get("payload_length").is_none());

    let info_object = info.as_object().expect("FIXTURE_TECNICA info object");
    assert_eq!(
        info_object.keys().map(String::as_str).collect::<Vec<_>>(),
        vec!["asr", "handle", "intent", "mic", "snd", "tts", "wake"]
    );
    assert_eq!(info["handle"], serde_json::json!([]));
    assert_eq!(info["asr"], serde_json::json!([]));
    assert_eq!(info["tts"], serde_json::json!([]));
    assert_eq!(info["wake"], serde_json::json!([]));
    assert_eq!(info["mic"], serde_json::json!([]));
    assert_eq!(info["snd"], serde_json::json!([]));

    let programs = info["intent"]
        .as_array()
        .expect("FIXTURE_TECNICA intent programs");
    assert_eq!(programs.len(), 1);
    assert_eq!(programs[0]["name"], "local-nlu");
    assert_eq!(programs[0]["installed"], true);
    assert_eq!(programs[0]["models"].as_array().map(Vec::len), Some(1));
    assert_eq!(
        programs[0]["models"][0]["languages"],
        serde_json::json!(["pt-BR"])
    );
    assert_eq!(programs[0]["models"][0]["installed"], true);
    assert!(
        !String::from_utf8(first_bytes)
            .expect("FIXTURE_TECNICA UTF-8")
            .contains("HandleProgram")
    );
}

#[test]
fn abstention_is_exact_not_recognized_without_context_or_payload() {
    let mut connection = new_connection();
    let request = begin_recognition(&mut connection, 101);
    let bytes = outbound_bytes(
        connection
            .complete(
                RecognitionCompletion::abstain(request.id()),
                MonotonicMillis::new(102),
            )
            .expect("FIXTURE_TECNICA abstention"),
    );
    assert_eq!(
        bytes,
        b"{\"type\":\"not-recognized\",\"version\":\"1.10.0\"}\n"
    );
}

#[test]
fn recognized_response_is_closed_request_bound_and_canonical() {
    let mut connection = new_connection();
    let request = begin_recognition(&mut connection, 101);
    let query =
        RevalidatedStateQuery::from_revalidated_exact_name("FIXTURE_TECNICA_ENTITY".to_owned())
            .expect("FIXTURE_TECNICA state query");
    let recognition =
        RevalidatedRecognition::single(request.id(), query).expect("FIXTURE_TECNICA recognition");
    let bytes = outbound_bytes(
        connection
            .complete(
                RecognitionCompletion::recognized(recognition),
                MonotonicMillis::new(102),
            )
            .expect("FIXTURE_TECNICA completion"),
    );

    let frames = decode_outbound_frames(&bytes);
    let [(header, Some(data))] = frames.as_slice() else {
        panic!("FIXTURE_TECNICA expected one intent frame");
    };
    assert_eq!(header["type"], "intent");
    assert_eq!(header["version"], "1.10.0");
    assert_eq!(
        data,
        &serde_json::json!({
            "name": "HassGetState",
            "entities": [{
                "name": "name",
                "value": "FIXTURE_TECNICA_ENTITY"
            }]
        })
    );
    assert!(data.get("service").is_none());
    assert!(data.get("payload").is_none());
    assert!(data.get("text").is_none());
    assert!(data.get("context").is_none());

    let canonical_data =
        b"{\"name\":\"HassGetState\",\"entities\":[{\"name\":\"name\",\"value\":\"FIXTURE_TECNICA_ENTITY\"}]}";
    let expected_header = format!(
        "{{\"type\":\"intent\",\"version\":\"1.10.0\",\"data_length\":{}}}\n",
        canonical_data.len()
    );
    let mut expected = expected_header.into_bytes();
    expected.extend_from_slice(canonical_data);
    assert_eq!(bytes, expected);
}

#[test]
fn independent_recognized_results_are_sorted_and_stream_bounded() {
    let mut connection = new_connection();
    let request = begin_recognition(&mut connection, 101);
    let queries = ["FIXTURE_TECNICA_B", "FIXTURE_TECNICA_A"]
        .into_iter()
        .map(|name| {
            RevalidatedStateQuery::from_revalidated_exact_name(name.to_owned())
                .expect("FIXTURE_TECNICA state query")
        })
        .collect();
    let recognition = RevalidatedRecognition::independent(request.id(), queries)
        .expect("FIXTURE_TECNICA recognition");
    let bytes = outbound_bytes(
        connection
            .complete(
                RecognitionCompletion::recognized(recognition),
                MonotonicMillis::new(102),
            )
            .expect("FIXTURE_TECNICA completion"),
    );
    let frames = decode_outbound_frames(&bytes);
    assert_eq!(frames.len(), 4);
    assert_eq!(frames[0].0["type"], "intents-start");
    assert_eq!(frames[1].0["type"], "intent");
    assert_eq!(
        frames[1].1.as_ref().expect("FIXTURE_TECNICA first")["entities"][0]["value"],
        "FIXTURE_TECNICA_A"
    );
    assert_eq!(
        frames[2].1.as_ref().expect("FIXTURE_TECNICA second")["entities"][0]["value"],
        "FIXTURE_TECNICA_B"
    );
    assert_eq!(frames[3].0["type"], "intents-stop");
}

#[test]
fn recognition_deadline_maps_to_not_recognized() {
    let mut connection = new_connection();
    let request = begin_recognition(&mut connection, 101);
    assert_eq!(request.deadline(), MonotonicMillis::new(5_101));
    assert!(
        connection
            .poll(MonotonicMillis::new(5_100))
            .expect("FIXTURE_TECNICA before deadline")
            .is_empty()
    );
    let bytes = outbound_bytes(one_action(
        connection
            .poll(MonotonicMillis::new(5_101))
            .expect("FIXTURE_TECNICA at deadline"),
    ));
    assert_eq!(
        bytes,
        b"{\"type\":\"not-recognized\",\"version\":\"1.10.0\"}\n"
    );
    assert!(!connection.has_pending_recognition());
}

#[test]
fn late_recognized_completion_abstains_without_emitting_intent() {
    let mut connection = new_connection();
    let request = begin_recognition(&mut connection, 101);
    let query =
        RevalidatedStateQuery::from_revalidated_exact_name("FIXTURE_TECNICA_ENTITY".to_owned())
            .expect("FIXTURE_TECNICA query");
    let recognition =
        RevalidatedRecognition::single(request.id(), query).expect("FIXTURE_TECNICA recognition");
    let bytes = outbound_bytes(
        connection
            .complete(
                RecognitionCompletion::recognized(recognition),
                request.deadline(),
            )
            .expect("FIXTURE_TECNICA late completion"),
    );
    assert_eq!(
        bytes,
        b"{\"type\":\"not-recognized\",\"version\":\"1.10.0\"}\n"
    );
}

#[test]
fn request_limit_connection_deadline_and_time_regression_close_connections() {
    let mut request_limited = new_connection();
    for offset in 0..MAX_REQUESTS_PER_CONNECTION {
        assert_eq!(
            request_limited
                .receive(
                    FIXTURE_TECNICA_DESCRIBE,
                    MonotonicMillis::new(
                        101 + u64::try_from(offset).expect("FIXTURE_TECNICA offset")
                    )
                )
                .expect("FIXTURE_TECNICA permitted request")
                .len(),
            1
        );
    }
    assert_eq!(
        request_limited
            .receive(FIXTURE_TECNICA_DESCRIBE, MonotonicMillis::new(200))
            .expect_err("FIXTURE_TECNICA request limit"),
        RuntimeError::RequestLimit
    );
    assert!(request_limited.is_closed());

    let mut deadline = new_connection();
    assert_eq!(
        deadline.connection_deadline(),
        MonotonicMillis::new(100 + CONNECTION_LIFETIME_MILLIS)
    );
    assert_eq!(
        deadline
            .receive(
                FIXTURE_TECNICA_DESCRIBE,
                MonotonicMillis::new(100 + CONNECTION_LIFETIME_MILLIS)
            )
            .expect_err("FIXTURE_TECNICA connection deadline"),
        RuntimeError::ConnectionDeadline
    );
    assert!(deadline.is_closed());

    let mut regressed = new_connection();
    regressed
        .poll(MonotonicMillis::new(102))
        .expect("FIXTURE_TECNICA monotonic advance");
    assert_eq!(
        regressed
            .poll(MonotonicMillis::new(101))
            .expect_err("FIXTURE_TECNICA time regression"),
        RuntimeError::TimeRegressed
    );
    assert!(regressed.is_closed());
}

#[test]
fn in_flight_pipelining_and_trailing_payload_smuggling_are_rejected() {
    let mut in_flight = new_connection();
    begin_recognition(&mut in_flight, 101);
    assert_eq!(
        in_flight
            .receive(FIXTURE_TECNICA_DESCRIBE, MonotonicMillis::new(102))
            .expect_err("FIXTURE_TECNICA in-flight request"),
        RuntimeError::RequestInFlight
    );
    assert!(in_flight.is_closed());

    let mut smuggled = fixture_tecnica_transcript();
    smuggled.extend_from_slice(b"FIXTURE_TECNICA_PAYLOAD");
    let mut connection = new_connection();
    assert_eq!(
        connection
            .receive(&smuggled, MonotonicMillis::new(101))
            .expect_err("FIXTURE_TECNICA trailing payload"),
        RuntimeError::PayloadNotAllowed
    );
    assert!(connection.is_closed());
}

#[test]
fn describe_and_recognize_can_be_coalesced_without_transport_assumptions() {
    let mut input = FIXTURE_TECNICA_DESCRIBE.to_vec();
    input.extend_from_slice(&fixture_tecnica_transcript());
    let mut connection = new_connection();
    let actions = connection
        .receive(&input, MonotonicMillis::new(101))
        .expect("FIXTURE_TECNICA coalesced service input");
    assert_eq!(actions.len(), 2);
    assert!(matches!(actions[0], ServiceAction::Write(_)));
    assert!(matches!(actions[1], ServiceAction::Recognize(_)));
}

#[test]
fn stale_completion_and_invalid_closed_results_fail() {
    let mut connection = new_connection();
    let first = begin_recognition(&mut connection, 101);
    connection
        .complete(
            RecognitionCompletion::abstain(first.id()),
            MonotonicMillis::new(102),
        )
        .expect("FIXTURE_TECNICA first completion");
    let _second = begin_recognition(&mut connection, 103);
    assert_eq!(
        connection
            .complete(
                RecognitionCompletion::abstain(first.id()),
                MonotonicMillis::new(104)
            )
            .expect_err("FIXTURE_TECNICA stale completion"),
        RuntimeError::WrongRecognitionRequest
    );
    assert!(connection.is_closed());

    assert_eq!(
        RevalidatedStateQuery::from_revalidated_exact_name(String::new())
            .expect_err("FIXTURE_TECNICA empty result"),
        RuntimeError::InvalidStateQuery
    );
    assert_eq!(
        RevalidatedStateQuery::from_revalidated_exact_name(
            "X".repeat(MAX_STATE_QUERY_NAME_BYTES + 1)
        )
        .expect_err("FIXTURE_TECNICA long result"),
        RuntimeError::InvalidStateQuery
    );

    let mut bounded = new_connection();
    let request = begin_recognition(&mut bounded, 101);
    let queries = (0..=MAX_REVALIDATED_INTENTS)
        .map(|index| {
            RevalidatedStateQuery::from_revalidated_exact_name(format!(
                "FIXTURE_TECNICA_{index:02}"
            ))
            .expect("FIXTURE_TECNICA query")
        })
        .collect();
    assert_eq!(
        RevalidatedRecognition::independent(request.id(), queries)
            .expect_err("FIXTURE_TECNICA intent limit"),
        RuntimeError::TooManyRecognizedIntents
    );
}
