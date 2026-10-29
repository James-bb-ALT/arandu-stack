use std::io::{Read, Write};

use noise_channel::{
    ChannelBinding, ChannelError, ConnectionDirection, ConnectionNonce, HandshakeMessage,
    InitiatorHandshake, MAX_ENCRYPTED_FRAME_LEN, MAX_HANDSHAKE_MESSAGE_LEN, NoiseChannel,
    PairingEpoch, ReceivedFrame, ResponderHandshake,
};

use crate::{Result, RuntimeError};

const OFFER_MAGIC: &[u8; 8] = b"NLUOFR01";
const OFFER_VERSION: u16 = 1;
const OFFER_BYTES: usize = 8 + 2 + 1 + 32 + 32;
const FRAME_PREFIX_BYTES: usize = 2;

pub struct NoiseStream<T> {
    transport: T,
    channel: NoiseChannel,
    binding: ChannelBinding,
}

impl<T> NoiseStream<T>
where
    T: Read + Write,
{
    pub fn send(&mut self, payload: &[u8]) -> Result<()> {
        let frame = self.channel.send(payload).map_err(map_frame_error)?;
        self.transport
            .write_all(frame.as_bytes())
            .and_then(|()| self.transport.flush())
            .map_err(|_| RuntimeError::ChannelIo)
    }

    pub fn receive(&mut self) -> Result<ReceivedFrame> {
        let mut prefix = [0_u8; FRAME_PREFIX_BYTES];
        self.transport
            .read_exact(&mut prefix)
            .map_err(|_| RuntimeError::ChannelIo)?;
        let ciphertext_len = usize::from(u16::from_be_bytes(prefix));
        let wire_len = FRAME_PREFIX_BYTES
            .checked_add(ciphertext_len)
            .ok_or(RuntimeError::ChannelFrameRejected)?;
        if ciphertext_len == 0 || wire_len > MAX_ENCRYPTED_FRAME_LEN {
            self.channel.close();
            return Err(RuntimeError::ChannelFrameRejected);
        }
        let mut wire = vec![0_u8; wire_len];
        wire[..FRAME_PREFIX_BYTES].copy_from_slice(&prefix);
        if self
            .transport
            .read_exact(&mut wire[FRAME_PREFIX_BYTES..])
            .is_err()
        {
            wire.fill(0);
            self.channel.close();
            return Err(RuntimeError::ChannelIo);
        }
        let result = self.channel.receive(&wire).map_err(map_frame_error);
        wire.fill(0);
        result
    }

    pub fn send_close(&mut self) -> Result<()> {
        let frame = self.channel.send_close().map_err(map_frame_error)?;
        self.transport
            .write_all(frame.as_bytes())
            .and_then(|()| self.transport.flush())
            .map_err(|_| RuntimeError::ChannelIo)
    }

    pub fn close(&mut self) {
        self.channel.close();
    }

    #[must_use]
    pub const fn binding(&self) -> ChannelBinding {
        self.binding
    }
}

impl<T> core::fmt::Debug for NoiseStream<T> {
    fn fmt(&self, formatter: &mut core::fmt::Formatter<'_>) -> core::fmt::Result {
        formatter.write_str("NoiseStream(state=redacted)")
    }
}

impl<T> Drop for NoiseStream<T> {
    fn drop(&mut self) {
        self.channel.close();
    }
}

pub fn initiate_noise_stream<T>(
    mut transport: T,
    epoch: PairingEpoch,
    direction: ConnectionDirection,
    pairing_credential: &[u8; 32],
) -> Result<NoiseStream<T>>
where
    T: Read + Write,
{
    let nonce = ConnectionNonce::generate().map_err(map_handshake_error)?;
    let binding = ChannelBinding::new(epoch, direction, nonce);
    write_offer(&mut transport, binding)?;
    let (handshake, first) =
        InitiatorHandshake::start(binding, pairing_credential).map_err(map_handshake_error)?;
    write_handshake(&mut transport, &first)?;
    let response = read_handshake(&mut transport)?;
    let channel = handshake.finish(&response).map_err(map_handshake_error)?;
    Ok(NoiseStream {
        transport,
        channel,
        binding,
    })
}

