use release_packager::{
    Architecture, ArchitectureBinding, ArchitectureEnablement, ArchitectureStage, ByteClaim,
    ByteOrigin, DeclaredInput, EmissionMode, EmissionPolicy, OciImageRequest, OciRootEntry,
    ReleaseAuthorization, ReleaseMetadataRequest, ShippedFile, SpdxDocumentInput, SpdxFile,
    SpdxPackageInput, TarEntry, emit_oci_image_layout, emit_posix_ustar, emit_release_metadata,
    emit_spdx_23_json,
};

#[test]
fn fixture_tecnica_release_primitives_compose_deterministically() {
    let dry_amd64 = dry_run(vec![Architecture::Amd64]);
    let oci_request = OciImageRequest {
        reference: "FIXTURE_TECNICA".to_owned(),
        architecture: Architecture::Amd64,
        entries: vec![
            OciRootEntry {
                entry: TarEntry::file(
                    "bin/FIXTURE_TECNICA",
                    0o755,
                    fixture_elf(Architecture::Amd64),
                ),
                architecture: ArchitectureBinding::Native(Architecture::Amd64),
            },
            OciRootEntry {
                entry: TarEntry::directory("bin", 0o755),
                architecture: ArchitectureBinding::Independent,
            },
            OciRootEntry {
                entry: TarEntry::file("LICENSE", 0o644, b"FIXTURE_TECNICA"),
                architecture: ArchitectureBinding::Independent,
            },
        ],
    };
    let first_layout = emit_oci_image_layout(&oci_request, &dry_amd64).expect("first OCI layout");
    let mut permuted_request = oci_request.clone();
    permuted_request.entries.reverse();
    let second_layout =
        emit_oci_image_layout(&permuted_request, &dry_amd64).expect("second OCI layout");
    assert_eq!(first_layout, second_layout);

    let archive_entries = vec![
        TarEntry::directory("bin", 0o755),
        TarEntry::file(
            "bin/FIXTURE_TECNICA",
            0o755,
            fixture_elf(Architecture::Amd64),
        ),
        TarEntry::file("LICENSE", 0o644, b"FIXTURE_TECNICA"),
    ];
    let archive = emit_posix_ustar(&archive_entries, &dry_amd64).expect("archive");
    let mut reversed = archive_entries;
    reversed.reverse();
    let reversed_archive = emit_posix_ustar(&reversed, &dry_amd64).expect("reversed archive");
    assert_eq!(archive, reversed_archive);

    let source = DeclaredInput::from_bytes(
        "FIXTURE_TECNICA/source",
        b"FIXTURE_TECNICA-source",
        "Apache-2.0",
        "FIXTURE_TECNICA",
    )
    .expect("source");
    let toolchain = DeclaredInput::from_bytes(
        "FIXTURE_TECNICA/toolchain",
        b"FIXTURE_TECNICA-toolchain",
        "Apache-2.0",
        "FIXTURE_TECNICA",
    )
    .expect("toolchain");
    let shipped = ShippedFile {
        path: "companion.tar".to_owned(),
        mode: 0o644,
        bytes: archive.bytes().to_vec(),
        claims: vec![ByteClaim {
            output_offset: 0,
            length: u64::try_from(archive.bytes().len()).expect("FIXTURE_TECNICA length"),
            origin: ByteOrigin::Derived {
                input_ids: vec![
                    "FIXTURE_TECNICA/source".to_owned(),
                    "FIXTURE_TECNICA/toolchain".to_owned(),
                ],
                transformation: "FIXTURE_TECNICA/package".to_owned(),
            },
        }],
    };
    let metadata_request = ReleaseMetadataRequest {
        release_id: "FIXTURE_TECNICA/release".to_owned(),
        release_version: "1.0.0".to_owned(),
        inputs: vec![source, toolchain],
        files: vec![shipped],
    };
    let metadata = emit_release_metadata(
        &metadata_request,
        &dry_run(vec![Architecture::Amd64, Architecture::Arm64]),
    )
    .expect("release metadata");
    assert_eq!(metadata.reconciliation().file_count(), 3);
    assert!(metadata.checksums_bytes().ends_with(b"\n"));

    let spdx = emit_spdx_23_json(
        &SpdxDocumentInput {
            name: "FIXTURE_TECNICA-document".to_owned(),
            namespace: "urn:FIXTURE_TECNICA:release".to_owned(),
            package: SpdxPackageInput {
                name: "FIXTURE_TECNICA-package".to_owned(),
                version: "1.0.0".to_owned(),
                download_location: "NOASSERTION".to_owned(),
                license_concluded: "Apache-2.0".to_owned(),
                license_declared: "Apache-2.0".to_owned(),
                copyright_text: "NOASSERTION".to_owned(),
                files: vec![SpdxFile {
                    path: "companion.tar".to_owned(),
                    bytes: archive.bytes().to_vec(),
                    license_concluded: "Apache-2.0".to_owned(),
                    license_info_in_files: vec!["Apache-2.0".to_owned()],
                    copyright_text: "NOASSERTION".to_owned(),
                }],
            },
        },
        &dry_amd64,
    )
    .expect("SPDX");
    assert!(
        spdx.bytes()
            .windows(b"\"spdxVersion\":\"SPDX-2.3\"".len())
            .any(|window| window == b"\"spdxVersion\":\"SPDX-2.3\"")
    );
}

