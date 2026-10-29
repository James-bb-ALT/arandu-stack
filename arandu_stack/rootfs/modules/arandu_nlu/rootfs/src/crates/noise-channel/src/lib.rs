//! Bounded Noise channel shared by the P14 add-on and companion peers.

#![forbid(unsafe_code)]

use std::{fmt, fs::File, io::Read};

use snow::{
    Error as SnowError, HandshakeState, StatelessTransportState,
    error::InitStage,
    params::{CipherChoice, DHChoice, HashChoice, NoiseParams},
    resolvers::{CryptoResolver, DefaultResolver},
    types::{Cipher, Dh, Hash, Random},
};

#[cfg(not(chacha20_force_soft))]
compile_error!("noise-channel requires the selected ChaCha20 software backend");
#[cfg(any(chacha20_force_neon, chacha20_force_avx2, chacha20_force_sse2))]
compile_error!("noise-channel prohibits accelerated ChaCha20 backends");
#[cfg(not(poly1305_force_soft))]
compile_error!("noise-channel requires the selected Poly1305 software backend");
#[cfg(not(curve25519_dalek_backend = "serial"))]
compile_error!("noise-channel requires the Curve25519 serial backend");
#[cfg(any(
    curve25519_dalek_backend = "auto",
    curve25519_dalek_backend = "fiat",
    curve25519_dalek_backend = "simd"
))]
compile_error!("noise-channel prohibits alternate Curve25519 backends");

/// The only Noise profile accepted by this channel.
pub const PROFILE: &str = "Noise_NNpsk0_25519_ChaChaPoly_BLAKE2s";
/// The protocol identifier bound into every Noise prologue.
pub const PROTOCOL_VERSION: &[u8] = b"nlu-ha-companion-v1";
/// Largest accepted Noise handshake message.
pub const MAX_HANDSHAKE_MESSAGE_LEN: usize = 128;
/// Largest accepted application payload.
pub const MAX_PAYLOAD_LEN: usize = 65_431;
/// Largest complete length-prefixed encrypted frame.
pub const MAX_ENCRYPTED_FRAME_LEN: usize = 65_537;
/// Number of accepted frames in one direction between deterministic rekeys.
pub const REKEY_INTERVAL: u64 = 1 << 20;

const ADDON_ROLE: &[u8] = b"addon-adapter";
const COMPANION_ROLE: &[u8] = b"ha-companion-helper";
const PROLOGUE_PREFIX: &[u8] = b"NLU_HA_NOISE_PROLOGUE_V1";
const RANDOM_DEVICE: &str = "/dev/urandom";
const NOISE_TAG_LEN: usize = 16;
const MAX_NOISE_MESSAGE_LEN: usize = 65_535;
const FRAME_PREFIX_LEN: usize = 2;
const FRAME_HEADER_LEN: usize = 88;
const MIN_ENCRYPTED_FRAME_LEN: usize = FRAME_PREFIX_LEN + FRAME_HEADER_LEN + NOISE_TAG_LEN;
const FRAME_MAGIC: &[u8; 8] = b"NLUCHN01";
const FRAME_VERSION: u16 = 1;
const ZERO_PSK: [u8; 32] = [0; 32];

/// Closed error categories which never expose Snow internals or input bytes.
#[derive(Clone, Copy, Eq, PartialEq)]
pub enum ChannelError {
    /// A typed epoch, nonce, or fixed protocol invariant was invalid.
    InvalidBinding,
    /// The required fallible operating-system entropy source was unavailable.
    EntropyUnavailable,
    /// A handshake message or peer proof was rejected.
    HandshakeRejected,
    /// An encrypted frame failed authentication or a bound field check.
    FrameRejected,
    /// A local outbound payload exceeds the fixed channel bound.
    FrameTooLarge,
    /// The next sequence would use Noise's reserved terminal nonce.
    SequenceExhausted,
    /// The requested channel direction or complete channel is closed.
    Closed,
}

impl ChannelError {
    fn label(self) -> &'static str {
        match self {
            Self::InvalidBinding => "invalid channel binding",
            Self::EntropyUnavailable => "channel entropy unavailable",
            Self::HandshakeRejected => "handshake rejected",
            Self::FrameRejected => "encrypted frame rejected",
            Self::FrameTooLarge => "frame exceeds channel limit",
            Self::SequenceExhausted => "channel sequence exhausted",
            Self::Closed => "channel closed",
        }
    }
}

