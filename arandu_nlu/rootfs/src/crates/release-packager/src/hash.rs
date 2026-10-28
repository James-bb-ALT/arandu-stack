use crate::{PackagerError, PackagerErrorCode, Result};

const SHA256_INITIAL_STATE: [u32; 8] = [
    0x6a09_e667,
    0xbb67_ae85,
    0x3c6e_f372,
    0xa54f_f53a,
    0x510e_527f,
    0x9b05_688c,
    0x1f83_d9ab,
    0x5be0_cd19,
];

const SHA256_ROUND_CONSTANTS: [u32; 64] = [
    0x428a_2f98,
    0x7137_4491,
    0xb5c0_fbcf,
    0xe9b5_dba5,
    0x3956_c25b,
    0x59f1_11f1,
    0x923f_82a4,
    0xab1c_5ed5,
    0xd807_aa98,
    0x1283_5b01,
    0x2431_85be,
    0x550c_7dc3,
    0x72be_5d74,
    0x80de_b1fe,
    0x9bdc_06a7,
    0xc19b_f174,
    0xe49b_69c1,
    0xefbe_4786,
    0x0fc1_9dc6,
    0x240c_a1cc,
    0x2de9_2c6f,
    0x4a74_84aa,
    0x5cb0_a9dc,
    0x76f9_88da,
    0x983e_5152,
    0xa831_c66d,
    0xb003_27c8,
    0xbf59_7fc7,
    0xc6e0_0bf3,
    0xd5a7_9147,
    0x06ca_6351,
    0x1429_2967,
    0x27b7_0a85,
    0x2e1b_2138,
    0x4d2c_6dfc,
    0x5338_0d13,
    0x650a_7354,
    0x766a_0abb,
    0x81c2_c92e,
    0x9272_2c85,
    0xa2bf_e8a1,
    0xa81a_664b,
    0xc24b_8b70,
    0xc76c_51a3,
    0xd192_e819,
    0xd699_0624,
    0xf40e_3585,
    0x106a_a070,
    0x19a4_c116,
    0x1e37_6c08,
    0x2748_774c,
    0x34b0_bcb5,
    0x391c_0cb3,
    0x4ed8_aa4a,
    0x5b9c_ca4f,
    0x682e_6ff3,
    0x748f_82ee,
    0x78a5_636f,
    0x84c8_7814,
    0x8cc7_0208,
    0x90be_fffa,
    0xa450_6ceb,
    0xbef9_a3f7,
    0xc671_78f2,
];

pub(crate) fn sha256(bytes: &[u8]) -> Result<[u8; 32]> {
    let bit_len = checked_bit_len(bytes.len(), "SHA-256")?;
    let mut state = SHA256_INITIAL_STATE;
    let padded = padded_message(bytes, bit_len)?;
    for block in padded.as_chunks::<64>().0 {
        sha256_compress(&mut state, block);
    }
    let mut output = [0_u8; 32];
    for (index, value) in state.iter().enumerate() {
        output[index * 4..index * 4 + 4].copy_from_slice(&value.to_be_bytes());
    }
    Ok(output)
}

fn sha256_compress(state: &mut [u32; 8], block: &[u8; 64]) {
    let mut words = [0_u32; 64];
    for index in 0..16 {
        words[index] = u32::from_be_bytes(
            block[index * 4..index * 4 + 4]
                .try_into()
                .expect("fixed SHA-256 word"),
        );
    }
    for index in 16..64 {
        let s0 = words[index - 15].rotate_right(7)
            ^ words[index - 15].rotate_right(18)
            ^ (words[index - 15] >> 3);
        let s1 = words[index - 2].rotate_right(17)
            ^ words[index - 2].rotate_right(19)
            ^ (words[index - 2] >> 10);
        words[index] = words[index - 16]
            .wrapping_add(s0)
            .wrapping_add(words[index - 7])
            .wrapping_add(s1);
    }

    let [mut a, mut b, mut c, mut d, mut e, mut f, mut g, mut h] = *state;
    for index in 0..64 {
        let choose = (e & f) ^ ((!e) & g);
        let majority = (a & b) ^ (a & c) ^ (b & c);
        let sum0 = a.rotate_right(2) ^ a.rotate_right(13) ^ a.rotate_right(22);
        let sum1 = e.rotate_right(6) ^ e.rotate_right(11) ^ e.rotate_right(25);
        let first = h
            .wrapping_add(sum1)
            .wrapping_add(choose)
            .wrapping_add(SHA256_ROUND_CONSTANTS[index])
            .wrapping_add(words[index]);
        let second = sum0.wrapping_add(majority);
        h = g;
        g = f;
        f = e;
        e = d.wrapping_add(first);
        d = c;
        c = b;
        b = a;
        a = first.wrapping_add(second);
    }
    for (slot, value) in state.iter_mut().zip([a, b, c, d, e, f, g, h]) {
        *slot = slot.wrapping_add(value);
    }
}

