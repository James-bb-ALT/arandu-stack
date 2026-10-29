use std::io::{Read, Write};

use noise_channel::{ConnectionDirection, ReceivedFrame};
use serde_json::{Map, Value, json};

use crate::{
    NoiseStream, PairingBindings, Result, RuntimeError, accept_noise_stream, initiate_noise_stream,
};

pub const DEFAULT_HELPER_PAIRING_PORT: u16 = 10_701;
pub const DEFAULT_ADAPTER_RELAY_PORT: u16 = 10_702;
pub const MAX_PAIRING_WIRE_BYTES: usize = 2_048;

const PAIRING_WIRE_VERSION: u16 = 1;
const PAIRING_OFFER_KIND: &str = "pairing_offer";
const PAIRING_ACCEPTED_KIND: &str = "pairing_accepted";

#[derive(Clone, Eq, PartialEq)]
pub struct PairingOffer {
    pairing_id: Box<str>,
    peer_id: Box<str>,
    relay_port: u16,
}

impl PairingOffer {
    pub fn new(pairing_id: &str, peer_id: &str, relay_port: u16) -> Result<Self> {
        if !valid_pairing_id(pairing_id) || !valid_peer_id(peer_id) || relay_port == 0 {
            return Err(RuntimeError::PairingProtocol);
        }
        Ok(Self {
            pairing_id: pairing_id.into(),
            peer_id: peer_id.into(),
            relay_port,
        })
    }

    #[must_use]
    pub fn pairing_id(&self) -> &str {
        &self.pairing_id
    }

    #[must_use]
    pub fn peer_id(&self) -> &str {
        &self.peer_id
    }

    #[must_use]
    pub const fn relay_port(&self) -> u16 {
        self.relay_port
    }

    fn encode(&self) -> Result<Vec<u8>> {
        bounded_canonical(&json!({
            "kind": PAIRING_OFFER_KIND,
            "pairing_id": self.pairing_id,
            "peer_id": self.peer_id,
            "relay_port": self.relay_port,
            "version": PAIRING_WIRE_VERSION,
        }))
    }

    fn decode(bytes: &[u8]) -> Result<Self> {
        let value = parse_document(bytes)?;
        let object = exact_object(
            &value,
            &["kind", "pairing_id", "peer_id", "relay_port", "version"],
        )?;
        if object.get("version").and_then(Value::as_u64) != Some(u64::from(PAIRING_WIRE_VERSION))
            || object.get("kind").and_then(Value::as_str) != Some(PAIRING_OFFER_KIND)
        {
            return Err(RuntimeError::PairingProtocol);
        }
        let pairing_id = required_string(object, "pairing_id")?;
        let peer_id = required_string(object, "peer_id")?;
        let relay_port = object
            .get("relay_port")
            .and_then(Value::as_u64)
            .and_then(|value| u16::try_from(value).ok())
            .ok_or(RuntimeError::PairingProtocol)?;
        Self::new(pairing_id, peer_id, relay_port)
    }
}

impl core::fmt::Debug for PairingOffer {
    fn fmt(&self, formatter: &mut core::fmt::Formatter<'_>) -> core::fmt::Result {
        formatter.write_str("PairingOffer(bindings=redacted)")
    }
}

pub fn initiate_pairing<T>(
    transport: T,
    bindings: PairingBindings,
    credential: &[u8; 32],
    peer_id: &str,
    relay_port: u16,
) -> Result<()>
where
    T: Read + Write,
{
    let pairing_id = bindings.pairing_id_hex();
    let offer = PairingOffer::new(&pairing_id, peer_id, relay_port)?;
    let mut channel = initiate_noise_stream(
        transport,
        bindings.epoch(),
        ConnectionDirection::AddonInitiates,
        credential,
    )?;
    channel.send(&offer.encode()?)?;
    let ReceivedFrame::Data(acknowledgement) = channel.receive()? else {
        channel.close();
        return Err(RuntimeError::PairingRejected);
    };
    validate_acknowledgement(acknowledgement.as_bytes(), &pairing_id)?;
    close_initiator(&mut channel)
}