impl fmt::Debug for ChannelError {
    fn fmt(&self, formatter: &mut fmt::Formatter<'_>) -> fmt::Result {
        formatter
            .debug_tuple("ChannelError")
            .field(&self.label())
            .finish()
    }
}

impl fmt::Display for ChannelError {
    fn fmt(&self, formatter: &mut fmt::Formatter<'_>) -> fmt::Result {
        formatter.write_str(self.label())
    }
}

impl std::error::Error for ChannelError {}

/// Nonzero 256-bit pairing epoch bound into the handshake and every encrypted frame.
#[derive(Clone, Copy, Eq, PartialEq)]
pub struct PairingEpoch([u8; 32]);

impl PairingEpoch {
    /// Constructs a pairing epoch, rejecting the all-zero sentinel.
    pub fn new(bytes: [u8; 32]) -> Result<Self, ChannelError> {
        if bytes.iter().all(|byte| *byte == 0) {
            Err(ChannelError::InvalidBinding)
        } else {
            Ok(Self(bytes))
        }
    }

    /// Returns the exact wire bytes.
    pub fn as_bytes(&self) -> &[u8; 32] {
        &self.0
    }
}

impl fmt::Debug for PairingEpoch {
    fn fmt(&self, formatter: &mut fmt::Formatter<'_>) -> fmt::Result {
        formatter.write_str("PairingEpoch([BOUND])")
    }
}

/// Ordered process role represented in a channel binding.
#[derive(Clone, Copy, Debug, Eq, PartialEq)]
pub enum PeerRole {
    /// Home Assistant add-on adapter.
    AddonAdapter,
    /// Rust helper colocated with the Home Assistant companion integration.
    CompanionHelper,
}

impl PeerRole {
    fn as_bytes(self) -> &'static [u8] {
        match self {
            Self::AddonAdapter => ADDON_ROLE,
            Self::CompanionHelper => COMPANION_ROLE,
        }
    }
}

/// Which process role initiates one Noise connection.
#[derive(Clone, Copy, Debug, Eq, PartialEq)]
pub enum ConnectionDirection {
    /// The add-on adapter is the Noise initiator.
    AddonInitiates,
    /// The companion helper is the Noise initiator.
    CompanionInitiates,
}

impl ConnectionDirection {
    fn roles(self) -> (PeerRole, PeerRole) {
        match self {
            Self::AddonInitiates => (PeerRole::AddonAdapter, PeerRole::CompanionHelper),
            Self::CompanionInitiates => (PeerRole::CompanionHelper, PeerRole::AddonAdapter),
        }
    }

    fn as_bytes(self) -> &'static [u8] {
        match self {
            Self::AddonInitiates => b"addon-to-companion",
            Self::CompanionInitiates => b"companion-to-addon",
        }
    }
}

/// Fresh 256-bit identifier for one connection.
#[derive(Clone, Copy, Eq, PartialEq)]
pub struct ConnectionNonce([u8; 32]);

impl ConnectionNonce {
    /// Generates one nonce from the selected fallible `/dev/urandom` source.
    pub fn generate() -> Result<Self, ChannelError> {
        let mut bytes = [0_u8; 32];
        File::open(RANDOM_DEVICE)
            .and_then(|mut file| file.read_exact(&mut bytes))
            .map_err(|_| ChannelError::EntropyUnavailable)?;
        Self::from_bytes(bytes)
    }

    /// Admits externally transported nonce bytes, rejecting the zero sentinel.
    pub fn from_bytes(bytes: [u8; 32]) -> Result<Self, ChannelError> {
        if bytes.iter().all(|byte| *byte == 0) {
            Err(ChannelError::InvalidBinding)
        } else {
            Ok(Self(bytes))
        }
    }

    /// Returns bytes for the bounded pre-handshake connection offer.
    pub fn as_bytes(&self) -> &[u8; 32] {
        &self.0
    }
}

impl fmt::Debug for ConnectionNonce {
    fn fmt(&self, formatter: &mut fmt::Formatter<'_>) -> fmt::Result {
        formatter.write_str("ConnectionNonce([REDACTED])")
    }
}

/// Immutable epoch, roles, direction, and nonce for one connection.
#[derive(Clone, Copy, Eq, PartialEq)]
pub struct ChannelBinding {
    epoch: PairingEpoch,
    direction: ConnectionDirection,
    connection_nonce: ConnectionNonce,
}

