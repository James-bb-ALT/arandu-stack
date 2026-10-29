use addon_runtime::PairingBindings;

const FIXTURE_TECNICA_CREDENTIAL: [u8; 32] = *b"FIXTURE_TECNICA_0123456789ABCDEF";

#[test]
fn domain_separated_bindings_are_stable_nonzero_and_redacted() {
    let bindings =
        PairingBindings::derive(&FIXTURE_TECNICA_CREDENTIAL).expect("FIXTURE_TECNICA bindings");

    assert_eq!(
        bindings.pairing_id_hex(),
        "867e1da8f99937836325ac5ce80a8b51"
    );
    assert_eq!(
        bindings.epoch_hex(),
        "c02c8a5d50d5d4e7711a5f8ac1d5c5ff9f67ebe0cde5b9d2927cd2fe897a94a3"
    );
    assert_ne!(bindings.epoch().as_bytes(), &[0_u8; 32]);
    assert_eq!(
        format!("{bindings:?}"),
        "PairingBindings(bindings=redacted)"
    );
    assert!(!format!("{bindings:?}").contains("FIXTURE_TECNICA"));
}

#[test]
fn credential_changes_both_bindings() {
    let first =
        PairingBindings::derive(&FIXTURE_TECNICA_CREDENTIAL).expect("FIXTURE_TECNICA first");
    let mut changed = FIXTURE_TECNICA_CREDENTIAL;
    changed[31] ^= 1;
    let second = PairingBindings::derive(&changed).expect("FIXTURE_TECNICA second");

    assert_ne!(first.pairing_id_hex(), second.pairing_id_hex());
    assert_ne!(first.epoch(), second.epoch());
}