pub(crate) fn sha1(bytes: &[u8]) -> Result<[u8; 20]> {
    let bit_len = checked_bit_len(bytes.len(), "SHA-1")?;
    let mut state = [
        0x6745_2301_u32,
        0xefcd_ab89,
        0x98ba_dcfe,
        0x1032_5476,
        0xc3d2_e1f0,
    ];
    let padded = padded_message(bytes, bit_len)?;
    for block in padded.as_chunks::<64>().0 {
        let mut words = [0_u32; 80];
        for index in 0..16 {
            words[index] = u32::from_be_bytes(
                block[index * 4..index * 4 + 4]
                    .try_into()
                    .expect("fixed SHA-1 word"),
            );
        }
        for index in 16..80 {
            words[index] =
                (words[index - 3] ^ words[index - 8] ^ words[index - 14] ^ words[index - 16])
                    .rotate_left(1);
        }
        let [mut a, mut b, mut c, mut d, mut e] = state;
        for (index, word) in words.iter().enumerate() {
            let (function, constant) = match index {
                0..=19 => ((b & c) | ((!b) & d), 0x5a82_7999),
                20..=39 => (b ^ c ^ d, 0x6ed9_eba1),
                40..=59 => ((b & c) | (b & d) | (c & d), 0x8f1b_bcdc),
                _ => (b ^ c ^ d, 0xca62_c1d6),
            };
            let next = a
                .rotate_left(5)
                .wrapping_add(function)
                .wrapping_add(e)
                .wrapping_add(constant)
                .wrapping_add(*word);
            e = d;
            d = c;
            c = b.rotate_left(30);
            b = a;
            a = next;
        }
        for (slot, value) in state.iter_mut().zip([a, b, c, d, e]) {
            *slot = slot.wrapping_add(value);
        }
    }
    let mut output = [0_u8; 20];
    for (index, value) in state.iter().enumerate() {
        output[index * 4..index * 4 + 4].copy_from_slice(&value.to_be_bytes());
    }
    Ok(output)
}

fn checked_bit_len(byte_len: usize, algorithm: &str) -> Result<u64> {
    let byte_len = u64::try_from(byte_len).map_err(|_| {
        PackagerError::new(
            PackagerErrorCode::ResourceLimit,
            format!("{algorithm} input length does not fit u64"),
        )
    })?;
    byte_len.checked_mul(8).ok_or_else(|| {
        PackagerError::new(
            PackagerErrorCode::ResourceLimit,
            format!("{algorithm} input is too large"),
        )
    })
}

fn padded_message(bytes: &[u8], bit_len: u64) -> Result<Vec<u8>> {
    let with_marker = bytes.len().checked_add(1).ok_or_else(|| {
        PackagerError::new(PackagerErrorCode::ResourceLimit, "hash padding overflow")
    })?;
    let zero_padding = (56 + 64 - (with_marker % 64)) % 64;
    let capacity = with_marker
        .checked_add(zero_padding)
        .and_then(|value| value.checked_add(8))
        .ok_or_else(|| {
            PackagerError::new(PackagerErrorCode::ResourceLimit, "hash padding overflow")
        })?;
    let mut padded = Vec::with_capacity(capacity);
    padded.extend_from_slice(bytes);
    padded.push(0x80);
    padded.resize(with_marker + zero_padding, 0);
    padded.extend_from_slice(&bit_len.to_be_bytes());
    Ok(padded)
}

pub(crate) fn hex(bytes: &[u8]) -> String {
    const DIGITS: &[u8; 16] = b"0123456789abcdef";
    let mut output = String::with_capacity(bytes.len() * 2);
    for byte in bytes {
        output.push(char::from(DIGITS[usize::from(byte >> 4)]));
        output.push(char::from(DIGITS[usize::from(byte & 0x0f)]));
    }
    output
}

pub fn sha256_hex(bytes: &[u8]) -> Result<String> {
    Ok(hex(&sha256(bytes)?))
}

pub fn sha1_hex(bytes: &[u8]) -> Result<String> {
    Ok(hex(&sha1(bytes)?))
}

#[cfg(test)]
mod tests {
    use super::{sha1_hex, sha256_hex};

    #[test]
    fn fixture_tecnica_hashes_match_public_vectors() {
        assert_eq!(
            sha256_hex(b"abc").expect("FIXTURE_TECNICA SHA-256"),
            "ba7816bf8f01cfea414140de5dae2223b00361a396177a9cb410ff61f20015ad"
        );
        assert_eq!(
            sha1_hex(b"abc").expect("FIXTURE_TECNICA SHA-1"),
            "a9993e364706816aba3e25717850c26c9cd0d89d"
        );
    }
}
