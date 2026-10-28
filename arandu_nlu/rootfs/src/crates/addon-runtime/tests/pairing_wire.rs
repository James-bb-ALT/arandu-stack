use std::os::unix::net::UnixStream;
use std::thread;
use std::time::Duration;

use addon_runtime::{PairingBindings, RuntimeError, accept_pairing, initiate_pairing};

const FIXTURE_TECNICA_CREDENTIAL: [u8; 32] = *b"FIXTURE_TECNICA_0123456789ABCDEF";
const FIXTURE_TECNICA_PEER: &str = "FIXTURE_TECNICA_ADDON";
const FIXTURE_TECNICA_PORT: u16 = 10_702;

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
fn authenticated_offer_round_trip_is_exact_and_mutually_closed() {
    let (initiator, responder) = pair();
    let bindings =
        PairingBindings::derive(&FIXTURE_TECNICA_CREDENTIAL).expect("FIXTURE_TECNICA bindings");
    let accepted = thread::spawn(move || {
        accept_pairing(responder, bindings, &FIXTURE_TECNICA_CREDENTIAL)
            .expect("FIXTURE_TECNICA accept")
    });

    initiate_pairing(
        initiator,
        bindings,
        &FIXTURE_TECNICA_CREDENTIAL,
        FIXTURE_TECNICA_PEER,
        FIXTURE_TECNICA_PORT,
    )
    .expect("FIXTURE_TECNICA initiate");

    let offer = accepted.join().expect("FIXTURE_TECNICA responder");
    assert_eq!(offer.pairing_id(), bindings.pairing_id_hex());
    assert_eq!(offer.peer_id(), FIXTURE_TECNICA_PEER);
    assert_eq!(offer.relay_port(), FIXTURE_TECNICA_PORT);
    assert_eq!(format!("{offer:?}"), "PairingOffer(bindings=redacted)");
}

#[test]
fn different_credential_cannot_complete_pairing() {
    let (initiator, responder) = pair();
    let expected =
        PairingBindings::derive(&FIXTURE_TECNICA_CREDENTIAL).expect("FIXTURE_TECNICA expected");
    let changed = [0x44_u8; 32];
    let offered = PairingBindings::derive(&changed).expect("FIXTURE_TECNICA changed");
    let accepted = thread::spawn(move || {
        accept_pairing(responder, expected, &FIXTURE_TECNICA_CREDENTIAL)
            .expect_err("FIXTURE_TECNICA wrong credential")
    });

    let error = initiate_pairing(
        initiator,
        offered,
        &changed,
        FIXTURE_TECNICA_PEER,
        FIXTURE_TECNICA_PORT,
    )
    .expect_err("FIXTURE_TECNICA mismatch");
    assert!(matches!(
        error,
        RuntimeError::ChannelIo | RuntimeError::ChannelHandshakeRejected
    ));
    assert!(matches!(
        accepted.join().expect("FIXTURE_TECNICA responder"),
        RuntimeError::ChannelOfferRejected | RuntimeError::ChannelHandshakeRejected
    ));
}
