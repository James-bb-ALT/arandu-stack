use std::collections::{BTreeMap, BTreeSet};

use crate::path::validate_attestation_id;
use crate::{PackagerError, PackagerErrorCode, Result};

#[derive(Clone, Copy, Debug, Eq, Ord, PartialEq, PartialOrd)]
pub enum Architecture {
    Amd64,
    Arm64,
}

impl Architecture {
    pub const fn as_oci_str(self) -> &'static str {
        match self {
            Self::Amd64 => "amd64",
            Self::Arm64 => "arm64",
        }
    }

    pub(crate) const fn elf_machine(self) -> u16 {
        match self {
            Self::Amd64 => 62,
            Self::Arm64 => 183,
        }
    }
}

#[derive(Clone, Copy, Debug, Eq, Ord, PartialEq, PartialOrd)]
pub enum ArchitectureStage {
    Disabled,
    BuildAdmitted,
    NativeValidated,
    HaLifecycleValidated,
    Reproducible,
    Enabled,
}

impl ArchitectureStage {
    pub const fn as_str(self) -> &'static str {
        match self {
            Self::Disabled => "disabled",
            Self::BuildAdmitted => "build-admitted",
            Self::NativeValidated => "native-validated",
            Self::HaLifecycleValidated => "ha-lifecycle-validated",
            Self::Reproducible => "reproducible",
            Self::Enabled => "enabled",
        }
    }

    const fn rank(self) -> u8 {
        match self {
            Self::Disabled => 0,
            Self::BuildAdmitted => 1,
            Self::NativeValidated => 2,
            Self::HaLifecycleValidated => 3,
            Self::Reproducible => 4,
            Self::Enabled => 5,
        }
    }
}

#[derive(Clone, Debug, Eq, PartialEq)]
pub struct ArchitectureEnablement {
    pub architecture: Architecture,
    pub stage: ArchitectureStage,
    pub build_admission: Option<String>,
    pub native_validation: Option<String>,
    pub ha_lifecycle_validation: Option<String>,
    pub reproducibility: Option<String>,
    pub enablement: Option<String>,
}

impl ArchitectureEnablement {
    pub fn disabled(architecture: Architecture) -> Self {
        Self {
            architecture,
            stage: ArchitectureStage::Disabled,
            build_admission: None,
            native_validation: None,
            ha_lifecycle_validation: None,
            reproducibility: None,
            enablement: None,
        }
    }
}

#[derive(Clone, Debug, Default, Eq, PartialEq)]
pub struct ReleaseAuthorization {
    pub artifact_build_allowed: bool,
    pub inherited_p14_gate: Option<String>,
    pub source_admission: Option<String>,
    pub license_reconciliation: Option<String>,
    pub p15_release_gate: Option<String>,
    pub architectures: Vec<ArchitectureEnablement>,
}

#[derive(Clone, Copy, Debug, Eq, PartialEq)]
pub enum EmissionMode {
    DryRun,
    Production,
}

#[derive(Clone, Debug, Eq, PartialEq)]
pub struct EmissionPolicy {
    mode: EmissionMode,
    targets: Vec<Architecture>,
    authorization: ReleaseAuthorization,
}

impl EmissionPolicy {
    pub fn new(
        mode: EmissionMode,
        targets: Vec<Architecture>,
        authorization: ReleaseAuthorization,
    ) -> Self {
        Self {
            mode,
            targets,
            authorization,
        }
    }

    pub const fn mode(&self) -> EmissionMode {
        self.mode
    }

    pub fn targets(&self) -> &[Architecture] {
        &self.targets
    }

    pub fn authorization(&self) -> &ReleaseAuthorization {
        &self.authorization
    }

    pub(crate) fn validate(&self) -> Result<Vec<Architecture>> {
        let targets = validate_targets(&self.targets)?;
        let architectures = validate_authorization(&self.authorization)?;

        if self.mode == EmissionMode::Production {
            if !self.authorization.artifact_build_allowed {
                return Err(gate_denied("artifact_build_allowed is false"));
            }
            for (name, value) in [
                ("inherited P14 gate", &self.authorization.inherited_p14_gate),
                ("source admission", &self.authorization.source_admission),
                (
                    "license reconciliation",
                    &self.authorization.license_reconciliation,
                ),
                ("P15 release gate", &self.authorization.p15_release_gate),
            ] {
                if value.is_none() {
                    return Err(gate_denied(format!("{name} attestation is absent")));
                }
            }
            for target in &targets {
                let state = architectures.get(target).ok_or_else(|| {
                    gate_denied(format!(
                        "{} architecture enablement is absent",
                        target.as_oci_str()
                    ))
                })?;
                if state.stage != ArchitectureStage::Enabled {
                    return Err(gate_denied(format!(
                        "{} architecture is not enabled",
                        target.as_oci_str()
                    )));
                }
            }
        }
        Ok(targets)
    }
}

fn validate_targets(targets: &[Architecture]) -> Result<Vec<Architecture>> {
    if targets.is_empty() {
        return Err(PackagerError::new(
            PackagerErrorCode::InvalidArchitecture,
            "at least one architecture target is required",
        ));
    }
    let sorted: BTreeSet<_> = targets.iter().copied().collect();
    if sorted.len() != targets.len() {
        return Err(PackagerError::new(
            PackagerErrorCode::InvalidArchitecture,
            "duplicate architecture target",
        ));
    }
    Ok(sorted.into_iter().collect())
}