impl ChannelBinding {
    /// Constructs a complete typed channel binding.
    pub fn new(
        epoch: PairingEpoch,
        direction: ConnectionDirection,
        connection_nonce: ConnectionNonce,
    ) -> Self {
        Self {
            epoch,
            direction,
            connection_nonce,
        }
    }

    /// Returns the pairing epoch.
    pub fn epoch(self) -> PairingEpoch {
        self.epoch
    }

    /// Returns the physical connection direction.
    pub fn direction(self) -> ConnectionDirection {
        self.direction
    }

    /// Returns the connection nonce.
    pub fn connection_nonce(self) -> ConnectionNonce {
        self.connection_nonce
    }

    /// Returns the ordered initiator role.
    pub fn initiator_role(self) -> PeerRole {
        self.direction.roles().0
    }

    /// Returns the ordered responder role.
    pub fn responder_role(self) -> PeerRole {
        self.direction.roles().1
    }

    fn encode_prologue(self) -> Result<Vec<u8>, ChannelError> {
        let mut encoded = Vec::with_capacity(192);
        encoded.extend_from_slice(PROLOGUE_PREFIX);
        append_prologue_field(&mut encoded, b"protocol", PROTOCOL_VERSION)?;
        append_prologue_field(&mut encoded, b"epoch", self.epoch.as_bytes())?;
        append_prologue_field(&mut encoded, b"initiator", self.initiator_role().as_bytes())?;
        append_prologue_field(&mut encoded, b"responder", self.responder_role().as_bytes())?;
        append_prologue_field(&mut encoded, b"direction", self.direction.as_bytes())?;
        append_prologue_field(
            &mut encoded,
            b"connection_nonce",
            self.connection_nonce.as_bytes(),
        )?;
        Ok(encoded)
    }
}

impl fmt::Debug for ChannelBinding {
    fn fmt(&self, formatter: &mut fmt::Formatter<'_>) -> fmt::Result {
        formatter.write_str("ChannelBinding([BOUND])")
    }
}

fn append_prologue_field(
    encoded: &mut Vec<u8>,
    name: &[u8],
    value: &[u8],
) -> Result<(), ChannelError> {
    let name_length = u16::try_from(name.len()).map_err(|_| ChannelError::InvalidBinding)?;
    let value_length = u16::try_from(value.len()).map_err(|_| ChannelError::InvalidBinding)?;
    encoded.extend_from_slice(&name_length.to_be_bytes());
    encoded.extend_from_slice(name);
    encoded.extend_from_slice(&value_length.to_be_bytes());
    encoded.extend_from_slice(value);
    Ok(())
}

struct FileRng(File);

impl Random for FileRng {
    fn try_fill_bytes(&mut self, destination: &mut [u8]) -> Result<(), SnowError> {
        self.0.read_exact(destination).map_err(|_| SnowError::Rng)
    }
}

struct P13Resolver;

impl CryptoResolver for P13Resolver {
    fn resolve_rng(&self) -> Option<Box<dyn Random>> {
        File::open(RANDOM_DEVICE)
            .ok()
            .map(|file| Box::new(FileRng(file)) as Box<dyn Random>)
    }

    fn resolve_dh(&self, choice: &DHChoice) -> Option<Box<dyn Dh>> {
        DefaultResolver.resolve_dh(choice)
    }

    fn resolve_hash(&self, choice: &HashChoice) -> Option<Box<dyn Hash>> {
        DefaultResolver.resolve_hash(choice)
    }

    fn resolve_cipher(&self, choice: &CipherChoice) -> Option<Box<dyn Cipher>> {
        DefaultResolver.resolve_cipher(choice)
    }
}

/// One bounded Noise handshake message.
pub struct HandshakeMessage {
    bytes: Vec<u8>,
}

impl HandshakeMessage {
    /// Copies a received bounded handshake message.
    pub fn from_bytes(bytes: &[u8]) -> Result<Self, ChannelError> {
        if bytes.is_empty() || bytes.len() > MAX_HANDSHAKE_MESSAGE_LEN {
            return Err(ChannelError::HandshakeRejected);
        }
        Ok(Self {
            bytes: bytes.to_vec(),
        })
    }

