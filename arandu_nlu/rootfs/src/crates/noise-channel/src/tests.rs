use super::*;

const FIXTURE_PSK: [u8; 32] = [0x42; 32];
const OTHER_PSK: [u8; 32] = [0x43; 32];
const FIXTURE_NONCE: [u8; 32] = [0x24; 32];
const OTHER_NONCE: [u8; 32] = [0x25; 32];
const FIXTURE_PAYLOAD: &[u8] = b"FIXTURE_TECNICA:P14:NOISE:PAYLOAD";

fn binding() -> ChannelBinding {
    ChannelBinding::new(
        PairingEpoch::new([7; 32]).expect("FIXTURE_TECNICA epoch must be valid"),
        ConnectionDirection::AddonInitiates,
        ConnectionNonce::from_bytes(FIXTURE_NONCE).expect("FIXTURE_TECNICA nonce must be valid"),
    )
}

fn other_binding() -> ChannelBinding {
    ChannelBinding::new(
        PairingEpoch::new([8; 32]).expect("FIXTURE_TECNICA epoch must be valid"),
        ConnectionDirection::AddonInitiates,
        ConnectionNonce::from_bytes(OTHER_NONCE).expect("FIXTURE_TECNICA nonce must be valid"),
    )
}

fn establish_with(binding: ChannelBinding, psk: &[u8; 32]) -> (NoiseChannel, NoiseChannel) {
    let (initiator, first) = InitiatorHandshake::start(binding, psk).expect("initiator must start");
    let responder = ResponderHandshake::new(binding, psk).expect("responder must start");
    let (responder, response) = responder
        .accept(&first)
        .expect("responder must authenticate");
    let initiator = initiator
        .finish(&response)
        .expect("initiator must authenticate");
    (initiator, responder)
}

fn establish() -> (NoiseChannel, NoiseChannel) {
    establish_with(binding(), &FIXTURE_PSK)
}

fn data_bytes(frame: ReceivedFrame) -> Vec<u8> {
    match frame {
        ReceivedFrame::Data(plaintext) => plaintext.as_bytes().to_vec(),
        ReceivedFrame::Close => panic!("expected FIXTURE_TECNICA data frame"),
    }
}

#[test]
fn successful_handshake_proves_mutual_psk_possession() {
    let (mut initiator, mut responder) = establish();

    let request = initiator
        .send(b"FIXTURE_TECNICA:P14:NOISE:REQUEST")
        .expect("initiator encryption must succeed");
    assert_eq!(
        data_bytes(
            responder
                .receive(request.as_bytes())
                .expect("responder decryption must succeed")
        ),
        b"FIXTURE_TECNICA:P14:NOISE:REQUEST"
    );

    let response = responder
        .send(b"FIXTURE_TECNICA:P14:NOISE:RESPONSE")
        .expect("responder encryption must succeed");
    assert_eq!(
        data_bytes(
            initiator
                .receive(response.as_bytes())
                .expect("initiator decryption must succeed")
        ),
        b"FIXTURE_TECNICA:P14:NOISE:RESPONSE"
    );
}

#[test]
fn wrong_psk_fails_closed() {
    let (initiator, first) =
        InitiatorHandshake::start(binding(), &FIXTURE_PSK).expect("initiator must start");
    let responder =
        ResponderHandshake::new(binding(), &OTHER_PSK).expect("responder must initialize");

    assert_eq!(
        responder
            .accept(&first)
            .expect_err("wrong PSK must not authenticate"),
        ChannelError::HandshakeRejected
    );
    initiator.close();
}

#[test]
fn every_prologue_field_substitution_fails_closed() {
    const FIELDS: [&[u8]; 6] = [
        b"protocol",
        b"epoch",
        b"initiator",
        b"responder",
        b"direction",
        b"connection_nonce",
    ];

    for field in FIELDS {
        let (initiator, first) =
            InitiatorHandshake::start(binding(), &FIXTURE_PSK).expect("initiator must start");
        let mut substituted = binding()
            .encode_prologue()
            .expect("canonical prologue must encode");
        flip_prologue_field(&mut substituted, field);
        let responder_state =
            build_handshake_with_prologue(&FIXTURE_PSK, &substituted, Side::Responder)
                .expect("substituted responder state must initialize");
        let responder = ResponderHandshake {
            state: Some(responder_state),
            binding: binding(),
        };

        assert_eq!(
            responder
                .accept(&first)
                .expect_err("substituted prologue must not authenticate"),
            ChannelError::HandshakeRejected,
            "FIXTURE_TECNICA prologue field: {}",
            String::from_utf8_lossy(field)
        );
        initiator.close();
    }
}

