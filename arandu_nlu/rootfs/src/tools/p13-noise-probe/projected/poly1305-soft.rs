//! Project-authored 64-bit Poly1305 backend for the P13 capability probe.
//!
//! This implementation uses three radix limbs of 44, 44, and 42 bits. It is
//! restricted to the selected 64-bit host, amd64 Linux, and aarch64 Linux
//! configurations.

use universal_hash::{
    UhfBackend, UniversalHash,
    consts::{U1, U16},
    crypto_common::{BlockSizeUser, ParBlocksSizeUser},
};

use crate::{Block, Key, Tag};

const MASK_44: u64 = (1_u64 << 44) - 1;
const MASK_42: u64 = (1_u64 << 42) - 1;
const R_CLAMP: u128 = 0x0fff_fffc_0fff_fffc_0fff_fffc_0fff_ffff;

#[derive(Clone, Default)]
pub(crate) struct State {
    r: [u64; 3],
    h: [u64; 3],
    pad: u128,
}

impl State {
    pub(crate) fn new(key: &Key) -> Self {
        let mut r_bytes = [0_u8; 16];
        let mut pad_bytes = [0_u8; 16];
        r_bytes.copy_from_slice(&key[..16]);
        pad_bytes.copy_from_slice(&key[16..]);

        let r = u128::from_le_bytes(r_bytes) & R_CLAMP;
        Self {
            r: [
                (r & u128::from(MASK_44)) as u64,
                ((r >> 44) & u128::from(MASK_44)) as u64,
                ((r >> 88) & u128::from(MASK_42)) as u64,
            ],
            h: [0; 3],
            pad: u128::from_le_bytes(pad_bytes),
        }
    }

    pub(crate) fn compute_block(&mut self, block: &Block, partial: bool) {
        let mut block_bytes = [0_u8; 16];
        block_bytes.copy_from_slice(block);
        let value = u128::from_le_bytes(block_bytes);

        let h0 = self.h[0] + (value & u128::from(MASK_44)) as u64;
        let h1 = self.h[1] + ((value >> 44) & u128::from(MASK_44)) as u64;
        let h2 = self.h[2]
            + ((value >> 88) & u128::from(MASK_42)) as u64
            + if partial { 0 } else { 1_u64 << 40 };

        let r0 = u128::from(self.r[0]);
        let r1 = u128::from(self.r[1]);
        let r2 = u128::from(self.r[2]);
        let h0 = u128::from(h0);
        let h1 = u128::from(h1);
        let h2 = u128::from(h2);

        let mut d0 = h0 * r0 + h1 * (r2 * 20) + h2 * (r1 * 20);
        let mut d1 = h0 * r1 + h1 * r0 + h2 * (r2 * 20);
        let mut d2 = h0 * r2 + h1 * r1 + h2 * r0;

        let mut carry = d0 >> 44;
        d0 &= u128::from(MASK_44);
        d1 += carry;

        carry = d1 >> 44;
        d1 &= u128::from(MASK_44);
        d2 += carry;

        carry = d2 >> 42;
        d2 &= u128::from(MASK_42);
        d0 += carry * 5;

        carry = d0 >> 44;
        d0 &= u128::from(MASK_44);
        d1 += carry;

        self.h = [d0 as u64, d1 as u64, d2 as u64];
    }

    pub(crate) fn finalize_mut(&mut self) -> Tag {
        let mut h0 = self.h[0];
        let mut h1 = self.h[1];
        let mut h2 = self.h[2];

        let mut carry = h1 >> 44;
        h1 &= MASK_44;
        h2 += carry;

        carry = h2 >> 42;
        h2 &= MASK_42;
        h0 += carry * 5;

        carry = h0 >> 44;
        h0 &= MASK_44;
        h1 += carry;

        let mut g0 = h0 + 5;
        carry = g0 >> 44;
        g0 &= MASK_44;

        let mut g1 = h1 + carry;
        carry = g1 >> 44;
        g1 &= MASK_44;

        let g2 = h2.wrapping_add(carry).wrapping_sub(1_u64 << 42);
        let use_reduced = (g2 >> 63).wrapping_sub(1);
        let keep_original = !use_reduced;
        h0 = (h0 & keep_original) | (g0 & use_reduced);
        h1 = (h1 & keep_original) | (g1 & use_reduced);
        h2 = (h2 & keep_original) | (g2 & use_reduced);

        let accumulator = u128::from(h0)
            | (u128::from(h1) << 44)
            | (u128::from(h2) << 88);
        let tag_bytes = accumulator.wrapping_add(self.pad).to_le_bytes();
        let mut tag = Block::default();
        tag.copy_from_slice(&tag_bytes);
        tag
    }
}

#[cfg(feature = "zeroize")]
impl Drop for State {
    fn drop(&mut self) {
        use zeroize::Zeroize;
        self.r.zeroize();
        self.h.zeroize();
        self.pad.zeroize();
    }
}

impl BlockSizeUser for State {
    type BlockSize = U16;
}

impl ParBlocksSizeUser for State {
    type ParBlocksSize = U1;
}

impl UhfBackend for State {
    fn proc_block(&mut self, block: &Block) {
        self.compute_block(block, false);
    }
}

impl UniversalHash for State {
    fn update_with_backend(
        &mut self,
        f: impl universal_hash::UhfClosure<BlockSize = Self::BlockSize>,
    ) {
        f.call(self);
    }

    fn finalize(mut self) -> Tag {
        self.finalize_mut()
    }
}
