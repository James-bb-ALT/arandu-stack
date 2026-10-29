use std::io::Read;
use std::os::unix::net::UnixStream;
use std::thread;
use std::time::Duration;

use addon_runtime::{CompanionSubmission, accept_noise_stream, read_helper_reply};
pub use addon_runtime::{
    HelperReply, MAX_COMPANION_WIRE_BYTES, NoiseStream, Result, RuntimeError,
    initiate_noise_stream, read_submission, relay_companion_submission, write_helper_reply,
};
use noise_channel::{ConnectionDirection, PairingEpoch, ReceivedFrame};

const FIXTURE_TECNICA_CREDENTIAL: [u8; 32] = [9_u8; 32];
const FIXTURE_TECNICA_GRAPH: &[u8] = br#"{"direction":"adapter_to_companion","fixture_tecnica_graph":"FIXTURE_TECNICA_GRAPH","version":1}"#;
const FIXTURE_TECNICA_MALFORMED_GRAPH: &[u8] = br#"["FIXTURE_TECNICA_PRIVATE_GRAPH_CANARY"]"#;

fn pair() -> (UnixStream, UnixStream) {
    let pair = UnixStream::pair().expect("FIXTURE_TECNICA Unix pair");
    for stream in [&pair.0, &pair.1] {
        stream
            .set_read_timeout(Some(Duration::from_secs(2)))
            .expect("FIXTURE_TECNICA read timeout");
        stream
            .set_write_timeout(Some(Duration::from_secs(2)))
            .expect("FIXTURE_TECNICA write timeout");
    }
    pair
}

fn epoch() -> PairingEpoch {
    PairingEpoch::new([7_u8; 32]).expect("FIXTURE_TECNICA epoch")
}

fn expected_submission() -> CompanionSubmission {
    CompanionSubmission::new(
        "FIXTURE_TECNICA_TEXT".to_owned(),
        "FIXTURE_TECNICA_CONTEXT".to_owned(),
        "FIXTURE_TECNICA_CALLER".to_owned(),
        Some("FIXTURE_TECNICA_CONVERSATION".to_owned()),
    )
    .expect("FIXTURE_TECNICA submission")
}

fn write_noncanonical_submission(stream: &mut UnixStream) {
    let payload = br#"{
        "version": 1,
        "text": "FIXTURE_TECNICA_TEXT",
        "language": "pt-BR",
        "conversation_id": "FIXTURE_TECNICA_CONVERSATION",
        "context_id": "FIXTURE_TECNICA_CONTEXT",
        "caller_id": "FIXTURE_TECNICA_CALLER"
    }"#;
    nlu_server::write_frame(stream, payload).expect("FIXTURE_TECNICA local submission");
}

#[test]
fn canonical_submission_one_graph_and_mutual_close_relay() {
    let (mut companion_io, mut helper_local) = pair();
    let (helper_noise, adapter_io) = pair();
    write_noncanonical_submission(&mut companion_io);

    let adapter = thread::spawn(move || {
        let mut channel = accept_noise_stream(
            adapter_io,
            epoch(),
            ConnectionDirection::CompanionInitiates,
            &FIXTURE_TECNICA_CREDENTIAL,
        )
        .expect("FIXTURE_TECNICA adapter handshake");
        assert_eq!(
            channel.binding().direction(),
            ConnectionDirection::CompanionInitiates
        );
        let ReceivedFrame::Data(submission) =
            channel.receive().expect("FIXTURE_TECNICA submission")
        else {
            panic!("FIXTURE_TECNICA expected submission data");
        };
        let canonical_submission = submission.as_bytes().to_vec();
        channel
            .send(FIXTURE_TECNICA_GRAPH)
            .expect("FIXTURE_TECNICA graph");
        assert!(matches!(
            channel.receive().expect("FIXTURE_TECNICA helper close"),
            ReceivedFrame::Close
        ));
        channel.send_close().expect("FIXTURE_TECNICA adapter close");
        canonical_submission
    });

    let returned = relay_companion_submission(
        &mut helper_local,
        move || Ok(helper_noise),
        epoch(),
        &FIXTURE_TECNICA_CREDENTIAL,
    )
    .expect("FIXTURE_TECNICA relay");
    let local_reply =
        read_helper_reply(&mut companion_io).expect("FIXTURE_TECNICA typed local reply");
    assert_eq!(local_reply, returned);
    assert_eq!(
        returned
            .authenticated_wire()
            .expect("FIXTURE_TECNICA authenticated wire")
            .expect("FIXTURE_TECNICA authenticated variant"),
        FIXTURE_TECNICA_GRAPH
    );
    assert!(matches!(
        returned,
        HelperReply::Authenticated {
            opened_connection: true,
            ..
        }
    ));
    assert_eq!(
        adapter.join().expect("FIXTURE_TECNICA adapter"),
        expected_submission()
            .encode()
            .expect("FIXTURE_TECNICA canonical submission")
    );
}

