use core::fmt;

use crate::{AdapterError, PositionPercent, RegistryEntryId, Result};

pub const MAX_OPERATION_TARGETS: usize = 64;

#[derive(Clone, Eq, Ord, PartialEq, PartialOrd)]
pub struct TargetSet {
    targets: Vec<RegistryEntryId>,
}

impl TargetSet {
    pub fn new(mut targets: Vec<RegistryEntryId>) -> Result<Self> {
        if targets.is_empty() {
            return Err(AdapterError::EmptyTargets);
        }
        if targets.len() > MAX_OPERATION_TARGETS {
            return Err(AdapterError::TooManyTargets);
        }
        targets.sort();
        if targets.windows(2).any(|pair| pair[0] == pair[1]) {
            return Err(AdapterError::DuplicateTarget);
        }
        Ok(Self { targets })
    }

    #[must_use]
    pub fn as_slice(&self) -> &[RegistryEntryId] {
        &self.targets
    }

    #[must_use]
    pub fn len(&self) -> usize {
        self.targets.len()
    }

    #[must_use]
    pub fn is_empty(&self) -> bool {
        false
    }
}

impl fmt::Debug for TargetSet {
    fn fmt(&self, formatter: &mut fmt::Formatter<'_>) -> fmt::Result {
        formatter
            .debug_struct("TargetSet")
            .field("target_count", &self.targets.len())
            .finish()
    }
}

#[derive(Clone, Copy, Debug, Eq, Ord, PartialEq, PartialOrd)]
pub enum OperationKind {
    ReadEntityState,
    ReadBundledSnapshot,
    TurnOn,
    TurnOff,
    SetCoverPosition,
}

impl OperationKind {
    #[must_use]
    pub const fn is_read_only(self) -> bool {
        matches!(self, Self::ReadEntityState | Self::ReadBundledSnapshot)
    }

    #[must_use]
    pub const fn is_effecting(self) -> bool {
        !self.is_read_only()
    }

    #[must_use]
    pub const fn required_capability(self) -> &'static str {
        match self {
            Self::ReadEntityState | Self::ReadBundledSnapshot => "ha:state_query",
            Self::TurnOn => "ha:light_control",
            Self::TurnOff => "ha:switch_control",
            Self::SetCoverPosition => "ha:cover_control",
        }
    }

    pub(crate) const fn transcript_tag(self) -> u8 {
        match self {
            Self::ReadEntityState => 1,
            Self::ReadBundledSnapshot => 2,
            Self::TurnOn => 3,
            Self::TurnOff => 4,
            Self::SetCoverPosition => 5,
        }
    }
}

#[derive(Clone, Eq, PartialEq)]
enum OperationData {
    ReadEntityState,
    ReadBundledSnapshot,
    TurnOn,
    TurnOff,
    SetCoverPosition(PositionPercent),
}

#[derive(Clone, Eq, PartialEq)]
pub struct TypedOperation {
    data: OperationData,
    targets: TargetSet,
}

impl TypedOperation {
    pub fn read_entity_state(target: RegistryEntryId) -> Result<Self> {
        Self::single(OperationData::ReadEntityState, target)
    }

    pub fn read_bundled_snapshot(targets: TargetSet) -> Result<Self> {
        if targets.len() < 2 {
            return Err(AdapterError::BundledSnapshotRequiresMultipleTargets);
        }
        Ok(Self {
            data: OperationData::ReadBundledSnapshot,
            targets,
        })
    }

    pub fn turn_on(target: RegistryEntryId) -> Result<Self> {
        Self::single(OperationData::TurnOn, target)
    }

    pub fn turn_off(target: RegistryEntryId) -> Result<Self> {
        Self::single(OperationData::TurnOff, target)
    }

    pub fn set_cover_position(target: RegistryEntryId, position: PositionPercent) -> Result<Self> {
        Self::single(OperationData::SetCoverPosition(position), target)
    }

    fn single(data: OperationData, target: RegistryEntryId) -> Result<Self> {
        Ok(Self {
            data,
            targets: TargetSet::new(vec![target])?,
        })
    }

    #[must_use]
    pub const fn kind(&self) -> OperationKind {
        match self.data {
            OperationData::ReadEntityState => OperationKind::ReadEntityState,
            OperationData::ReadBundledSnapshot => OperationKind::ReadBundledSnapshot,
            OperationData::TurnOn => OperationKind::TurnOn,
            OperationData::TurnOff => OperationKind::TurnOff,
            OperationData::SetCoverPosition(_) => OperationKind::SetCoverPosition,
        }
    }

    #[must_use]
    pub fn targets(&self) -> &TargetSet {
        &self.targets
    }

    #[must_use]
    pub const fn position(&self) -> Option<PositionPercent> {
        match self.data {
            OperationData::SetCoverPosition(position) => Some(position),
            _ => None,
        }
    }

    #[must_use]
    pub const fn is_read_only(&self) -> bool {
        self.kind().is_read_only()
    }

    pub(crate) fn append_transcript(&self, output: &mut Vec<u8>) {
        output.push(self.kind().transcript_tag());
        output.extend_from_slice(&(self.targets.len() as u16).to_be_bytes());
        for target in self.targets.as_slice() {
            output.extend_from_slice(target.as_str().as_bytes());
        }
        if let Some(position) = self.position() {
            output.push(position.get());
        }
    }
}

impl fmt::Debug for TypedOperation {
    fn fmt(&self, formatter: &mut fmt::Formatter<'_>) -> fmt::Result {
        formatter
            .debug_struct("TypedOperation")
            .field("kind", &self.kind())
            .field("target_count", &self.targets.len())
            .field("has_numeric_argument", &self.position().is_some())
            .finish()
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    fn target(value: char) -> RegistryEntryId {
        RegistryEntryId::new(&value.to_string().repeat(32)).expect("technical target")
    }

    #[test]
    fn target_sets_are_canonical_and_bounded() {
        let targets = TargetSet::new(vec![target('b'), target('a')]).expect("targets");
        assert_eq!(targets.as_slice()[0].as_str(), "a".repeat(32));
        assert_eq!(
            TargetSet::new(vec![target('a'), target('a')]),
            Err(AdapterError::DuplicateTarget)
        );
        assert!(
            TypedOperation::read_bundled_snapshot(
                TargetSet::new(vec![target('a')]).expect("one target")
            )
            .is_err()
        );
    }
}
