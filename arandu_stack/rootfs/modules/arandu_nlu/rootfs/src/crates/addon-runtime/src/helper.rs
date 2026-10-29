use std::io::{Read, Write};

use noise_channel::{ConnectionDirection, PairingEpoch, ReceivedFrame};

use crate::{
    HelperReply, MAX_COMPANION_WIRE_BYTES, Result, RuntimeError, initiate_noise_stream,
    read_submission, write_helper_reply,
};

/// Relays one validated companion submission over one fresh Noise connection.
///
/// The caller must authenticate `local_stream` with kernel peer credentials
/// before calling this function. The connector is invoked only after the
/// bounded submission has decoded and re-encoded to its canonical bytes.
pub fn relay_companion_submission<Local, Transport, Connect>(
    local_stream: &mut Local,
    connect: Connect,
    epoch: PairingEpoch,
    pairing_credential: &[u8; 32],
) -> Result<HelperReply>
where
    Local: Read + Write,
    Transport: Read + Write,
    Connect: FnOnce() -> Result<Transport>,
{
    let submission = read_submission(local_stream)?;
    let canonical_submission = submission.encode()?;
    let transport = connect()?;
    let mut channel = initiate_noise_stream(
        transport,
        epoch,
        ConnectionDirection::CompanionInitiates,
        pairing_credential,
    )?;
    channel.send(&canonical_submission)?;

    let reply_result = match channel.receive() {
        Ok(ReceivedFrame::Data(payload)) => {
            let bytes = payload.as_bytes();
            if bytes.is_empty() || bytes.len() > MAX_COMPANION_WIRE_BYTES {
                Err(RuntimeError::InvalidHelperReply)
            } else {
                HelperReply::authenticated(bytes, true)
            }
        }
        Ok(ReceivedFrame::Close) => {
            let close_result = channel.send_close();
            channel.close();
            close_result?;
            return Err(RuntimeError::ChannelFrameRejected);
        }
        Err(error) => {
            channel.close();
            return Err(error);
        }
    };

    let close_result = close_after_one_payload(&mut channel);
    channel.close();
    close_result?;

    let reply = reply_result?;
    write_helper_reply(local_stream, &reply)?;
    Ok(reply)
}

fn close_after_one_payload<Transport>(channel: &mut crate::NoiseStream<Transport>) -> Result<()>
where
    Transport: Read + Write,
{
    channel.send_close()?;
    match channel.receive()? {
        ReceivedFrame::Close => Ok(()),
        ReceivedFrame::Data(_) => Err(RuntimeError::ChannelFrameRejected),
    }
}