    /// Returns the exact bytes to place on the peer transport.
    pub fn as_bytes(&self) -> &[u8] {
        &self.bytes
    }
}

impl fmt::Debug for HandshakeMessage {
    fn fmt(&self, formatter: &mut fmt::Formatter<'_>) -> fmt::Result {
        formatter.write_str("HandshakeMessage([REDACTED])")
    }
}

impl Drop for HandshakeMessage {
    fn drop(&mut self) {
        self.bytes.fill(0);
    }
}

/// Initiator state after emitting the first NNpsk0 handshake message.
pub struct InitiatorHandshake {
    state: Option<HandshakeState>,
    binding: ChannelBinding,
}

impl InitiatorHandshake {
    /// Creates initiator state and the first bounded handshake message.
    pub fn start(
        binding: ChannelBinding,
        psk: &[u8; 32],
    ) -> Result<(Self, HandshakeMessage), ChannelError> {
        let mut state = build_handshake(binding, psk, Side::Initiator)?;
        let first_message = match write_handshake_message(&mut state) {
            Ok(message) => message,
            Err(error) => {
                discard_handshake(state);
                return Err(error);
            }
        };
        Ok((
            Self {
                state: Some(state),
                binding,
            },
            first_message,
        ))
    }

    /// Authenticates the responder's message and enters transport mode.
    pub fn finish(mut self, response: &HandshakeMessage) -> Result<NoiseChannel, ChannelError> {
        let mut state = self.state.take().ok_or(ChannelError::Closed)?;
        if let Err(error) = read_handshake_message(&mut state, response) {
            discard_handshake(state);
            return Err(error);
        }
        finish_handshake(state, self.binding, Side::Initiator)
    }

    /// Aborts the pending handshake and clears its stored PSK slot.
    pub fn close(mut self) {
        if let Some(state) = self.state.take() {
            discard_handshake(state);
        }
    }
}

impl fmt::Debug for InitiatorHandshake {
    fn fmt(&self, formatter: &mut fmt::Formatter<'_>) -> fmt::Result {
        formatter.write_str("InitiatorHandshake([REDACTED])")
    }
}

impl Drop for InitiatorHandshake {
    fn drop(&mut self) {
        if let Some(state) = self.state.take() {
            discard_handshake(state);
        }
    }
}

/// Responder state waiting for the first NNpsk0 handshake message.
pub struct ResponderHandshake {
    state: Option<HandshakeState>,
    binding: ChannelBinding,
}

impl ResponderHandshake {
    /// Creates responder state for one exact binding and PSK.
    pub fn new(binding: ChannelBinding, psk: &[u8; 32]) -> Result<Self, ChannelError> {
        Ok(Self {
            state: Some(build_handshake(binding, psk, Side::Responder)?),
            binding,
        })
    }

    /// Authenticates the initiator and returns transport state plus the response.
    pub fn accept(
        mut self,
        first_message: &HandshakeMessage,
    ) -> Result<(NoiseChannel, HandshakeMessage), ChannelError> {
        let mut state = self.state.take().ok_or(ChannelError::Closed)?;
        if let Err(error) = read_handshake_message(&mut state, first_message) {
            discard_handshake(state);
            return Err(error);
        }
        let response = match write_handshake_message(&mut state) {
            Ok(message) => message,
            Err(error) => {
                discard_handshake(state);
                return Err(error);
            }
        };
        let channel = finish_handshake(state, self.binding, Side::Responder)?;
        Ok((channel, response))
    }

    /// Aborts the pending handshake and clears its stored PSK slot.
    pub fn close(mut self) {
        if let Some(state) = self.state.take() {
            discard_handshake(state);
        }
    }
}

impl fmt::Debug for ResponderHandshake {
    fn fmt(&self, formatter: &mut fmt::Formatter<'_>) -> fmt::Result {
        formatter.write_str("ResponderHandshake([REDACTED])")
    }
}

impl Drop for ResponderHandshake {
    fn drop(&mut self) {
        if let Some(state) = self.state.take() {
            discard_handshake(state);
        }
    }
}

fn build_handshake(
    binding: ChannelBinding,
    psk: &[u8; 32],
    side: Side,
) -> Result<HandshakeState, ChannelError> {
    let prologue = binding.encode_prologue()?;
    build_handshake_with_prologue(psk, &prologue, side)
}

