use release_packager::{
    Architecture, ArchitectureBinding, ArchitectureEnablement, ArchitectureStage, ByteClaim,
    ByteOrigin, DeclaredInput, EmissionMode, EmissionPolicy, OciImageRequest, OciRootEntry,
    PackagerErrorCode, ReleaseAuthorization, ShippedFile, TarEntry, TarEntryKind, TarMetadata,
    emit_oci_image_layout, emit_posix_ustar, reconcile_release_bytes,
};

#[test]
fn fixture_tecnica_unsafe_path_mutations_are_rejected() {
    let paths = [
        "",
        "/FIXTURE_TECNICA",
        "../FIXTURE_TECNICA",
        "a/../FIXTURE_TECNICA",
        "a/./FIXTURE_TECNICA",
        "a//FIXTURE_TECNICA",
        "FIXTURE_TECNICA/",
        "a\\FIXTURE_TECNICA",
        "a\nFIXTURE_TECNICA",
    ];
    for path in paths {
        assert!(
            emit_posix_ustar(
                &[TarEntry::file(path, 0o644, b"x")],
                &dry_run(Architecture::Amd64),
            )
            .is_err(),
            "unsafe path unexpectedly accepted: {path:?}"
        );
    }
    let too_long = "a".repeat(256);
    assert!(
        emit_posix_ustar(
            &[TarEntry::file(too_long, 0o644, b"x")],
            &dry_run(Architecture::Amd64),
        )
        .is_err()
    );
}

#[test]
fn fixture_tecnica_special_file_mutations_are_rejected() {
    let kinds = [
        TarEntryKind::SymbolicLink {
            target: "FIXTURE_TECNICA".to_owned(),
        },
        TarEntryKind::HardLink {
            target: "FIXTURE_TECNICA".to_owned(),
        },
        TarEntryKind::CharacterDevice { major: 1, minor: 2 },
        TarEntryKind::BlockDevice { major: 1, minor: 2 },
        TarEntryKind::Fifo,
    ];
    for kind in kinds {
        let error = emit_posix_ustar(
            &[TarEntry {
                path: "FIXTURE_TECNICA".to_owned(),
                kind,
                metadata: TarMetadata::canonical(0o644),
                bytes: Vec::new(),
            }],
            &dry_run(Architecture::Amd64),
        )
        .expect_err("special file must fail");
        assert_eq!(error.code(), PackagerErrorCode::UnsupportedEntryKind);
    }
}

#[test]
fn fixture_tecnica_each_nondeterministic_metadata_mutation_is_rejected() {
    for mutation in 0..5 {
        let mut entry = TarEntry::file("FIXTURE_TECNICA", 0o644, b"x");
        match mutation {
            0 => entry.metadata.uid = 1,
            1 => entry.metadata.gid = 1,
            2 => entry.metadata.mtime = 1,
            3 => entry.metadata.user_name = "FIXTURE_TECNICA".to_owned(),
            4 => entry.metadata.group_name = "FIXTURE_TECNICA".to_owned(),
            _ => unreachable!(),
        }
        let error = emit_posix_ustar(&[entry], &dry_run(Architecture::Amd64))
            .expect_err("metadata mutation must fail");
        assert_eq!(error.code(), PackagerErrorCode::NondeterministicMetadata);
    }
}

#[test]
fn fixture_tecnica_every_removed_gate_refuses_production() {
    let base = complete_authorization();
    let mut mutations = Vec::new();

    let mut authorization = base.clone();
    authorization.artifact_build_allowed = false;
    mutations.push(authorization);
    let mut authorization = base.clone();
    authorization.inherited_p14_gate = None;
    mutations.push(authorization);
    let mut authorization = base.clone();
    authorization.source_admission = None;
    mutations.push(authorization);
    let mut authorization = base.clone();
    authorization.license_reconciliation = None;
    mutations.push(authorization);
    let mut authorization = base.clone();
    authorization.p15_release_gate = None;
    mutations.push(authorization);
    for architecture_index in 0..2 {
        for attestation_index in 0..5 {
            let mut authorization = base.clone();
            let state = &mut authorization.architectures[architecture_index];
            match attestation_index {
                0 => state.build_admission = None,
                1 => state.native_validation = None,
                2 => state.ha_lifecycle_validation = None,
                3 => state.reproducibility = None,
                4 => state.enablement = None,
                _ => unreachable!(),
            }
            mutations.push(authorization);
        }
    }

    for authorization in mutations {
        let policy = EmissionPolicy::new(
            EmissionMode::Production,
            vec![Architecture::Amd64, Architecture::Arm64],
            authorization,
        );
        assert!(
            emit_posix_ustar(&[TarEntry::file("FIXTURE_TECNICA", 0o644, b"x")], &policy,).is_err()
        );
    }
}