pub fn accept_pairing<T>(
    transport: T,
    bindings: PairingBindings,
    credential: &[u8; 32],
) -> Result<PairingOffer>
where
    T: Read + Write,
{
    let mut channel = accept_noise_stream(
        transport,
        bindings.epoch(),
        ConnectionDirection::AddonInitiates,
        credential,
    )?;
    let ReceivedFrame::Data(payload) = channel.receive()? else {
        channel.close();
        return Err(RuntimeError::PairingRejected);
    };
    let offer = PairingOffer::decode(payload.as_bytes())?;
    let expected_pairing_id = bindings.pairing_id_hex();
    if offer.pairing_id() != expected_pairing_id {
        channel.close();
        return Err(RuntimeError::PairingRejected);
    }
    channel.send(&encode_acknowledgement(&expected_pairing_id)?)?;
    close_responder(&mut channel)?;
    Ok(offer)
}

fn encode_acknowledgement(pairing_id: &str) -> Result<Vec<u8>> {
    if !valid_pairing_id(pairing_id) {
        return Err(RuntimeError::PairingProtocol);
    }
    bounded_canonical(&json!({
        "kind": PAIRING_ACCEPTED_KIND,
        "pairing_id": pairing_id,
        "version": PAIRING_WIRE_VERSION,
    }))
}

fn validate_acknowledgement(bytes: &[u8], expected_pairing_id: &str) -> Result<()> {
    let value = parse_document(bytes)?;
    let object = exact_object(&value, &["kind", "pairing_id", "version"])?;
    if object.get("version").and_then(Value::as_u64) != Some(u64::from(PAIRING_WIRE_VERSION))
        || object.get("kind").and_then(Value::as_str) != Some(PAIRING_ACCEPTED_KIND)
        || object.get("pairing_id").and_then(Value::as_str) != Some(expected_pairing_id)
    {
        return Err(RuntimeError::PairingRejected);
    }
    Ok(())
}

fn close_initiator<T>(channel: &mut NoiseStream<T>) -> Result<()>
where
    T: Read + Write,
{
    channel.send_close()?;
    if matches!(channel.receive()?, ReceivedFrame::Close) {
        Ok(())
    } else {
        channel.close();
        Err(RuntimeError::ChannelFrameRejected)
    }
}

fn close_responder<T>(channel: &mut NoiseStream<T>) -> Result<()>
where
    T: Read + Write,
{
    if !matches!(channel.receive()?, ReceivedFrame::Close) {
        channel.close();
        return Err(RuntimeError::ChannelFrameRejected);
    }
    channel.send_close()
}

fn parse_document(bytes: &[u8]) -> Result<Value> {
    if bytes.is_empty() || bytes.len() > MAX_PAIRING_WIRE_BYTES {
        return Err(RuntimeError::PairingProtocol);
    }
    nlu_data::parse_strict_json(bytes, "pairing wire").map_err(|_| RuntimeError::PairingProtocol)
}

fn bounded_canonical(value: &Value) -> Result<Vec<u8>> {
    let encoded = nlu_data::canonical_json(value, "pairing wire")
        .map_err(|_| RuntimeError::PairingProtocol)?;
    if encoded.is_empty() || encoded.len() > MAX_PAIRING_WIRE_BYTES {
        return Err(RuntimeError::PairingProtocol);
    }
    Ok(encoded)
}

fn exact_object<'a>(value: &'a Value, fields: &[&str]) -> Result<&'a Map<String, Value>> {
    let object = value.as_object().ok_or(RuntimeError::PairingProtocol)?;
    if object.len() != fields.len() || object.keys().any(|field| !fields.contains(&field.as_str()))
    {
        return Err(RuntimeError::PairingProtocol);
    }
    Ok(object)
}

fn required_string<'a>(object: &'a Map<String, Value>, field: &str) -> Result<&'a str> {
    object
        .get(field)
        .and_then(Value::as_str)
        .ok_or(RuntimeError::PairingProtocol)
}

fn valid_pairing_id(value: &str) -> bool {
    value.len() == 32
        && value
            .bytes()
            .all(|byte| byte.is_ascii_digit() || matches!(byte, b'a'..=b'f'))
}

fn valid_peer_id(value: &str) -> bool {
    !value.is_empty()
        && value.len() <= 128
        && value.as_bytes()[0].is_ascii_alphanumeric()
        && value
            .bytes()
            .all(|byte| byte.is_ascii_alphanumeric() || matches!(byte, b'.' | b'_' | b':' | b'-'))
}