fn flip_prologue_field(prologue: &mut [u8], target: &[u8]) {
    let mut cursor = PROLOGUE_PREFIX.len();
    while cursor < prologue.len() {
        let name_length = usize::from(u16::from_be_bytes([prologue[cursor], prologue[cursor + 1]]));
        cursor += 2;
        let name_start = cursor;
        let name_end = name_start + name_length;
        cursor = name_end;
        let value_length =
            usize::from(u16::from_be_bytes([prologue[cursor], prologue[cursor + 1]]));
        cursor += 2;
        let value_start = cursor;
        let value_end = value_start + value_length;
        if &prologue[name_start..name_end] == target {
            prologue[value_start] ^= 1;
            return;
        }
        cursor = value_end;
    }
    panic!("FIXTURE_TECNICA prologue field must exist");
}

#[test]
fn replayed_transport_frame_closes_receiver() {
    let (mut initiator, mut responder) = establish();
    let frame = initiator.send(FIXTURE_PAYLOAD).expect("send must work");
    responder
        .receive(frame.as_bytes())
        .expect("first delivery must work");

    assert_eq!(
        responder
            .receive(frame.as_bytes())
            .expect_err("replay must fail"),
        ChannelError::FrameRejected
    );
    assert!(responder.is_closed());
}

#[test]
fn wrong_message_direction_closes_receiver() {
    let (initiator, mut responder) = establish();
    let frame = initiator.fixture_frame(
        0,
        binding(),
        MessageDirection::ResponderToInitiator,
        0,
        FIXTURE_PAYLOAD,
    );

    assert_eq!(
        responder
            .receive(frame.as_bytes())
            .expect_err("reflected direction must fail"),
        ChannelError::FrameRejected
    );
    assert!(responder.is_closed());
}

#[test]
fn cross_connection_and_bound_connection_substitution_fail() {
    let (mut first_initiator, _) = establish_with(binding(), &FIXTURE_PSK);
    let (_, mut second_responder) = establish_with(other_binding(), &FIXTURE_PSK);
    let cross_connection = first_initiator
        .send(FIXTURE_PAYLOAD)
        .expect("first connection send must work");

    assert_eq!(
        second_responder
            .receive(cross_connection.as_bytes())
            .expect_err("cross-connection ciphertext must fail"),
        ChannelError::FrameRejected
    );
    assert!(second_responder.is_closed());

    let (initiator, mut responder) = establish();
    let substituted = initiator.fixture_frame(
        0,
        other_binding(),
        MessageDirection::InitiatorToResponder,
        0,
        FIXTURE_PAYLOAD,
    );
    assert_eq!(
        responder
            .receive(substituted.as_bytes())
            .expect_err("authenticated wrong binding must fail"),
        ChannelError::FrameRejected
    );
    assert!(responder.is_closed());
}

#[test]
fn sequence_gaps_and_overflow_fail_closed() {
    let (initiator, mut responder) = establish();
    let gap = initiator.fixture_frame(
        0,
        binding(),
        MessageDirection::InitiatorToResponder,
        1,
        FIXTURE_PAYLOAD,
    );
    assert_eq!(
        responder
            .receive(gap.as_bytes())
            .expect_err("authenticated sequence gap must fail"),
        ChannelError::FrameRejected
    );
    assert!(responder.is_closed());

    let (mut initiator, mut responder) = establish();
    initiator.set_sequences_for_test(u64::MAX - 1, 0);
    responder.set_sequences_for_test(0, u64::MAX - 1);
    let terminal = initiator
        .send(FIXTURE_PAYLOAD)
        .expect("last permitted sequence must work");
    assert_eq!(
        data_bytes(
            responder
                .receive(terminal.as_bytes())
                .expect("last permitted sequence must authenticate")
        ),
        FIXTURE_PAYLOAD
    );
    assert_eq!(
        initiator
            .send(FIXTURE_PAYLOAD)
            .expect_err("reserved terminal nonce must fail"),
        ChannelError::SequenceExhausted
    );
    assert!(initiator.is_closed());
    assert_eq!(
        responder
            .receive(terminal.as_bytes())
            .expect_err("receiver at reserved terminal nonce must fail"),
        ChannelError::SequenceExhausted
    );
    assert!(responder.is_closed());
}

#[test]
fn deterministic_rekey_keeps_both_peers_synchronized() {
    let (mut initiator, mut responder) = establish();
    initiator.set_sequences_for_test(REKEY_INTERVAL - 1, 0);
    responder.set_sequences_for_test(0, REKEY_INTERVAL - 1);

    let before_rekey = initiator
        .send(FIXTURE_PAYLOAD)
        .expect("rekey boundary frame must encrypt");
    responder
        .receive(before_rekey.as_bytes())
        .expect("rekey boundary frame must decrypt");
    let after_rekey = initiator
        .send(FIXTURE_PAYLOAD)
        .expect("post-rekey frame must encrypt");
    assert_eq!(
        data_bytes(
            responder
                .receive(after_rekey.as_bytes())
                .expect("post-rekey frame must decrypt")
        ),
        FIXTURE_PAYLOAD
    );
}

