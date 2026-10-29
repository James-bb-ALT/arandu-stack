use noise_channel::PairingEpoch;

use crate::{Result, RuntimeError};

const PAIRING_ID_DOMAIN: &[u8] = b"ptbr-nlu-pairing-id-v1\0";
const PAIRING_EPOCH_DOMAIN: &[u8] = b"ptbr-nlu-pairing-epoch-v1\0";
const PAIRING_ID_BYTES: usize = 16;

#[derive(Clone, Copy, Eq, PartialEq)]
pub struct PairingBindings {
    pairing_id: [u8; PAIRING_ID_BYTES],
    epoch: PairingEpoch,
}

impl PairingBindings {
    pub fn derive(credential: &[u8; 32]) -> Result<Self> {
        let id_digest = domain_digest(PAIRING_ID_DOMAIN, credential)?;
        let mut pairing_id = [0_u8; PAIRING_ID_BYTES];
        pairing_id.copy_from_slice(&id_digest[..PAIRING_ID_BYTES]);
        let epoch = PairingEpoch::new(domain_digest(PAIRING_EPOCH_DOMAIN, credential)?)
            .map_err(|_| RuntimeError::InvalidPairingMaterial)?;
        Ok(Self { pairing_id, epoch })
    }

    #[must_use]
    pub fn pairing_id_hex(self) -> String {
        encode_hex(&self.pairing_id)
    }

    #[must_use]
    pub const fn epoch(self) -> PairingEpoch {
        self.epoch
    }

    #[must_use]
    pub fn epoch_hex(self) -> String {
        encode_hex(self.epoch.as_bytes())
    }
}

impl core::fmt::Debug for PairingBindings {
    fn fmt(&self, formatter: &mut core::fmt::Formatter<'_>) -> core::fmt::Result {
        formatter.write_str("PairingBindings(bindings=redacted)")
    }
}

fn domain_digest(domain: &[u8], credential: &[u8; 32]) -> Result<[u8; 32]> {
    let domain_length =
        u32::try_from(domain.len()).map_err(|_| RuntimeError::InvalidPairingMaterial)?;
    let credential_length =
        u32::try_from(credential.len()).map_err(|_| RuntimeError::InvalidPairingMaterial)?;
    let mut input = Vec::with_capacity(domain.len() + credential.len() + 8);
    input.extend_from_slice(&domain_length.to_be_bytes());
    input.extend_from_slice(domain);
    input.extend_from_slice(&credential_length.to_be_bytes());
    input.extend_from_slice(credential);
    let encoded = nlu_data::sha256_hex(&input).map_err(|_| RuntimeError::InvalidPairingMaterial)?;
    decode_digest(&encoded)
}

fn decode_digest(encoded: &str) -> Result<[u8; 32]> {
    if encoded.len() != 64 {
        return Err(RuntimeError::InvalidPairingMaterial);
    }
    let mut output = [0_u8; 32];
    for (destination, pair) in output.iter_mut().zip(encoded.as_bytes().as_chunks::<2>().0) {
        let high = hex_nibble(pair[0]).ok_or(RuntimeError::InvalidPairingMaterial)?;
        let low = hex_nibble(pair[1]).ok_or(RuntimeError::InvalidPairingMaterial)?;
        *destination = (high << 4) | low;
    }
    Ok(output)
}

fn encode_hex(bytes: &[u8]) -> String {
    const HEX: &[u8; 16] = b"0123456789abcdef";
    let mut output = String::with_capacity(bytes.len() * 2);
    for byte in bytes {
        output.push(char::from(HEX[usize::from(byte >> 4)]));
        output.push(char::from(HEX[usize::from(byte & 0x0f)]));
    }
    output
}

const fn hex_nibble(byte: u8) -> Option<u8> {
    match byte {
        b'0'..=b'9' => Some(byte - b'0'),
        b'a'..=b'f' => Some(byte - b'a' + 10),
        _ => None,
    }
}