#[test]
fn fixture_tecnica_each_unrepresented_or_changed_byte_is_rejected() {
    let bytes: Vec<u8> = (0_u8..32).collect();
    let input = DeclaredInput::from_bytes(
        "FIXTURE_TECNICA/input",
        bytes.clone(),
        "Apache-2.0",
        "FIXTURE_TECNICA",
    )
    .expect("input");
    let claims: Vec<_> = (0_u64..32)
        .map(|offset| ByteClaim {
            output_offset: offset,
            length: 1,
            origin: ByteOrigin::Copied {
                input_id: "FIXTURE_TECNICA/input".to_owned(),
                input_offset: offset,
            },
        })
        .collect();

    for removed in 0..claims.len() {
        let mut mutated_claims = claims.clone();
        mutated_claims.remove(removed);
        let file = shipped(bytes.clone(), mutated_claims);
        assert!(
            reconcile_release_bytes(
                std::slice::from_ref(&input),
                &[file],
                &dry_run(Architecture::Amd64),
            )
            .is_err(),
            "removed claim {removed} unexpectedly passed"
        );
    }

    for changed in 0..bytes.len() {
        let mut mutated_bytes = bytes.clone();
        mutated_bytes[changed] ^= 0xff;
        let file = shipped(mutated_bytes, claims.clone());
        let error = reconcile_release_bytes(
            std::slice::from_ref(&input),
            &[file],
            &dry_run(Architecture::Amd64),
        )
        .expect_err("changed copied byte must fail");
        assert_eq!(error.code(), PackagerErrorCode::CopiedByteMismatch);
    }
}

#[test]
fn fixture_tecnica_architecture_and_elf_header_mutations_are_rejected() {
    let amd64 = fixture_elf(Architecture::Amd64);
    let relabeled = OciImageRequest {
        reference: "FIXTURE_TECNICA".to_owned(),
        architecture: Architecture::Arm64,
        entries: vec![OciRootEntry {
            entry: TarEntry::file("bin/FIXTURE_TECNICA", 0o755, amd64.clone()),
            architecture: ArchitectureBinding::Native(Architecture::Amd64),
        }],
    };
    assert_eq!(
        emit_oci_image_layout(&relabeled, &dry_run(Architecture::Arm64))
            .expect_err("architecture relabeling")
            .code(),
        PackagerErrorCode::ArchitectureMismatch
    );

    for index in [0_usize, 4, 5, 6, 18, 19] {
        let mut mutated = amd64.clone();
        mutated[index] ^= 0xff;
        let request = OciImageRequest {
            reference: "FIXTURE_TECNICA".to_owned(),
            architecture: Architecture::Amd64,
            entries: vec![OciRootEntry {
                entry: TarEntry::file("bin/FIXTURE_TECNICA", 0o755, mutated),
                architecture: ArchitectureBinding::Native(Architecture::Amd64),
            }],
        };
        assert!(
            emit_oci_image_layout(&request, &dry_run(Architecture::Amd64)).is_err(),
            "ELF mutation at {index} unexpectedly passed"
        );
    }
}

fn shipped(bytes: Vec<u8>, claims: Vec<ByteClaim>) -> ShippedFile {
    ShippedFile {
        path: "FIXTURE_TECNICA.bin".to_owned(),
        mode: 0o644,
        bytes,
        claims,
    }
}

fn dry_run(architecture: Architecture) -> EmissionPolicy {
    EmissionPolicy::new(
        EmissionMode::DryRun,
        vec![architecture],
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