fn build_handshake_with_prologue(
    psk: &[u8; 32],
    prologue: &[u8],
    side: Side,
) -> Result<HandshakeState, ChannelError> {
    let parameters: NoiseParams = PROFILE.parse().map_err(|_| ChannelError::InvalidBinding)?;
    let builder = snow::Builder::with_resolver(parameters, Box::new(P13Resolver))
        .psk(0, psk)
        .map_err(map_handshake_error)?
        .prologue(prologue)
        .map_err(map_handshake_error)?;
    match side {
        Side::Initiator => builder.build_initiator(),
        Side::Responder => builder.build_responder(),
    }
    .map_err(map_handshake_error)
}

fn write_handshake_message(state: &mut HandshakeState) -> Result<HandshakeMessage, ChannelError> {
    let mut bytes = vec![0_u8; MAX_HANDSHAKE_MESSAGE_LEN];
    let written = state
        .write_message(&[], &mut bytes)
        .map_err(map_handshake_error)?;
    if written == 0 || written > MAX_HANDSHAKE_MESSAGE_LEN {
        bytes.fill(0);
        return Err(ChannelError::HandshakeRejected);
    }
    bytes.truncate(written);
    Ok(HandshakeMessage { bytes })
}

fn read_handshake_message(
    state: &mut HandshakeState,
    message: &HandshakeMessage,
) -> Result<(), ChannelError> {
    let mut payload = [];
    let read = state
        .read_message(message.as_bytes(), &mut payload)
        .map_err(map_handshake_error)?;
    if read == 0 {
        Ok(())
    } else {
        Err(ChannelError::HandshakeRejected)
    }
}

fn finish_handshake(
    mut state: HandshakeState,
    binding: ChannelBinding,
    side: Side,
) -> Result<NoiseChannel, ChannelError> {
    if !state.is_handshake_finished() {
        discard_handshake(state);
        return Err(ChannelError::HandshakeRejected);
    }
    if state.set_psk(0, &ZERO_PSK).is_err() {
        discard_handshake(state);
        return Err(ChannelError::HandshakeRejected);
    }
    let transport = state
        .into_stateless_transport_mode()
        .map_err(map_handshake_error)?;
    Ok(NoiseChannel::new(transport, binding, side))
}

fn discard_handshake(mut state: HandshakeState) {
    let _ = state.set_psk(0, &ZERO_PSK);
    drop(state);
}

fn map_handshake_error(error: SnowError) -> ChannelError {
    match error {
        SnowError::Init(InitStage::GetRngImpl) | SnowError::Rng => ChannelError::EntropyUnavailable,
        _ => ChannelError::HandshakeRejected,
    }
}

#[derive(Clone, Copy, Debug, Eq, PartialEq)]
enum Side {
    Initiator,
    Responder,
}

#[derive(Clone, Copy, Debug, Eq, PartialEq)]
#[repr(u8)]
enum MessageDirection {
    InitiatorToResponder = 1,
    ResponderToInitiator = 2,
}

impl MessageDirection {
    fn outgoing(side: Side) -> Self {
        match side {
            Side::Initiator => Self::InitiatorToResponder,
            Side::Responder => Self::ResponderToInitiator,
        }
    }

    fn incoming(side: Side) -> Self {
        match side {
            Side::Initiator => Self::ResponderToInitiator,
            Side::Responder => Self::InitiatorToResponder,
        }
    }

    fn from_byte(byte: u8) -> Result<Self, ChannelError> {
        match byte {
            1 => Ok(Self::InitiatorToResponder),
            2 => Ok(Self::ResponderToInitiator),
            _ => Err(ChannelError::FrameRejected),
        }
    }
}

#[derive(Clone, Copy, Debug, Eq, PartialEq)]
#[repr(u8)]
enum FrameKind {
    Data = 1,
    Close = 2,
}

impl FrameKind {
    fn from_byte(byte: u8) -> Result<Self, ChannelError> {
        match byte {
            1 => Ok(Self::Data),
            2 => Ok(Self::Close),
            _ => Err(ChannelError::FrameRejected),
        }
    }
}

/// One bounded, length-prefixed encrypted transport frame.
pub struct EncryptedFrame {
    bytes: Vec<u8>,
}

impl EncryptedFrame {
    /// Returns exact bytes to write to the reliable peer transport.
    pub fn as_bytes(&self) -> &[u8] {
        &self.bytes
    }
}