fn validate_authorization(
    authorization: &ReleaseAuthorization,
) -> Result<BTreeMap<Architecture, &ArchitectureEnablement>> {
    validate_optional_attestation(
        &authorization.inherited_p14_gate,
        "inherited P14 gate attestation",
    )?;
    validate_optional_attestation(
        &authorization.source_admission,
        "source admission attestation",
    )?;
    validate_optional_attestation(
        &authorization.license_reconciliation,
        "license reconciliation attestation",
    )?;
    validate_optional_attestation(
        &authorization.p15_release_gate,
        "P15 release gate attestation",
    )?;

    let mut architectures = BTreeMap::new();
    for state in &authorization.architectures {
        if architectures.insert(state.architecture, state).is_some() {
            return Err(PackagerError::new(
                PackagerErrorCode::ContradictoryAuthorization,
                format!(
                    "duplicate {} architecture enablement",
                    state.architecture.as_oci_str()
                ),
            ));
        }
        validate_architecture_state(state)?;
    }

    if authorization.artifact_build_allowed {
        for (name, value) in [
            ("inherited P14 gate", &authorization.inherited_p14_gate),
            ("source admission", &authorization.source_admission),
            (
                "license reconciliation",
                &authorization.license_reconciliation,
            ),
            ("P15 release gate", &authorization.p15_release_gate),
        ] {
            if value.is_none() {
                return Err(PackagerError::new(
                    PackagerErrorCode::ContradictoryAuthorization,
                    format!("artifact build is allowed without {name}"),
                ));
            }
        }
        for architecture in [Architecture::Amd64, Architecture::Arm64] {
            let state = architectures.get(&architecture).ok_or_else(|| {
                PackagerError::new(
                    PackagerErrorCode::ContradictoryAuthorization,
                    format!(
                        "artifact build is allowed without {} enablement",
                        architecture.as_oci_str()
                    ),
                )
            })?;
            if state.stage != ArchitectureStage::Enabled {
                return Err(PackagerError::new(
                    PackagerErrorCode::ContradictoryAuthorization,
                    format!(
                        "artifact build is allowed while {} is not enabled",
                        architecture.as_oci_str()
                    ),
                ));
            }
        }
    }
    Ok(architectures)
}

fn validate_architecture_state(state: &ArchitectureEnablement) -> Result<()> {
    let fields = [
        ("build admission", &state.build_admission),
        ("native validation", &state.native_validation),
        ("Home Assistant lifecycle", &state.ha_lifecycle_validation),
        ("reproducibility", &state.reproducibility),
        ("enablement", &state.enablement),
    ];
    for (index, (name, value)) in fields.iter().enumerate() {
        validate_optional_attestation(
            value,
            &format!("{} {name} attestation", state.architecture.as_oci_str()),
        )?;
        let should_exist = usize::from(state.stage.rank()) > index;
        if value.is_some() != should_exist {
            return Err(PackagerError::new(
                PackagerErrorCode::ContradictoryAuthorization,
                format!(
                    "{} stage {:?} contradicts its {name} attestation",
                    state.architecture.as_oci_str(),
                    state.stage
                ),
            ));
        }
    }
    Ok(())
}

fn validate_optional_attestation(value: &Option<String>, context: &str) -> Result<()> {
    if let Some(value) = value {
        validate_attestation_id(value, context)?;
    }
    Ok(())
}

fn gate_denied(message: impl Into<String>) -> PackagerError {
    PackagerError::new(PackagerErrorCode::ProductionGateDenied, message)
}

#[cfg(test)]
mod tests {
    use super::{
        Architecture, ArchitectureEnablement, ArchitectureStage, EmissionMode, EmissionPolicy,
        ReleaseAuthorization,
    };
    use crate::PackagerErrorCode;

    #[test]
    fn fixture_tecnica_default_authorization_is_dry_run_only() {
        let dry_run = EmissionPolicy::new(
            EmissionMode::DryRun,
            vec![Architecture::Amd64],
            ReleaseAuthorization::default(),
        );
        assert!(dry_run.validate().is_ok());

        let production = EmissionPolicy::new(
            EmissionMode::Production,
            vec![Architecture::Amd64],
            ReleaseAuthorization::default(),
        );
        assert_eq!(
            production
                .validate()
                .expect_err("production must fail")
                .code(),
            PackagerErrorCode::ProductionGateDenied
        );
    }

    #[test]
    fn fixture_tecnica_stage_cannot_skip_an_attestation() {
        let mut state = ArchitectureEnablement::disabled(Architecture::Amd64);
        state.stage = ArchitectureStage::NativeValidated;
        state.build_admission = Some("FIXTURE_TECNICA/build".to_owned());
        let policy = EmissionPolicy::new(
            EmissionMode::DryRun,
            vec![Architecture::Amd64],
            ReleaseAuthorization {
                architectures: vec![state],
                ..ReleaseAuthorization::default()
            },
        );
        assert_eq!(
            policy.validate().expect_err("state must fail").code(),
            PackagerErrorCode::ContradictoryAuthorization
        );
    }
}
