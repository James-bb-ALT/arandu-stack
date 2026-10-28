use std::{
    fs::File,
    io::Read,
    path::{Path, PathBuf},
};

use snow::{
    Error,
    params::{CipherChoice, DHChoice, HashChoice, NoiseParams},
    resolvers::{CryptoResolver, DefaultResolver},
    types::{Cipher, Dh, Hash, Random},
};

#[cfg(not(chacha20_force_soft))]
compile_error!("P13 capability probe requires the ChaCha20 software backend");
#[cfg(any(chacha20_force_neon, chacha20_force_avx2, chacha20_force_sse2))]
compile_error!("P13 capability probe prohibits accelerated ChaCha20 backends");
#[cfg(not(poly1305_force_soft))]
compile_error!("P13 capability probe requires the Poly1305 software backend");
#[cfg(not(curve25519_dalek_backend = "serial"))]
compile_error!("P13 capability probe requires the Curve25519 serial backend");
#[cfg(any(
    curve25519_dalek_backend = "auto",
    curve25519_dalek_backend = "fiat",
    curve25519_dalek_backend = "simd"
))]
compile_error!("P13 capability probe prohibits alternate Curve25519 backends");

pub const PROFILE: &str = "Noise_NNpsk0_25519_ChaChaPoly_BLAKE2s";
pub const PROTOCOL_VERSION: &[u8] = b"nlu-ha-companion-v1";
pub const ADDON_ROLE: &[u8] = b"addon-adapter";
pub const COMPANION_ROLE: &[u8] = b"ha-companion-integration";

#[derive(Clone, Copy, Debug, Eq, PartialEq)]
pub enum ConnectionDirection {
    AddonInitiates,
    CompanionInitiates,
}

impl ConnectionDirection {
    fn as_bytes(self) -> &'static [u8] {
        match self {
            Self::AddonInitiates => b"addon-to-companion",
            Self::CompanionInitiates => b"companion-to-addon",
        }
    }
}

#[derive(Clone, Copy)]
pub struct PrologueBinding<'a> {
    pub protocol_version: &'a [u8],
    pub epoch: u64,
    pub initiator_role: &'a [u8],
    pub responder_role: &'a [u8],
    pub direction: ConnectionDirection,
    pub connection_nonce: &'a [u8; 32],
}

impl PrologueBinding<'_> {
    pub fn encode(self) -> Vec<u8> {
        let mut encoded = b"NLU_HA_NOISE_PROLOGUE_V1".to_vec();
        append_field(&mut encoded, b"protocol", self.protocol_version);
        append_field(&mut encoded, b"epoch", &self.epoch.to_be_bytes());
        append_field(&mut encoded, b"initiator", self.initiator_role);
        append_field(&mut encoded, b"responder", self.responder_role);
        append_field(&mut encoded, b"direction", self.direction.as_bytes());
        append_field(&mut encoded, b"connection_nonce", self.connection_nonce);
        encoded
    }
}

fn append_field(encoded: &mut Vec<u8>, name: &[u8], value: &[u8]) {
    let name_length = u16::try_from(name.len()).expect("fixed field name must fit u16");
    let value_length = u16::try_from(value.len()).expect("bounded field value must fit u16");
    encoded.extend_from_slice(&name_length.to_be_bytes());
    encoded.extend_from_slice(name);
    encoded.extend_from_slice(&value_length.to_be_bytes());
    encoded.extend_from_slice(value);
}

struct FileRng(File);

impl Random for FileRng {
    fn try_fill_bytes(&mut self, destination: &mut [u8]) -> Result<(), Error> {
        self.0.read_exact(destination).map_err(|_| Error::Rng)
    }
}

pub struct OsResolver {
    random_device: PathBuf,
}

impl OsResolver {
    pub fn linux() -> Self {
        Self {
            random_device: PathBuf::from("/dev/urandom"),
        }
    }

    pub fn with_random_device(path: impl AsRef<Path>) -> Self {
        Self {
            random_device: path.as_ref().to_owned(),
        }
    }
}

