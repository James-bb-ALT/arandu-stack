use std::io::{Read, Write};

use noise_channel::{ConnectionDirection, PairingEpoch, ReceivedFrame};

use crate::{
    CompanionSubmission, MAX_COMPANION_WIRE_BYTES, NoiseStream, Result, RuntimeError,
    accept_noise_stream,
};

pub fn serve_companion_once<T, F>(
    transport: T,
    epoch: PairingEpoch,
    pairing_credential: &[u8; 32],
    interpret: F,
) -> Result<()>
where
    T: Read + Write,
    F: FnOnce(noise_channel::ChannelBinding, &CompanionSubmission) -> Result<Vec<u8>>,
{
    let mut channel = accept_noise_stream(
        transport,
        epoch,
        ConnectionDirection::CompanionInitiates,
        pairing_credential,
    )?;
    let binding = channel.binding();
    let ReceivedFrame::Data(plaintext) = channel.receive()? else {
        channel.close();
        return Err(RuntimeError::InvalidSubmission);
    };
    let submission = CompanionSubmission::decode(plaintext.as_bytes())?;
    let response = interpret(binding, &submission)?;
    validate_response(&response)?;
    channel.send(&response)?;
    expect_close(&mut channel)?;
    channel.send_close()
}

fn validate_response(response: &[u8]) -> Result<()> {
    if response.is_empty() || response.len() > MAX_COMPANION_WIRE_BYTES {
        return Err(RuntimeError::WireTooLarge);
    }
    let value = nlu_data::parse_strict_json(response, "companion relay response")
        .map_err(|_| RuntimeError::WireEncoding)?;
    if !value.is_object() {
        return Err(RuntimeError::WireEncoding);
    }
    Ok(())
}

fn expect_close<T>(channel: &mut NoiseStream<T>) -> Result<()>
where
    T: Read + Write,
{
    if matches!(channel.receive()?, ReceivedFrame::Close) {
        Ok(())
    } else {
        channel.close();
        Err(RuntimeError::ChannelFrameRejected)
    }
}

#[cfg(test)]
mod tests {
    use std::os::unix::net::UnixStream;
    use std::thread;
    use std::time::Duration;

    use noise_channel::{ConnectionDirection, PairingEpoch, ReceivedFrame};

    use crate::{CompanionSubmission, RuntimeError, initiate_noise_stream};

    use super::*;

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

    fn submission() -> CompanionSubmission {
        CompanionSubmission::new(
            "FIXTURE_TECNICA_INPUT".to_owned(),
            "FIXTURE_TECNICA_CONTEXT".to_owned(),
            "FIXTURE_TECNICA_CALLER".to_owned(),
            None,
        )
        .expect("FIXTURE_TECNICA submission")
    }

    #[test]
    fn companion_initiated_submission_receives_one_bound_response_and_close() {
        let (helper_io, adapter_io) = pair();
        let epoch = PairingEpoch::new([7; 32]).expect("FIXTURE_TECNICA epoch");
        let credential = [9_u8; 32];
        let expected = submission();
        let responder = thread::spawn(move || {
            serve_companion_once(adapter_io, epoch, &credential, |binding, received| {
                assert_eq!(received, &expected);
                assert_eq!(binding.direction(), ConnectionDirection::CompanionInitiates);
                Ok(br#"{"FIXTURE_TECNICA_graph":true}"#.to_vec())
            })
        });

        let mut helper = initiate_noise_stream(
            helper_io,
            epoch,
            ConnectionDirection::CompanionInitiates,
            &credential,
        )
        .expect("FIXTURE_TECNICA helper handshake");
        helper
            .send(
                &submission()
                    .encode()
                    .expect("FIXTURE_TECNICA encoded submission"),
            )
            .expect("FIXTURE_TECNICA send submission");
        let ReceivedFrame::Data(response) = helper.receive().expect("FIXTURE_TECNICA response")
        else {
            panic!("FIXTURE_TECNICA expected response");
        };
        assert_eq!(response.as_bytes(), br#"{"FIXTURE_TECNICA_graph":true}"#);
        helper.send_close().expect("FIXTURE_TECNICA helper close");
        assert!(matches!(
            helper.receive().expect("FIXTURE_TECNICA adapter close"),
            ReceivedFrame::Close
        ));
        responder
            .join()
            .expect("FIXTURE_TECNICA responder")
            .expect("FIXTURE_TECNICA relay");
    }

    #[test]
    fn non_object_response_is_rejected_before_channel_send() {
        assert_eq!(
            validate_response(br#"["FIXTURE_TECNICA_OPEN"]"#)
                .expect_err("FIXTURE_TECNICA object required"),
            RuntimeError::WireEncoding
        );
    }
}