#[test]
fn malformed_authenticated_payload_closes_both_directions_and_returns_no_reply() {
    let (mut companion_io, mut helper_local) = pair();
    let (helper_noise, adapter_io) = pair();
    write_noncanonical_submission(&mut companion_io);

    let adapter = thread::spawn(move || {
        let mut channel = accept_noise_stream(
            adapter_io,
            epoch(),
            ConnectionDirection::CompanionInitiates,
            &FIXTURE_TECNICA_CREDENTIAL,
        )
        .expect("FIXTURE_TECNICA adapter handshake");
        assert!(matches!(
            channel.receive().expect("FIXTURE_TECNICA submission"),
            ReceivedFrame::Data(_)
        ));
        channel
            .send(FIXTURE_TECNICA_MALFORMED_GRAPH)
            .expect("FIXTURE_TECNICA malformed graph");
        let helper_closed = matches!(
            channel.receive().expect("FIXTURE_TECNICA helper close"),
            ReceivedFrame::Close
        );
        channel.send_close().expect("FIXTURE_TECNICA adapter close");
        helper_closed
    });

    let error = relay_companion_submission(
        &mut helper_local,
        move || Ok(helper_noise),
        epoch(),
        &FIXTURE_TECNICA_CREDENTIAL,
    )
    .expect_err("FIXTURE_TECNICA malformed graph must fail");
    assert_eq!(error, RuntimeError::InvalidHelperReply);
    assert!(
        !format!("{error:?}").contains("FIXTURE_TECNICA_PRIVATE_GRAPH_CANARY"),
        "FIXTURE_TECNICA errors redact graph content"
    );
    assert!(adapter.join().expect("FIXTURE_TECNICA adapter close"));

    drop(helper_local);
    let mut output = [0_u8; 1];
    assert_eq!(
        companion_io
            .read(&mut output)
            .expect("FIXTURE_TECNICA local EOF"),
        0
    );
}

#[test]
fn second_authenticated_payload_is_rejected_before_local_delivery() {
    let (mut companion_io, mut helper_local) = pair();
    let (helper_noise, adapter_io) = pair();
    write_noncanonical_submission(&mut companion_io);

    let adapter = thread::spawn(move || {
        let mut channel = accept_noise_stream(
            adapter_io,
            epoch(),
            ConnectionDirection::CompanionInitiates,
            &FIXTURE_TECNICA_CREDENTIAL,
        )
        .expect("FIXTURE_TECNICA adapter handshake");
        assert!(matches!(
            channel.receive().expect("FIXTURE_TECNICA submission"),
            ReceivedFrame::Data(_)
        ));
        channel
            .send(FIXTURE_TECNICA_GRAPH)
            .expect("FIXTURE_TECNICA first graph");
        channel
            .send(FIXTURE_TECNICA_GRAPH)
            .expect("FIXTURE_TECNICA second graph");
        matches!(
            channel.receive().expect("FIXTURE_TECNICA helper close"),
            ReceivedFrame::Close
        )
    });

    assert_eq!(
        relay_companion_submission(
            &mut helper_local,
            move || Ok(helper_noise),
            epoch(),
            &FIXTURE_TECNICA_CREDENTIAL,
        )
        .expect_err("FIXTURE_TECNICA duplicate graph must fail"),
        RuntimeError::ChannelFrameRejected
    );
    assert!(adapter.join().expect("FIXTURE_TECNICA adapter close"));

    drop(helper_local);
    let mut output = [0_u8; 1];
    assert_eq!(
        companion_io
            .read(&mut output)
            .expect("FIXTURE_TECNICA local EOF"),
        0
    );
}

#[test]
fn close_before_graph_is_acknowledged_and_fails_closed() {
    let (mut companion_io, mut helper_local) = pair();
    let (helper_noise, adapter_io) = pair();
    write_noncanonical_submission(&mut companion_io);

    let adapter = thread::spawn(move || {
        let mut channel = accept_noise_stream(
            adapter_io,
            epoch(),
            ConnectionDirection::CompanionInitiates,
            &FIXTURE_TECNICA_CREDENTIAL,
        )
        .expect("FIXTURE_TECNICA adapter handshake");
        assert!(matches!(
            channel.receive().expect("FIXTURE_TECNICA submission"),
            ReceivedFrame::Data(_)
        ));
        channel
            .send_close()
            .expect("FIXTURE_TECNICA early adapter close");
        matches!(
            channel
                .receive()
                .expect("FIXTURE_TECNICA reciprocal helper close"),
            ReceivedFrame::Close
        )
    });

    assert_eq!(
        relay_companion_submission(
            &mut helper_local,
            move || Ok(helper_noise),
            epoch(),
            &FIXTURE_TECNICA_CREDENTIAL,
        )
        .expect_err("FIXTURE_TECNICA close before graph must fail"),
        RuntimeError::ChannelFrameRejected
    );
    assert!(adapter.join().expect("FIXTURE_TECNICA adapter close"));
}