impl fmt::Debug for EncryptedFrame {
    fn fmt(&self, formatter: &mut fmt::Formatter<'_>) -> fmt::Result {
        formatter.write_str("EncryptedFrame([REDACTED])")
    }
}

impl Drop for EncryptedFrame {
    fn drop(&mut self) {
        self.bytes.fill(0);
    }
}

/// Received application plaintext that clears its owned buffer on drop.
pub struct Plaintext {
    bytes: Vec<u8>,
}

impl Plaintext {
    /// Borrows the exact application payload.
    pub fn as_bytes(&self) -> &[u8] {
        &self.bytes[FRAME_HEADER_LEN..]
    }
}

impl fmt::Debug for Plaintext {
    fn fmt(&self, formatter: &mut fmt::Formatter<'_>) -> fmt::Result {
        formatter.write_str("Plaintext([REDACTED])")
    }
}

impl Drop for Plaintext {
    fn drop(&mut self) {
        self.bytes.fill(0);
    }
}

/// Result of accepting one authenticated transport frame.
pub enum ReceivedFrame {
    /// Authenticated application data.
    Data(Plaintext),
    /// Authenticated orderly close for the peer's sending direction.
    Close,
}

impl fmt::Debug for ReceivedFrame {
    fn fmt(&self, formatter: &mut fmt::Formatter<'_>) -> fmt::Result {
        match self {
            Self::Data(_) => formatter.write_str("ReceivedFrame::Data([REDACTED])"),
            Self::Close => formatter.write_str("ReceivedFrame::Close"),
        }
    }
}

/// Established, strictly sequenced Noise transport state.
pub struct NoiseChannel {
    transport: Option<StatelessTransportState>,
    binding: ChannelBinding,
    side: Side,
    send_sequence: u64,
    receive_sequence: u64,
    send_open: bool,
    receive_open: bool,
}

impl NoiseChannel {
    fn new(transport: StatelessTransportState, binding: ChannelBinding, side: Side) -> Self {
        Self {
            transport: Some(transport),
            binding,
            side,
            send_sequence: 0,
            receive_sequence: 0,
            send_open: true,
            receive_open: true,
        }
    }

    /// Encrypts one application payload under the exact next sequence.
    pub fn send(&mut self, payload: &[u8]) -> Result<EncryptedFrame, ChannelError> {
        if !self.receive_open {
            return Err(ChannelError::Closed);
        }
        self.encrypt_frame(FrameKind::Data, payload)
    }

    /// Emits one authenticated close frame for this peer's sending direction.
    pub fn send_close(&mut self) -> Result<EncryptedFrame, ChannelError> {
        self.encrypt_frame(FrameKind::Close, &[])
    }

    /// Authenticates, binds, and accepts exactly the next peer frame.
    pub fn receive(&mut self, wire: &[u8]) -> Result<ReceivedFrame, ChannelError> {
        if self.transport.is_none() || !self.receive_open {
            return Err(ChannelError::Closed);
        }
        if self.receive_sequence == u64::MAX {
            self.close_transport();
            return Err(ChannelError::SequenceExhausted);
        }

        let ciphertext = match bounded_ciphertext(wire) {
            Ok(ciphertext) => ciphertext,
            Err(error) => {
                self.close_transport();
                return Err(error);
            }
        };
        let mut plaintext = vec![0_u8; ciphertext.len() - NOISE_TAG_LEN];
        let Some(transport) = self.transport.as_ref() else {
            plaintext.fill(0);
            return Err(ChannelError::Closed);
        };
        let read_result = transport.read_message(self.receive_sequence, ciphertext, &mut plaintext);
        let read = match read_result {
            Ok(read) if read == plaintext.len() => read,
            Ok(_) | Err(_) => {
                plaintext.fill(0);
                self.close_transport();
                return Err(ChannelError::FrameRejected);
            }
        };
        plaintext.truncate(read);

        let kind = match validate_plaintext_frame(
            &plaintext,
            self.binding,
            MessageDirection::incoming(self.side),
            self.receive_sequence,
        ) {
            Ok(kind) => kind,
            Err(error) => {
                plaintext.fill(0);
                self.close_transport();
                return Err(error);
            }
        };

        self.receive_sequence += 1;
        if should_rekey(self.receive_sequence) {
            let Some(transport) = self.transport.as_mut() else {
                plaintext.fill(0);
                return Err(ChannelError::Closed);
            };
            transport.rekey_incoming();
        }

        match kind {
            FrameKind::Data => Ok(ReceivedFrame::Data(Plaintext { bytes: plaintext })),
            FrameKind::Close => {
                plaintext.fill(0);
                self.receive_open = false;
                if !self.send_open {
                    self.close_transport();
                }
                Ok(ReceivedFrame::Close)
            }
        }
    }

