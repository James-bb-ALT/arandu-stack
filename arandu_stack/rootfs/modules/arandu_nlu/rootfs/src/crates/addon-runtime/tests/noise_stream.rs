use std::os::unix::net::UnixStream;
use std::thread;
use std::time::Duration;

use addon_runtime::{RuntimeError, accept_noise_stream, initiate_noise_stream};
use noise_channel::{ConnectionDirection, PairingEpoch, ReceivedFrame};

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

#[test]
fn real_noise_handshake_data_and_close_round_trip() {
    let (initiator_io, responder_io) = pair();
    let epoch = PairingEpoch::new([7; 32]).expect("FIXTURE_TECNICA epoch");
    let credential = [9_u8; 32];
    let responder = thread::spawn(move || {
        let mut stream = accept_noise_stream(
            responder_io,
            epoch,
            ConnectionDirection::AddonInitiates,
            &credential,
        )
        .expect("FIXTURE_TECNICA responder handshake");
        let ReceivedFrame::Data(request) = stream.receive().expect("FIXTURE_TECNICA request")
        else {
            panic!("FIXTURE_TECNICA expected data");
        };
        assert_eq!(request.as_bytes(), b"FIXTURE_TECNICA_REQUEST");
        stream
            .send(b"FIXTURE_TECNICA_RESPONSE")
            .expect("FIXTURE_TECNICA response");
        assert!(matches!(
            stream.receive().expect("FIXTURE_TECNICA close"),
            ReceivedFrame::Close
        ));
        stream.send_close().expect("FIXTURE_TECNICA close reply");
    });

    let mut initiator = initiate_noise_stream(
        initiator_io,
        epoch,
        ConnectionDirection::AddonInitiates,
        &credential,
    )
    .expect("FIXTURE_TECNICA initiator handshake");
    initiator
        .send(b"FIXTURE_TECNICA_REQUEST")
        .expect("FIXTURE_TECNICA request");
    let ReceivedFrame::Data(response) = initiator.receive().expect("FIXTURE_TECNICA response")
    else {
        panic!("FIXTURE_TECNICA expected data");
    };
    assert_eq!(response.as_bytes(), b"FIXTURE_TECNICA_RESPONSE");
    initiator
        .send_close()
        .expect("FIXTURE_TECNICA initiator close");
    assert!(matches!(
        initiator.receive().expect("FIXTURE_TECNICA close reply"),
        ReceivedFrame::Close
    ));
    responder.join().expect("FIXTURE_TECNICA responder");
}

#[test]
fn epoch_substitution_fails_before_transport_mode() {
    let (initiator_io, responder_io) = pair();
    let offered_epoch = PairingEpoch::new([7; 32]).expect("FIXTURE_TECNICA offered epoch");
    let expected_epoch = PairingEpoch::new([8; 32]).expect("FIXTURE_TECNICA expected epoch");
    let credential = [9_u8; 32];
    let responder = thread::spawn(move || {
        accept_noise_stream(
            responder_io,
            expected_epoch,
            ConnectionDirection::AddonInitiates,
            &credential,
        )
        .expect_err("epoch mismatch")
    });
    let initiator_error = initiate_noise_stream(
        initiator_io,
        offered_epoch,
        ConnectionDirection::AddonInitiates,
        &credential,
    )
    .expect_err("responder closes rejected offer");
    assert!(matches!(
        initiator_error,
        RuntimeError::ChannelIo | RuntimeError::ChannelHandshakeRejected
    ));
    assert_eq!(
        responder.join().expect("FIXTURE_TECNICA responder"),
        RuntimeError::ChannelOfferRejected
    );
}