#[test]
fn fixture_tecnica_complete_authorization_allows_production_tagging() {
    let policy = EmissionPolicy::new(
        EmissionMode::Production,
        vec![Architecture::Amd64, Architecture::Arm64],
        complete_authorization(),
    );
    let archive = emit_posix_ustar(
        &[TarEntry::file("FIXTURE_TECNICA", 0o644, b"fixture")],
        &policy,
    )
    .expect("authorized production archive");
    assert_eq!(archive.mode(), EmissionMode::Production);
}

#[test]
fn fixture_tecnica_ustar_header_checksum_and_modes_are_canonical() {
    let archive = emit_posix_ustar(
        &[
            TarEntry::directory("bin", 0o755),
            TarEntry::file("bin/FIXTURE_TECNICA", 0o555, b"x"),
        ],
        &dry_run(vec![Architecture::Amd64]),
    )
    .expect("archive");
    let bytes = archive.bytes();
    assert_eq!(&bytes[100..108], b"0000755\0");
    assert_eq!(bytes[156], b'5');
    let second = 512;
    assert_eq!(&bytes[second + 100..second + 108], b"0000555\0");
    assert_eq!(bytes[second + 156], b'0');

    for offset in [0, second] {
        let header = &bytes[offset..offset + 512];
        let expected = parse_octal(&header[148..154]);
        let mut normalized = header.to_vec();
        normalized[148..156].fill(b' ');
        let actual: u64 = normalized.iter().map(|byte| u64::from(*byte)).sum();
        assert_eq!(actual, expected);
    }
}

fn parse_octal(bytes: &[u8]) -> u64 {
    bytes
        .iter()
        .fold(0_u64, |value, byte| value * 8 + u64::from(byte - b'0'))
}

fn dry_run(targets: Vec<Architecture>) -> EmissionPolicy {
    EmissionPolicy::new(
        EmissionMode::DryRun,
        targets,
        ReleaseAuthorization::default(),
    )
}

fn fixture_elf(architecture: Architecture) -> Vec<u8> {
    let machine = match architecture {
        Architecture::Amd64 => 62_u16,
        Architecture::Arm64 => 183_u16,
    };
    let mut bytes = vec![0_u8; 64];
    bytes[..4].copy_from_slice(b"\x7fELF");
    bytes[4] = 2;
    bytes[5] = 1;
    bytes[6] = 1;
    bytes[18..20].copy_from_slice(&machine.to_le_bytes());
    bytes
}

fn complete_authorization() -> ReleaseAuthorization {
    ReleaseAuthorization {
        artifact_build_allowed: true,
        inherited_p14_gate: Some("FIXTURE_TECNICA/p14".to_owned()),
        source_admission: Some("FIXTURE_TECNICA/source".to_owned()),
        license_reconciliation: Some("FIXTURE_TECNICA/license".to_owned()),
        p15_release_gate: Some("FIXTURE_TECNICA/p15".to_owned()),
        architectures: vec![
            enabled_architecture(Architecture::Amd64),
            enabled_architecture(Architecture::Arm64),
        ],
    }
}

fn enabled_architecture(architecture: Architecture) -> ArchitectureEnablement {
    let name = architecture.as_oci_str();
    ArchitectureEnablement {
        architecture,
        stage: ArchitectureStage::Enabled,
        build_admission: Some(format!("FIXTURE_TECNICA/{name}/build")),
        native_validation: Some(format!("FIXTURE_TECNICA/{name}/native")),
        ha_lifecycle_validation: Some(format!("FIXTURE_TECNICA/{name}/ha")),
        reproducibility: Some(format!("FIXTURE_TECNICA/{name}/reproducible")),
        enablement: Some(format!("FIXTURE_TECNICA/{name}/enabled")),
    }
}