    /// Immediately drops Snow state and closes both directions.
    pub fn close(&mut self) {
        self.close_transport();
    }

    /// Reports whether Snow transport state has been dropped.
    pub fn is_closed(&self) -> bool {
        self.transport.is_none()
    }

    fn encrypt_frame(
        &mut self,
        kind: FrameKind,
        payload: &[u8],
    ) -> Result<EncryptedFrame, ChannelError> {
        if self.transport.is_none() || !self.send_open {
            return Err(ChannelError::Closed);
        }
        if payload.len() > MAX_PAYLOAD_LEN {
            return Err(ChannelError::FrameTooLarge);
        }
        if self.send_sequence == u64::MAX {
            self.close_transport();
            return Err(ChannelError::SequenceExhausted);
        }

        let mut plaintext = encode_plaintext_frame(
            self.binding,
            MessageDirection::outgoing(self.side),
            kind,
            self.send_sequence,
            payload,
        )?;
        let ciphertext_length = plaintext.len() + NOISE_TAG_LEN;
        let mut wire = vec![0_u8; FRAME_PREFIX_LEN + ciphertext_length];
        let ciphertext_length_u16 = match u16::try_from(ciphertext_length) {
            Ok(length) => length,
            Err(_) => {
                plaintext.fill(0);
                wire.fill(0);
                self.close_transport();
                return Err(ChannelError::FrameTooLarge);
            }
        };
        wire[..FRAME_PREFIX_LEN].copy_from_slice(&ciphertext_length_u16.to_be_bytes());
        let Some(transport) = self.transport.as_ref() else {
            plaintext.fill(0);
            wire.fill(0);
            return Err(ChannelError::Closed);
        };
        let write_result = transport.write_message(
            self.send_sequence,
            &plaintext,
            &mut wire[FRAME_PREFIX_LEN..],
        );
        plaintext.fill(0);
        match write_result {
            Ok(written) if written == ciphertext_length => {}
            Ok(_) | Err(_) => {
                wire.fill(0);
                self.close_transport();
                return Err(ChannelError::FrameRejected);
            }
        }

        self.send_sequence += 1;
        if should_rekey(self.send_sequence) {
            let Some(transport) = self.transport.as_mut() else {
                wire.fill(0);
                return Err(ChannelError::Closed);
            };
            transport.rekey_outgoing();
        }
        if kind == FrameKind::Close {
            self.send_open = false;
            if !self.receive_open {
                self.close_transport();
            }
        }
        Ok(EncryptedFrame { bytes: wire })
    }

    fn close_transport(&mut self) {
        drop(self.transport.take());
        self.send_open = false;
        self.receive_open = false;
    }

    #[cfg(test)]
    fn fixture_frame(
        &self,
        noise_sequence: u64,
        frame_binding: ChannelBinding,
        frame_direction: MessageDirection,
        frame_sequence: u64,
        payload: &[u8],
    ) -> EncryptedFrame {
        let mut plaintext = encode_plaintext_frame(
            frame_binding,
            frame_direction,
            FrameKind::Data,
            frame_sequence,
            payload,
        )
        .expect("FIXTURE_TECNICA frame must encode");
        let ciphertext_length = plaintext.len() + NOISE_TAG_LEN;
        let mut wire = vec![0_u8; FRAME_PREFIX_LEN + ciphertext_length];
        wire[..FRAME_PREFIX_LEN].copy_from_slice(
            &u16::try_from(ciphertext_length)
                .expect("fixture ciphertext length must fit")
                .to_be_bytes(),
        );
        let written = self
            .transport
            .as_ref()
            .expect("fixture channel must be open")
            .write_message(noise_sequence, &plaintext, &mut wire[FRAME_PREFIX_LEN..])
            .expect("FIXTURE_TECNICA frame encryption must succeed");
        assert_eq!(written, ciphertext_length);
        plaintext.fill(0);
        EncryptedFrame { bytes: wire }
    }