impl CryptoResolver for OsResolver {
    fn resolve_rng(&self) -> Option<Box<dyn Random>> {
        File::open(&self.random_device)
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

pub fn builder<'a>(psk: &'a [u8; 32], prologue: &'a [u8]) -> snow::Builder<'a> {
    let parameters: NoiseParams = PROFILE.parse().expect("fixed profile must parse");
    snow::Builder::with_resolver(parameters, Box::new(OsResolver::linux()))
        .psk(0, psk)
        .expect("fixed PSK position must be valid")
        .prologue(prologue)
        .expect("bounded prologue must be valid")
}

#[cfg(test)]
mod tests {
    use super::*;
    use poly1305::{Poly1305, universal_hash::KeyInit};

    const EPOCH: u64 = 7;
    const NONCE: [u8; 32] = [0x24; 32];
    const POLY1305_MODULUS: [u32; 5] = [
        0xffff_fffb,
        0xffff_ffff,
        0xffff_ffff,
        0xffff_ffff,
        0x0000_0003,
    ];
    const FIXTURE_TECNICA_CASES: [(&str, u32, usize); 20] = [
        ("FIXTURE_TECNICA:P13-POLY1305:EMPTY", 0x1432_7698, 0),
        ("FIXTURE_TECNICA:P13-POLY1305:BYTE-1", 0x6d21_0fb4, 1),
        ("FIXTURE_TECNICA:P13-POLY1305:BYTE-3", 0x38a7_c125, 3),
        ("FIXTURE_TECNICA:P13-POLY1305:BLOCK-15", 0xe17c_492a, 15),
        ("FIXTURE_TECNICA:P13-POLY1305:BLOCK-16", 0x94b3_065d, 16),
        ("FIXTURE_TECNICA:P13-POLY1305:BLOCK-17", 0x2af8_d731, 17),
        ("FIXTURE_TECNICA:P13-POLY1305:BLOCK-30", 0x75c4_18e6, 30),
        ("FIXTURE_TECNICA:P13-POLY1305:BLOCK-31", 0xc90e_a347, 31),
        ("FIXTURE_TECNICA:P13-POLY1305:BLOCK-32", 0x4b71_5dc8, 32),
        ("FIXTURE_TECNICA:P13-POLY1305:BLOCK-33", 0xa326_f904, 33),
        ("FIXTURE_TECNICA:P13-POLY1305:BLOCK-47", 0x1fd9_842e, 47),
        ("FIXTURE_TECNICA:P13-POLY1305:BLOCK-48", 0x8762_3ab5, 48),
        ("FIXTURE_TECNICA:P13-POLY1305:BLOCK-49", 0xd40b_e791, 49),
        ("FIXTURE_TECNICA:P13-POLY1305:BLOCK-63", 0x59e8_14c3, 63),
        ("FIXTURE_TECNICA:P13-POLY1305:BLOCK-64", 0xb137_6f0a, 64),
        ("FIXTURE_TECNICA:P13-POLY1305:BLOCK-65", 0x03ac_d852, 65),
        ("FIXTURE_TECNICA:P13-POLY1305:BLOCK-127", 0x7e42_951d, 127),
        ("FIXTURE_TECNICA:P13-POLY1305:BLOCK-128", 0xca16_30f7, 128),
        ("FIXTURE_TECNICA:P13-POLY1305:BLOCK-129", 0x25f9_ab64, 129),
        ("FIXTURE_TECNICA:P13-POLY1305:BLOCK-257", 0x918d_47be, 257),
    ];

    fn binding() -> PrologueBinding<'static> {
        PrologueBinding {
            protocol_version: PROTOCOL_VERSION,
            epoch: EPOCH,
            initiator_role: ADDON_ROLE,
            responder_role: COMPANION_ROLE,
            direction: ConnectionDirection::AddonInitiates,
            connection_nonce: &NONCE,
        }
    }

    fn handshake(
        initiator_psk: &[u8; 32],
        responder_psk: &[u8; 32],
        initiator_prologue: &[u8],
        responder_prologue: &[u8],
    ) -> Result<(snow::TransportState, snow::TransportState), Error> {
        let mut initiator = builder(initiator_psk, initiator_prologue).build_initiator()?;
        let mut responder = builder(responder_psk, responder_prologue).build_responder()?;
        let mut message = [0_u8; 128];
        let mut payload = [0_u8; 32];

        let written = initiator.write_message(b"", &mut message)?;
        responder.read_message(&message[..written], &mut payload)?;
        let written = responder.write_message(b"", &mut message)?;
        initiator.read_message(&message[..written], &mut payload)?;

        Ok((
            initiator.into_transport_mode()?,
            responder.into_transport_mode()?,
        ))
    }

    fn fixture_tecnica_bytes(seed: u32, output: &mut [u8]) {
        let mut state = seed;
        for (index, byte) in output.iter_mut().enumerate() {
            let position = u32::try_from(index).expect("fixture length must fit u32");
            state =
                state.rotate_left(9).wrapping_add(0x4f1b_bcdd) ^ position.wrapping_mul(0x21f0_aaad);
            state ^= state.rotate_right(11);
            *byte = state.to_le_bytes()[index % 4];
        }
    }

    fn fixture_tecnica(seed: u32, message_length: usize) -> ([u8; 32], Vec<u8>) {
        let mut key = [0_u8; 32];
        let mut message = vec![0_u8; message_length];
        fixture_tecnica_bytes(seed ^ 0xa53c_917e, &mut key);
        fixture_tecnica_bytes(seed.rotate_left(13) ^ 0x36e2_4bd1, &mut message);

        for index in [3, 7, 11, 15] {
            key[index] |= 0xf0;
        }
        for index in [4, 8, 12] {
            key[index] |= 0x03;
        }
        if seed & 1 == 0 {
            for byte in &mut key[16..] {
                *byte |= 0xf0;
            }
        }

        (key, message)
    }

    fn low_word(value: u64) -> u32 {
        u32::try_from(value & u64::from(u32::MAX)).expect("masked value must fit u32")
    }

    fn little_endian_words(bytes: &[u8]) -> [u32; 5] {
        assert!(bytes.len() <= 20);
        let mut words = [0_u32; 5];
        for (index, byte) in bytes.iter().enumerate() {
            words[index / 4] |= u32::from(*byte) << ((index % 4) * 8);
        }
        words
    }

    fn add_words(left: &[u32; 5], right: &[u32; 5]) -> [u32; 5] {
        let mut result = [0_u32; 5];
        let mut carry = 0_u64;
        for ((result_word, left_word), right_word) in result.iter_mut().zip(left).zip(right) {
            let total = u64::from(*left_word) + u64::from(*right_word) + carry;
            *result_word = low_word(total);
            carry = total >> 32;
        }
        assert_eq!(carry, 0, "130-bit addition must fit the reference width");
        result
    }

    fn multiply_words(left: &[u32; 5], right: &[u32; 5]) -> [u32; 10] {
        let mut product = [0_u32; 10];
        for (left_index, left_word) in left.iter().enumerate() {
            let mut carry = 0_u64;
            for (right_index, right_word) in right.iter().enumerate() {
                let product_index = left_index + right_index;
                let total = u64::from(product[product_index])
                    + u64::from(*left_word) * u64::from(*right_word)
                    + carry;
                product[product_index] = low_word(total);
                carry = total >> 32;
            }
            product[left_index + right.len()] = low_word(carry);
        }
        product
    }

    fn at_least_modulus(value: &[u32; 5]) -> bool {
        for (value_word, modulus_word) in value.iter().zip(POLY1305_MODULUS.iter()).rev() {
            match value_word.cmp(modulus_word) {
                std::cmp::Ordering::Less => return false,
                std::cmp::Ordering::Greater => return true,
                std::cmp::Ordering::Equal => {}
            }
        }
        true
    }

    fn subtract_modulus(value: &mut [u32; 5]) {
        let mut borrow = 0_u64;
        for (value_word, modulus_word) in value.iter_mut().zip(POLY1305_MODULUS) {
            let subtrahend = u64::from(modulus_word) + borrow;
            let minuend = u64::from(*value_word);
            let (difference, next_borrow) = if minuend >= subtrahend {
                (minuend - subtrahend, 0)
            } else {
                ((1_u64 << 32) + minuend - subtrahend, 1)
            };
            *value_word = low_word(difference);
            borrow = next_borrow;
        }
        assert_eq!(borrow, 0, "modulus subtraction must not underflow");
    }

    fn reduce_product(product: &[u32; 10]) -> [u32; 5] {
        let mut remainder = [0_u32; 5];
        for bit_index in (0..320).rev() {
            let mut carry = 0_u32;
            for word in &mut remainder {
                let next_carry = *word >> 31;
                *word = (*word << 1) | carry;
                carry = next_carry;
            }
            assert_eq!(carry, 0, "reduced value must fit the reference width");
            remainder[0] |= (product[bit_index / 32] >> (bit_index % 32)) & 1;
            if at_least_modulus(&remainder) {
                subtract_modulus(&mut remainder);
            }
        }
        remainder
    }

    fn reference_poly1305(key: &[u8; 32], message: &[u8]) -> [u8; 16] {
        let mut r_bytes = [0_u8; 16];
        r_bytes.copy_from_slice(&key[..16]);
        for index in [3, 7, 11, 15] {
            r_bytes[index] &= 0x0f;
        }
        for index in [4, 8, 12] {
            r_bytes[index] &= 0xfc;
        }

        let r = little_endian_words(&r_bytes);
        let mut accumulator = [0_u32; 5];
        for chunk in message.chunks(16) {
            let mut block = little_endian_words(chunk);
            let marker_bit = chunk.len() * 8;
            block[marker_bit / 32] |= 1_u32 << (marker_bit % 32);
            accumulator = reduce_product(&multiply_words(&add_words(&accumulator, &block), &r));
        }

        let pad = little_endian_words(&key[16..]);
        let mut tag = [0_u8; 16];
        let mut carry = 0_u64;
        for (index, (accumulator_word, pad_word)) in
            accumulator.iter().zip(pad.iter()).take(4).enumerate()
        {
            let total = u64::from(*accumulator_word) + u64::from(*pad_word) + carry;
            tag[index * 4..index * 4 + 4].copy_from_slice(&low_word(total).to_le_bytes());
            carry = total >> 32;
        }
        tag
    }

    #[test]
    fn exact_profile_completes_and_exchanges_both_directions() {
        let key = [0x42; 32];
        let prologue = binding().encode();
        let (mut initiator, mut responder) =
            handshake(&key, &key, &prologue, &prologue).expect("exact profile must complete");
        let mut ciphertext = [0_u8; 128];
        let mut plaintext = [0_u8; 128];

        let written = initiator
            .write_message(b"request", &mut ciphertext)
            .expect("initiator encryption must succeed");
        let read = responder
            .read_message(&ciphertext[..written], &mut plaintext)
            .expect("responder decryption must succeed");
        assert_eq!(&plaintext[..read], b"request");

        let written = responder
            .write_message(b"response", &mut ciphertext)
            .expect("responder encryption must succeed");
        let read = initiator
            .read_message(&ciphertext[..written], &mut plaintext)
            .expect("initiator decryption must succeed");
        assert_eq!(&plaintext[..read], b"response");
    }

    #[test]
    fn wrong_psk_fails_closed() {
        let prologue = binding().encode();
        let result = handshake(&[0x42; 32], &[0x43; 32], &prologue, &prologue);
        assert!(matches!(result, Err(Error::Decrypt)));
    }

    #[test]
    fn every_prologue_binding_substitution_fails_closed() {
        let key = [0x42; 32];
        let expected = binding().encode();
        let changed_nonce = [0x25; 32];
        let substitutions = [
            PrologueBinding {
                protocol_version: b"nlu-ha-companion-v2",
                ..binding()
            },
            PrologueBinding {
                epoch: EPOCH + 1,
                ..binding()
            },
            PrologueBinding {
                initiator_role: b"ha-companion-integration",
                ..binding()
            },
            PrologueBinding {
                responder_role: b"addon-adapter",
                ..binding()
            },
            PrologueBinding {
                direction: ConnectionDirection::CompanionInitiates,
                ..binding()
            },
            PrologueBinding {
                connection_nonce: &changed_nonce,
                ..binding()
            },
        ];

        for substituted in substitutions {
            let result = handshake(&key, &key, &expected, &substituted.encode());
            assert!(matches!(result, Err(Error::Decrypt)));
        }
    }

    #[test]
    fn tampered_handshake_fails_closed() {
        let key = [0x42; 32];
        let prologue = binding().encode();
        let mut initiator = builder(&key, &prologue)
            .build_initiator()
            .expect("initiator must initialize");
        let mut responder = builder(&key, &prologue)
            .build_responder()
            .expect("responder must initialize");
        let mut message = [0_u8; 128];
        let mut payload = [0_u8; 32];
        let written = initiator
            .write_message(b"", &mut message)
            .expect("first handshake message must write");
        message[written - 1] ^= 1;
        assert!(matches!(
            responder.read_message(&message[..written], &mut payload),
            Err(Error::Decrypt)
        ));
    }

    #[test]
    fn replayed_transport_ciphertext_fails_closed() {
        let key = [0x42; 32];
        let prologue = binding().encode();
        let (mut initiator, mut responder) =
            handshake(&key, &key, &prologue, &prologue).expect("handshake must complete");
        let mut ciphertext = [0_u8; 128];
        let mut plaintext = [0_u8; 128];
        let written = initiator
            .write_message(b"request", &mut ciphertext)
            .expect("transport encryption must succeed");
        responder
            .read_message(&ciphertext[..written], &mut plaintext)
            .expect("first transport decryption must succeed");
        assert!(matches!(
            responder.read_message(&ciphertext[..written], &mut plaintext),
            Err(Error::Decrypt)
        ));
    }

    #[test]
    fn unavailable_entropy_device_prevents_state_construction() {
        let parameters: NoiseParams = PROFILE.parse().expect("fixed profile must parse");
        let key = [0x42; 32];
        let prologue = binding().encode();
        let error = snow::Builder::with_resolver(
            parameters,
            Box::new(OsResolver::with_random_device(
                "/definitely-missing-p13-random-device",
            )),
        )
        .psk(0, &key)
        .expect("fixed PSK position must be valid")
        .prologue(&prologue)
        .expect("bounded prologue must be valid")
        .build_initiator()
        .expect_err("missing entropy source must prevent construction");
        assert!(matches!(error, Error::Init(_)));
    }

    #[test]
    fn project_poly1305_matches_fixture_tecnica_reference() {
        for (label, seed, message_length) in FIXTURE_TECNICA_CASES {
            let (key_bytes, message) = fixture_tecnica(seed, message_length);
            let expected = reference_poly1305(&key_bytes, &message);
            let key = poly1305::Key::from_slice(&key_bytes);
            let actual = Poly1305::new(key).compute_unpadded(&message);
            assert_eq!(&actual[..], &expected, "{label}");
        }
    }
}
