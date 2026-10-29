# Third-party notices

The app is Apache-2.0 licensed. Its Rust dependency source is included under
`vendor/` with upstream license files and exact Cargo checksum manifests.

For the compiled app image, this distribution selects the Apache-2.0 option
where an upstream package offers it, except:

- `memchr` 2.8.3: MIT; copyright © 2015 Andrew Gallant.
- `unicode-ident` 1.0.24: Apache-2.0 plus the required Unicode-3.0 license;
  Unicode data and software copyright © 1991-2023 Unicode, Inc.

The image contains the complete Apache-2.0, selected MIT, and Unicode-3.0
license texts under `/licenses`.

The contextual grammar derives lexical templates from Arandu STT's MIT-licensed
repository, commit cd3cac0be30050b6899555f83eba0e74da984c6f. Dataset manifest
provenance is `user_supplied`; no acoustic model or STT runtime is included.
The MIT notice is preserved in `engine/data/STT-MIT.txt` and `/licenses/STT-MIT.txt`.

The statically linked musl runtime is MIT-licensed. Its complete upstream
copyright notice is preserved in `licenses/MUSL-COPYRIGHT.txt`, fetched from
https://git.musl-libc.org/cgit/musl/plain/COPYRIGHT?h=v1.2.5
(SHA-256 f9bc4423732350eb0b3f7ed7e91d530298476f8fec0c6c427a1c04ade22655af).
This is a notice reference, not a claim that the builder's installed libc and
the Rust toolchain's bundled target libc necessarily have identical versions.

Rust 1.98.0 standard-library notices, including third-party portions, are
copied unmodified from the pinned builder's `share/doc/rust/COPYRIGHT-library.html`
to `licenses/RUST-COPYRIGHT-library.html`
(SHA-256 68129500b616d5838629e68f55ff3aed5e096dacf60ce9eb41bbe599a563afa6).
Both notices are included in the scratch image under `/licenses/runtime`.