    #[cfg(test)]
    fn set_sequences_for_test(&mut self, send: u64, receive: u64) {
        self.send_sequence = send;
        self.receive_sequence = receive;
    }
}

impl fmt::Debug for NoiseChannel {
    fn fmt(&self, formatter: &mut fmt::Formatter<'_>) -> fmt::Result {
        let state = if self.transport.is_some() {
            "active"
        } else {
            "closed"
        };
        formatter
            .debug_struct("NoiseChannel")
            .field("state", &state)
            .finish()
    }
}

impl Drop for NoiseChannel {
    fn drop(&mut self) {
        self.close_transport();
    }
}

fn encode_plaintext_frame(
    binding: ChannelBinding,
    direction: MessageDirection,
    kind: FrameKind,
    sequence: u64,
    payload: &[u8],
) -> Result<Vec<u8>, ChannelError> {
    let payload_length = u32::try_from(payload.len()).map_err(|_| ChannelError::FrameTooLarge)?;
    let mut plaintext = Vec::with_capacity(FRAME_HEADER_LEN + payload.len());
    plaintext.extend_from_slice(FRAME_MAGIC);
    plaintext.extend_from_slice(&FRAME_VERSION.to_be_bytes());
    plaintext.extend_from_slice(binding.epoch.as_bytes());
    plaintext.extend_from_slice(binding.connection_nonce.as_bytes());
    plaintext.push(direction as u8);
    plaintext.push(kind as u8);
    plaintext.extend_from_slice(&sequence.to_be_bytes());
    plaintext.extend_from_slice(&payload_length.to_be_bytes());
    if plaintext.len() != FRAME_HEADER_LEN {
        plaintext.fill(0);
        return Err(ChannelError::FrameRejected);
    }
    plaintext.extend_from_slice(payload);
    Ok(plaintext)
}

fn bounded_ciphertext(wire: &[u8]) -> Result<&[u8], ChannelError> {
    if wire.len() < MIN_ENCRYPTED_FRAME_LEN || wire.len() > MAX_ENCRYPTED_FRAME_LEN {
        return Err(ChannelError::FrameRejected);
    }
    let declared = usize::from(u16::from_be_bytes([wire[0], wire[1]]));
    if !(FRAME_HEADER_LEN + NOISE_TAG_LEN..=MAX_NOISE_MESSAGE_LEN).contains(&declared)
        || declared != wire.len() - FRAME_PREFIX_LEN
    {
        return Err(ChannelError::FrameRejected);
    }
    Ok(&wire[FRAME_PREFIX_LEN..])
}

fn validate_plaintext_frame(
    plaintext: &[u8],
    binding: ChannelBinding,
    expected_direction: MessageDirection,
    expected_sequence: u64,
) -> Result<FrameKind, ChannelError> {
    if plaintext.len() < FRAME_HEADER_LEN
        || &plaintext[0..8] != FRAME_MAGIC
        || u16::from_be_bytes([plaintext[8], plaintext[9]]) != FRAME_VERSION
    {
        return Err(ChannelError::FrameRejected);
    }

    if plaintext[10..42] != binding.epoch.as_bytes()[..]
        || plaintext[42..74] != binding.connection_nonce.as_bytes()[..]
        || MessageDirection::from_byte(plaintext[74])? != expected_direction
    {
        return Err(ChannelError::FrameRejected);
    }

    let kind = FrameKind::from_byte(plaintext[75])?;
    let sequence = u64::from_be_bytes(
        plaintext[76..84]
            .try_into()
            .map_err(|_| ChannelError::FrameRejected)?,
    );
    let payload_length = usize::try_from(u32::from_be_bytes(
        plaintext[84..88]
            .try_into()
            .map_err(|_| ChannelError::FrameRejected)?,
    ))
    .map_err(|_| ChannelError::FrameRejected)?;
    if sequence != expected_sequence
        || payload_length > MAX_PAYLOAD_LEN
        || payload_length != plaintext.len() - FRAME_HEADER_LEN
        || (kind == FrameKind::Close && payload_length != 0)
    {
        return Err(ChannelError::FrameRejected);
    }
    Ok(kind)
}

fn should_rekey(next_sequence: u64) -> bool {
    next_sequence != 0 && next_sequence.is_multiple_of(REKEY_INTERVAL)
}

#[cfg(test)]
mod tests;