pub fn accept_noise_stream<T>(
    mut transport: T,
    expected_epoch: PairingEpoch,
    expected_direction: ConnectionDirection,
    pairing_credential: &[u8; 32],
) -> Result<NoiseStream<T>>
where
    T: Read + Write,
{
    let binding = read_offer(&mut transport)?;
    if binding.epoch() != expected_epoch || binding.direction() != expected_direction {
        return Err(RuntimeError::ChannelOfferRejected);
    }
    let first = read_handshake(&mut transport)?;
    let responder =
        ResponderHandshake::new(binding, pairing_credential).map_err(map_handshake_error)?;
    let (channel, response) = responder.accept(&first).map_err(map_handshake_error)?;
    write_handshake(&mut transport, &response)?;
    Ok(NoiseStream {
        transport,
        channel,
        binding,
    })
}

fn write_offer(writer: &mut impl Write, binding: ChannelBinding) -> Result<()> {
    let mut offer = [0_u8; OFFER_BYTES];
    offer[..8].copy_from_slice(OFFER_MAGIC);
    offer[8..10].copy_from_slice(&OFFER_VERSION.to_be_bytes());
    offer[10] = direction_tag(binding.direction());
    offer[11..43].copy_from_slice(binding.epoch().as_bytes());
    offer[43..75].copy_from_slice(binding.connection_nonce().as_bytes());
    writer
        .write_all(&offer)
        .map_err(|_| RuntimeError::ChannelIo)
}

fn read_offer(reader: &mut impl Read) -> Result<ChannelBinding> {
    let mut offer = [0_u8; OFFER_BYTES];
    reader
        .read_exact(&mut offer)
        .map_err(|_| RuntimeError::ChannelIo)?;
    if &offer[..8] != OFFER_MAGIC || u16::from_be_bytes([offer[8], offer[9]]) != OFFER_VERSION {
        return Err(RuntimeError::ChannelOfferRejected);
    }
    let direction = direction_from_tag(offer[10])?;
    let epoch = PairingEpoch::new(
        offer[11..43]
            .try_into()
            .map_err(|_| RuntimeError::ChannelOfferRejected)?,
    )
    .map_err(|_| RuntimeError::ChannelOfferRejected)?;
    let nonce = ConnectionNonce::from_bytes(
        offer[43..75]
            .try_into()
            .map_err(|_| RuntimeError::ChannelOfferRejected)?,
    )
    .map_err(|_| RuntimeError::ChannelOfferRejected)?;
    Ok(ChannelBinding::new(epoch, direction, nonce))
}

fn write_handshake(writer: &mut impl Write, message: &HandshakeMessage) -> Result<()> {
    let length = u16::try_from(message.as_bytes().len())
        .map_err(|_| RuntimeError::ChannelHandshakeRejected)?;
    writer
        .write_all(&length.to_be_bytes())
        .and_then(|()| writer.write_all(message.as_bytes()))
        .and_then(|()| writer.flush())
        .map_err(|_| RuntimeError::ChannelIo)
}

fn read_handshake(reader: &mut impl Read) -> Result<HandshakeMessage> {
    let mut prefix = [0_u8; 2];
    reader
        .read_exact(&mut prefix)
        .map_err(|_| RuntimeError::ChannelIo)?;
    let length = usize::from(u16::from_be_bytes(prefix));
    if length == 0 || length > MAX_HANDSHAKE_MESSAGE_LEN {
        return Err(RuntimeError::ChannelHandshakeRejected);
    }
    let mut bytes = vec![0_u8; length];
    if reader.read_exact(&mut bytes).is_err() {
        bytes.fill(0);
        return Err(RuntimeError::ChannelIo);
    }
    let message =
        HandshakeMessage::from_bytes(&bytes).map_err(|_| RuntimeError::ChannelHandshakeRejected);
    bytes.fill(0);
    message
}

const fn direction_tag(direction: ConnectionDirection) -> u8 {
    match direction {
        ConnectionDirection::AddonInitiates => 1,
        ConnectionDirection::CompanionInitiates => 2,
    }
}

fn direction_from_tag(tag: u8) -> Result<ConnectionDirection> {
    match tag {
        1 => Ok(ConnectionDirection::AddonInitiates),
        2 => Ok(ConnectionDirection::CompanionInitiates),
        _ => Err(RuntimeError::ChannelOfferRejected),
    }
}

const fn map_handshake_error(_: ChannelError) -> RuntimeError {
    RuntimeError::ChannelHandshakeRejected
}

const fn map_frame_error(_: ChannelError) -> RuntimeError {
    RuntimeError::ChannelFrameRejected
}