#[test]
fn frame_limits_are_exact_and_fail_closed() {
    let (mut initiator, mut responder) = establish();
    let maximum = vec![0x5a; MAX_PAYLOAD_LEN];
    let frame = initiator
        .send(&maximum)
        .expect("maximum payload must encrypt");
    assert_eq!(frame.as_bytes().len(), MAX_ENCRYPTED_FRAME_LEN);
    assert_eq!(
        data_bytes(
            responder
                .receive(frame.as_bytes())
                .expect("maximum payload must decrypt")
        ),
        maximum
    );

    let too_large = vec![0x5a; MAX_PAYLOAD_LEN + 1];
    assert_eq!(
        initiator
            .send(&too_large)
            .expect_err("oversized outbound payload must fail"),
        ChannelError::FrameTooLarge
    );
    assert!(!initiator.is_closed());

    let small = initiator
        .send(FIXTURE_PAYLOAD)
        .expect("local size rejection must not consume sequence");
    assert_eq!(
        data_bytes(
            responder
                .receive(small.as_bytes())
                .expect("next bounded frame must decrypt")
        ),
        FIXTURE_PAYLOAD
    );

    let (_, mut malformed_receiver) = establish();
    assert_eq!(
        malformed_receiver
            .receive(&[0_u8; MIN_ENCRYPTED_FRAME_LEN - 1])
            .expect_err("undersized wire frame must fail"),
        ChannelError::FrameRejected
    );
    assert!(malformed_receiver.is_closed());

    let (mut sender, mut prefix_receiver) = establish();
    let valid = sender.send(FIXTURE_PAYLOAD).expect("send must work");
    let mut mismatched_prefix = valid.as_bytes().to_vec();
    mismatched_prefix[1] = mismatched_prefix[1].wrapping_add(1);
    assert_eq!(
        prefix_receiver
            .receive(&mismatched_prefix)
            .expect_err("length prefix mismatch must fail"),
        ChannelError::FrameRejected
    );
    assert!(prefix_receiver.is_closed());
}

#[test]
fn orderly_and_immediate_close_drop_transport_state() {
    let (mut initiator, mut responder) = establish();
    let initiator_close = initiator.send_close().expect("close frame must encrypt");
    assert!(matches!(
        responder
            .receive(initiator_close.as_bytes())
            .expect("close frame must authenticate"),
        ReceivedFrame::Close
    ));
    let responder_close = responder.send_close().expect("close reply must encrypt");
    assert!(responder.is_closed());
    assert!(matches!(
        initiator
            .receive(responder_close.as_bytes())
            .expect("close reply must authenticate"),
        ReceivedFrame::Close
    ));
    assert!(initiator.is_closed());

    let (mut immediate, _) = establish();
    immediate.close();
    assert!(immediate.is_closed());
    assert_eq!(
        immediate
            .send(FIXTURE_PAYLOAD)
            .expect_err("closed channel must reject sends"),
        ChannelError::Closed
    );
}

#[test]
fn debug_and_error_output_are_redacted() {
    let secret_text = "FIXTURE_TECNICA:P14:DO_NOT_LOG_SECRET";
    let secret_psk = [0xab; 32];
    let secret_nonce = ConnectionNonce::from_bytes([0xcd; 32]).expect("nonce must be valid");
    let secret_binding = ChannelBinding::new(
        PairingEpoch::new([99; 32]).expect("epoch must be valid"),
        ConnectionDirection::AddonInitiates,
        secret_nonce,
    );
    let (initiator_handshake, first) =
        InitiatorHandshake::start(secret_binding, &secret_psk).expect("handshake must start");
    let responder_handshake =
        ResponderHandshake::new(secret_binding, &secret_psk).expect("handshake must start");
    let (mut responder, response) = responder_handshake
        .accept(&first)
        .expect("responder must authenticate");
    let mut initiator = initiator_handshake
        .finish(&response)
        .expect("initiator must authenticate");
    let encrypted = initiator
        .send(secret_text.as_bytes())
        .expect("secret fixture must encrypt");
    let plaintext = responder
        .receive(encrypted.as_bytes())
        .expect("secret fixture must decrypt");

    let outputs = [
        format!("{secret_nonce:?}"),
        format!("{secret_binding:?}"),
        format!("{first:?}"),
        format!("{response:?}"),
        format!("{initiator:?}"),
        format!("{encrypted:?}"),
        format!("{plaintext:?}"),
        format!("{:?}", ChannelError::HandshakeRejected),
        ChannelError::FrameRejected.to_string(),
    ];
    for output in outputs {
        assert!(!output.contains(secret_text));
        assert!(!output.contains("abababab"));
        assert!(!output.contains("cdcdcdcd"));
        assert!(!output.contains("Snow"));
        assert!(!output.contains("Decrypt"));
    }
}

#[test]
fn zero_epoch_and_zero_connection_nonce_are_rejected() {
    assert_eq!(
        PairingEpoch::new([0; 32]).expect_err("zero epoch must fail"),
        ChannelError::InvalidBinding
    );
    assert_eq!(
        ConnectionNonce::from_bytes([0; 32]).expect_err("zero nonce must fail"),
        ChannelError::InvalidBinding
    );
}
