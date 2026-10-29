use core::fmt::Write;

const REQUEST: &[u8] = include_bytes!("fixtures/FIXTURE_TECNICA_request-v1.json");
const ABSTENTION: &[u8] = include_bytes!("fixtures/FIXTURE_TECNICA_response-abstention-v1.json");
const CLARIFICATION: &[u8] =
    include_bytes!("fixtures/FIXTURE_TECNICA_response-clarification-v1.json");
const PLAN: &[u8] = include_bytes!("fixtures/FIXTURE_TECNICA_response-plan-v1.json");
const PROTOCOL_ERROR: &[u8] =
    include_bytes!("fixtures/FIXTURE_TECNICA_response-protocol-error-v1.json");
const REQUEST_SCHEMA: &[u8] = include_bytes!("../../../schemas/protocol-v1-request.schema.json");
const RESPONSE_SCHEMA: &[u8] = include_bytes!("../../../schemas/protocol-v1-response.schema.json");
const V1_SOURCE: &[u8] = include_bytes!("../src/v1.rs");

const ROUND_CONSTANTS: [u32; 64] = [
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

fn sha256_hex(input: &[u8]) -> String {
    let bit_length = u64::try_from(input.len())
        .expect("fixture length fits u64")
        .checked_mul(8)
        .expect("fixture bit length fits u64");
    let mut padded = input.to_vec();
    padded.push(0x80);
    while padded.len() % 64 != 56 {
        padded.push(0);
    }
    padded.extend_from_slice(&bit_length.to_be_bytes());

    let mut state = [
        0x6a09_e667_u32,
        0xbb67_ae85,
        0x3c6e_f372,
        0xa54f_f53a,
        0x510e_527f,
        0x9b05_688c,
        0x1f83_d9ab,
        0x5be0_cd19,
    ];
    let (blocks, remainder) = padded.as_chunks::<64>();
    assert!(remainder.is_empty());
    for block in blocks {
        let mut schedule = [0_u32; 64];
        let (words, remainder) = block.as_slice().as_chunks::<4>();
        assert!(remainder.is_empty());
        for (index, bytes) in words.iter().enumerate() {
            schedule[index] = u32::from_be_bytes(*bytes);
        }
        for index in 16..64 {
            let first = schedule[index - 15].rotate_right(7)
                ^ schedule[index - 15].rotate_right(18)
                ^ (schedule[index - 15] >> 3);
            let second = schedule[index - 2].rotate_right(17)
                ^ schedule[index - 2].rotate_right(19)
                ^ (schedule[index - 2] >> 10);
            schedule[index] = schedule[index - 16]
                .wrapping_add(first)
                .wrapping_add(schedule[index - 7])
                .wrapping_add(second);
        }

        let [mut a, mut b, mut c, mut d, mut e, mut f, mut g, mut h] = state;
        for (word, constant) in schedule.into_iter().zip(ROUND_CONSTANTS) {
            let sum_one = h
                .wrapping_add(e.rotate_right(6) ^ e.rotate_right(11) ^ e.rotate_right(25))
                .wrapping_add((e & f) ^ ((!e) & g))
                .wrapping_add(constant)
                .wrapping_add(word);
            let sum_zero = (a.rotate_right(2) ^ a.rotate_right(13) ^ a.rotate_right(22))
                .wrapping_add((a & b) ^ (a & c) ^ (b & c));
            h = g;
            g = f;
            f = e;
            e = d.wrapping_add(sum_one);
            d = c;
            c = b;
            b = a;
            a = sum_one.wrapping_add(sum_zero);
        }
        for (current, compressed) in state.iter_mut().zip([a, b, c, d, e, f, g, h]) {
            *current = current.wrapping_add(compressed);
        }
    }

    let mut result = String::with_capacity(64);
    for value in state {
        for byte in value.to_be_bytes() {
            write!(&mut result, "{byte:02x}").expect("write to String");
        }
    }
    result
}

#[test]
fn test_hash_implementation_matches_the_published_empty_digest() {
    assert_eq!(
        sha256_hex(b""),
        "e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855"
    );
}

#[test]
fn v1_fixture_bytes_are_frozen_exactly() {
    assert_eq!(
        REQUEST,
        b"{\"version\":1,\"request\":{\"text\":\"FIXTURE_TECNICA_A\"}}\n"
    );
    assert_eq!(
        ABSTENTION,
        b"{\"version\":1,\"outcome\":{\"type\":\"abstention\",\"reason\":\"insufficient_evidence\"}}\n"
    );
    assert_eq!(
        CLARIFICATION,
        b"{\"version\":1,\"outcome\":{\"type\":\"clarification\",\"options\":[{\"id\":\"fixture_tecnica:option\",\"intent\":\"fixture_tecnica:intent\",\"confidence_bps\":5000,\"evidence\":[{\"start\":0,\"end\":17}]}]}}\n"
    );
    assert_eq!(
        PLAN,
        b"{\"version\":1,\"outcome\":{\"type\":\"plan\",\"catalog_generation\":1,\"nodes\":[{\"id\":\"fixture_tecnica:node\",\"capability\":\"fixture_tecnica:capability\",\"operation\":\"fixture_tecnica:operation\",\"slots\":[],\"evidence\":[{\"start\":0,\"end\":17}]}],\"relations\":[]}}\n"
    );
    assert_eq!(
        PROTOCOL_ERROR,
        b"{\"version\":1,\"outcome\":{\"type\":\"protocol_error\",\"code\":\"input_too_large\",\"limit\":65536}}\n"
    );
}

#[test]
fn v1_source_schema_and_fixture_sha256_values_are_frozen() {
    let subjects = [
        (
            REQUEST,
            "f917a791d68e59704ccbbf4da4121748fe36e325743e5ef367547217a51e2d17",
        ),
        (
            ABSTENTION,
            "5099b10ea68d3a931e220a5ef2db560e17272129f3ada44349ee8a50abc33cad",
        ),
        (
            CLARIFICATION,
            "4d523580b4e177871d69f0ff28d4e873ae1a6401c69d01bed9fbf32505b7d4c6",
        ),
        (
            PLAN,
            "0bb6bd07357cec66db156042194e667d59f83b5469013835a0cd97a715637572",
        ),
        (
            PROTOCOL_ERROR,
            "541511bdf75add75af3a5c0ae3eeb7d1aaeba2aebff8bf8d56c30a4ad362d4e2",
        ),
        (
            REQUEST_SCHEMA,
            "5d6c8574c57ac9a8df4c1a3aec7e023d794be1c61b3aa9d8e82810e39d5e5231",
        ),
        (
            RESPONSE_SCHEMA,
            "6d45b099513cf1b57d42fc5f71e1a942fe56149fc7fac3cf6293829f5cd8c644",
        ),
        (
            V1_SOURCE,
            "dc30a3849e11f9a0edd3a72fb3d7e09df5daa51b1f7b50d13d978b1a38ffe9b4",
        ),
    ];
    for (bytes, expected) in subjects {
        assert_eq!(sha256_hex(bytes), expected);
    }
}
