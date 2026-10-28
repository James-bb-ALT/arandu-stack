use core::fmt;
use std::collections::BTreeSet;

use lang_ptbr::NormalizedText;
use nlu_core::{CapabilityId, CatalogGeneration, EntityRef, RequestText, Utf8Span};

use crate::{
    AreaId, CatalogError, CatalogSnapshot, DeviceId, Domain, DuplicateKind, FloorId, LimitKind,
    RegistryEntryId,
};

pub const MAX_QUERY_CONSTRAINTS: usize = 16;

#[derive(Clone, Debug, Eq, Ord, PartialEq, PartialOrd)]
pub enum EntityConstraint {
    Domain {
        domain: Domain,
        evidence: Utf8Span,
    },
    Capability {
        capability: CapabilityId,
        evidence: Utf8Span,
    },
    Area {
        area_id: AreaId,
        evidence: Utf8Span,
    },
    Floor {
        floor_id: FloorId,
        evidence: Utf8Span,
    },
    Device {
        device_id: DeviceId,
        evidence: Utf8Span,
    },
}

impl EntityConstraint {
    #[must_use]
    pub const fn domain(domain: Domain, evidence: Utf8Span) -> Self {
        Self::Domain { domain, evidence }
    }

    #[must_use]
    pub const fn capability(capability: CapabilityId, evidence: Utf8Span) -> Self {
        Self::Capability {
            capability,
            evidence,
        }
    }

    #[must_use]
    pub const fn area(area_id: AreaId, evidence: Utf8Span) -> Self {
        Self::Area { area_id, evidence }
    }

    #[must_use]
    pub const fn floor(floor_id: FloorId, evidence: Utf8Span) -> Self {
        Self::Floor { floor_id, evidence }
    }

    #[must_use]
    pub const fn device(device_id: DeviceId, evidence: Utf8Span) -> Self {
        Self::Device {
            device_id,
            evidence,
        }
    }

    #[must_use]
    pub const fn evidence(&self) -> &Utf8Span {
        match self {
            Self::Domain { evidence, .. }
            | Self::Capability { evidence, .. }
            | Self::Area { evidence, .. }
            | Self::Floor { evidence, .. }
            | Self::Device { evidence, .. } => evidence,
        }
    }

    fn same_value(&self, other: &Self) -> bool {
        match (self, other) {
            (Self::Domain { domain: left, .. }, Self::Domain { domain: right, .. }) => {
                left == right
            }
            (
                Self::Capability {
                    capability: left, ..
                },
                Self::Capability {
                    capability: right, ..
                },
            ) => left == right,
            (Self::Area { area_id: left, .. }, Self::Area { area_id: right, .. }) => left == right,
            (
                Self::Floor { floor_id: left, .. },
                Self::Floor {
                    floor_id: right, ..
                },
            ) => left == right,
            (
                Self::Device {
                    device_id: left, ..
                },
                Self::Device {
                    device_id: right, ..
                },
            ) => left == right,
            _ => false,
        }
    }

    const fn singleton_kind(&self) -> Option<u8> {
        match self {
            Self::Domain { .. } => Some(0),
            Self::Capability { .. } => None,
            Self::Area { .. } => Some(1),
            Self::Floor { .. } => Some(2),
            Self::Device { .. } => Some(3),
        }
    }
}

#[derive(Clone)]
pub struct EntityQuery {
    source: RequestText,
    expected_generation: CatalogGeneration,
    mention: Utf8Span,
    mention_nfc: Box<str>,
    constraints: Box<[EntityConstraint]>,
}

impl EntityQuery {
    pub fn new(
        source: &RequestText,
        expected_generation: CatalogGeneration,
        mention: Utf8Span,
        mut constraints: Vec<EntityConstraint>,
    ) -> Result<Self, CatalogError> {
        if !mention.belongs_to(source)
            || constraints
                .iter()
                .any(|constraint| !constraint.evidence().belongs_to(source))
        {
            return Err(CatalogError::SpanSourceMismatch);
        }
        if constraints.len() > MAX_QUERY_CONSTRAINTS {
            return Err(CatalogError::LimitExceeded {
                kind: LimitKind::QueryConstraints,
                limit: MAX_QUERY_CONSTRAINTS as u32,
            });
        }

        let mention_text = mention
            .slice(source)
            .map_err(|_| CatalogError::SpanSourceMismatch)?;
        let normalized = NormalizedText::new(mention_text.to_owned())
            .map_err(|_| CatalogError::InvalidQueryText)?;

        constraints.sort();
        for pair in constraints.windows(2) {
            if pair[0].same_value(&pair[1]) {
                return Err(CatalogError::Duplicate {
                    kind: DuplicateKind::Constraint,
                });
            }
        }
        validate_singleton_constraints(&constraints)?;

        Ok(Self {
            source: source.clone(),
            expected_generation,
            mention,
            mention_nfc: normalized.as_str().into(),
            constraints: constraints.into_boxed_slice(),
        })
    }

    #[must_use]
    pub const fn source(&self) -> &RequestText {
        &self.source
    }

    #[must_use]
    pub const fn expected_generation(&self) -> CatalogGeneration {
        self.expected_generation
    }

    #[must_use]
    pub const fn mention(&self) -> &Utf8Span {
        &self.mention
    }

    #[must_use]
    pub fn constraints(&self) -> &[EntityConstraint] {
        &self.constraints
    }

    fn mention_nfc(&self) -> &str {
        &self.mention_nfc
    }
}

impl fmt::Debug for EntityQuery {
    fn fmt(&self, formatter: &mut fmt::Formatter<'_>) -> fmt::Result {
        formatter
            .debug_struct("EntityQuery")
            .field("source", &self.source)
            .field("expected_generation", &self.expected_generation)
            .field("mention", &self.mention)
            .field("normalized_mention_bytes", &self.mention_nfc.len())
            .field("constraint_count", &self.constraints.len())
            .finish()
    }
}

#[derive(Clone, Copy, Debug, Eq, Ord, PartialEq, PartialOrd)]
pub enum ResolutionRank {
    ExternalEntityId,
    ExplicitRegistryAlias,
    DisplayNameWithConstraint,
}

#[derive(Clone, Copy, Debug, Eq, Ord, PartialEq, PartialOrd)]
pub enum RankingFactorKind {
    ExternalEntityId,
    ExplicitRegistryAlias,
    DisplayName,
    DomainConstraint,
    CapabilityConstraint,
    AreaConstraint,
    FloorConstraint,
    DeviceConstraint,
}

#[derive(Clone, Debug, Eq, PartialEq)]
pub struct RankingFactor {
    kind: RankingFactorKind,
    evidence: Utf8Span,
}

impl RankingFactor {
    #[must_use]
    pub const fn kind(&self) -> RankingFactorKind {
        self.kind
    }

    #[must_use]
    pub const fn evidence(&self) -> &Utf8Span {
        &self.evidence
    }
}

#[derive(Clone, Debug, Eq, PartialEq)]
pub struct ResolutionExplanation {
    rank: ResolutionRank,
    factors: Box<[RankingFactor]>,
}

impl ResolutionExplanation {
    #[must_use]
    pub const fn rank(&self) -> ResolutionRank {
        self.rank
    }

    #[must_use]
    pub fn factors(&self) -> &[RankingFactor] {
        &self.factors
    }
}

#[derive(Clone, Debug, Eq, PartialEq)]
pub struct EntityMatch {
    registry_id: RegistryEntryId,
    entity: EntityRef,
    explanation: ResolutionExplanation,
}

impl EntityMatch {
    #[must_use]
    pub const fn registry_id(&self) -> &RegistryEntryId {
        &self.registry_id
    }

    #[must_use]
    pub const fn entity(&self) -> &EntityRef {
        &self.entity
    }

    #[must_use]
    pub const fn explanation(&self) -> &ResolutionExplanation {
        &self.explanation
    }
}

#[derive(Clone, Debug, Eq, PartialEq)]
pub struct EntityClarification {
    options: Box<[EntityMatch]>,
}

impl EntityClarification {
    #[must_use]
    pub fn options(&self) -> &[EntityMatch] {
        &self.options
    }
}

#[derive(Clone, Copy, Debug, Eq, PartialEq)]
pub enum ResolutionAbstention {
    NoMatch,
    ConstraintMismatch,
    DisplayNameRequiresIndependentConstraint,
}

#[derive(Clone, Debug, Eq, PartialEq)]
pub enum EntityResolution {
    Resolved(EntityMatch),
    Clarification(EntityClarification),
    Abstained(ResolutionAbstention),
}

pub fn resolve_entity(
    snapshot: &CatalogSnapshot,
    query: &EntityQuery,
) -> Result<EntityResolution, CatalogError> {
    if snapshot.generation() != query.expected_generation() {
        return Err(CatalogError::StaleCatalogGeneration);
    }

    if let Some(registry_id) = snapshot.external_candidate(query.mention_nfc()) {
        let candidates = BTreeSet::from([registry_id.clone()]);
        let eligible = eligible_candidates(snapshot, query, &candidates);
        if eligible.is_empty() {
            return Ok(EntityResolution::Abstained(
                ResolutionAbstention::ConstraintMismatch,
            ));
        }
        return Ok(resolution_from_candidates(
            snapshot,
            query,
            ResolutionRank::ExternalEntityId,
            RankingFactorKind::ExternalEntityId,
            eligible,
        ));
    }

    if let Some(candidates) = snapshot.alias_candidates(query.mention_nfc()) {
        let eligible = eligible_candidates(snapshot, query, candidates);
        if eligible.is_empty() {
            return Ok(EntityResolution::Abstained(
                ResolutionAbstention::ConstraintMismatch,
            ));
        }
        return Ok(resolution_from_candidates(
            snapshot,
            query,
            ResolutionRank::ExplicitRegistryAlias,
            RankingFactorKind::ExplicitRegistryAlias,
            eligible,
        ));
    }

    if let Some(candidates) = snapshot.display_candidates(query.mention_nfc()) {
        if query.constraints().is_empty() {
            return Ok(EntityResolution::Abstained(
                ResolutionAbstention::DisplayNameRequiresIndependentConstraint,
            ));
        }
        let eligible = eligible_candidates(snapshot, query, candidates);
        if !eligible.is_empty() {
            return Ok(resolution_from_candidates(
                snapshot,
                query,
                ResolutionRank::DisplayNameWithConstraint,
                RankingFactorKind::DisplayName,
                eligible,
            ));
        }
    }

    Ok(EntityResolution::Abstained(ResolutionAbstention::NoMatch))
}

fn eligible_candidates(
    snapshot: &CatalogSnapshot,
    query: &EntityQuery,
    candidates: &BTreeSet<RegistryEntryId>,
) -> Vec<RegistryEntryId> {
    candidates
        .iter()
        .filter(|registry_id| {
            snapshot
                .entity(registry_id)
                .is_some_and(|entity| constraints_match(entity, query.constraints()))
        })
        .cloned()
        .collect()
}

fn constraints_match(entity: &crate::EntityRecord, constraints: &[EntityConstraint]) -> bool {
    constraints.iter().all(|constraint| match constraint {
        EntityConstraint::Domain { domain, .. } => entity.domain() == domain,
        EntityConstraint::Capability { capability, .. } => {
            entity.capabilities().contains(capability)
        }
        EntityConstraint::Area { area_id, .. } => entity.area_id() == Some(area_id),
        EntityConstraint::Floor { floor_id, .. } => entity.floor_id() == Some(floor_id),
        EntityConstraint::Device { device_id, .. } => entity.device_id() == Some(device_id),
    })
}

fn resolution_from_candidates(
    snapshot: &CatalogSnapshot,
    query: &EntityQuery,
    rank: ResolutionRank,
    primary_factor: RankingFactorKind,
    candidates: Vec<RegistryEntryId>,
) -> EntityResolution {
    let options = candidates
        .into_iter()
        .filter_map(|registry_id| {
            let entity = snapshot.entity(&registry_id)?;
            Some(EntityMatch {
                registry_id,
                entity: entity.entity_ref(),
                explanation: explanation(query, rank, primary_factor),
            })
        })
        .collect::<Vec<_>>();

    if options.len() == 1 {
        let mut options = options.into_iter();
        if let Some(option) = options.next() {
            EntityResolution::Resolved(option)
        } else {
            EntityResolution::Abstained(ResolutionAbstention::NoMatch)
        }
    } else {
        EntityResolution::Clarification(EntityClarification {
            options: options.into_boxed_slice(),
        })
    }
}

fn explanation(
    query: &EntityQuery,
    rank: ResolutionRank,
    primary_factor: RankingFactorKind,
) -> ResolutionExplanation {
    let mut factors = Vec::with_capacity(query.constraints().len() + 1);
    factors.push(RankingFactor {
        kind: primary_factor,
        evidence: query.mention().clone(),
    });
    factors.extend(query.constraints().iter().map(|constraint| RankingFactor {
        kind: match constraint {
            EntityConstraint::Domain { .. } => RankingFactorKind::DomainConstraint,
            EntityConstraint::Capability { .. } => RankingFactorKind::CapabilityConstraint,
            EntityConstraint::Area { .. } => RankingFactorKind::AreaConstraint,
            EntityConstraint::Floor { .. } => RankingFactorKind::FloorConstraint,
            EntityConstraint::Device { .. } => RankingFactorKind::DeviceConstraint,
        },
        evidence: constraint.evidence().clone(),
    }));
    ResolutionExplanation {
        rank,
        factors: factors.into_boxed_slice(),
    }
}

fn validate_singleton_constraints(constraints: &[EntityConstraint]) -> Result<(), CatalogError> {
    let mut seen = [false; 4];
    for constraint in constraints {
        if let Some(kind) = constraint.singleton_kind() {
            let slot = &mut seen[kind as usize];
            if *slot {
                return Err(CatalogError::ContradictoryConstraint);
            }
            *slot = true;
        }
    }
    Ok(())
}

#[cfg(test)]
mod tests {
    use nlu_core::{CatalogGeneration, RequestText};

    use super::*;
    use crate::{
        AliasProvenance, CapabilityDescriptorInput, CatalogSnapshotInput, EntityInput,
        EntityInputParts, EntityVisibility, ExplicitAlias, ExternalEntityId, SensitiveText,
    };

    const FIXTURE_TECNICA_REGISTRY_A: &str = "aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa";
    const FIXTURE_TECNICA_REGISTRY_B: &str = "bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb";

    fn generation() -> CatalogGeneration {
        CatalogGeneration::new(7).expect("FIXTURE_TECNICA generation")
    }

    fn entity(
        registry: &str,
        external: &str,
        display: &str,
        aliases: &[&str],
        domain: &str,
    ) -> EntityInput {
        EntityInput::new(EntityInputParts {
            generation: generation(),
            registry_id: RegistryEntryId::new(registry).expect("FIXTURE_TECNICA registry"),
            external_id: ExternalEntityId::new(external).expect("FIXTURE_TECNICA external"),
            domain: Domain::new(domain).expect("FIXTURE_TECNICA domain"),
            display_name: SensitiveText::new(display).expect("FIXTURE_TECNICA display"),
            aliases: aliases
                .iter()
                .map(|alias| {
                    ExplicitAlias::new(
                        SensitiveText::new(*alias).expect("FIXTURE_TECNICA alias"),
                        AliasProvenance::EntityRegistry,
                    )
                })
                .collect(),
            capabilities: Vec::new(),
            area_id: None,
            floor_id: None,
            device_id: None,
            visibility: EntityVisibility::exposed(),
        })
    }

    fn snapshot(entities: Vec<EntityInput>) -> CatalogSnapshot {
        CatalogSnapshot::build(CatalogSnapshotInput {
            generation: generation(),
            floors: Vec::new(),
            areas: Vec::new(),
            devices: Vec::new(),
            capability_descriptors: Vec::new(),
            entities,
        })
        .expect("FIXTURE_TECNICA snapshot")
    }

    fn full_query(text: &str, constraints: Vec<EntityConstraint>) -> EntityQuery {
        let source = RequestText::new(text.to_owned()).expect("FIXTURE_TECNICA request");
        let mention = source
            .span(0, source.len() as u64)
            .expect("FIXTURE_TECNICA span");
        EntityQuery::new(&source, generation(), mention, constraints)
            .expect("FIXTURE_TECNICA query")
    }

    #[test]
    fn ranks_external_above_alias_and_alias_above_qualified_display() {
        let catalog = snapshot(vec![
            entity(
                FIXTURE_TECNICA_REGISTRY_A,
                "fixture_tecnica.target_a",
                "FIXTURE_TECNICA_OTHER",
                &[],
                "fixture_tecnica",
            ),
            entity(
                FIXTURE_TECNICA_REGISTRY_B,
                "fixture_tecnica.target_b",
                "fixture_tecnica.target_a",
                &["fixture_tecnica.target_a"],
                "fixture_tecnica",
            ),
        ]);
        let query = full_query("fixture_tecnica.target_a", Vec::new());
        let EntityResolution::Resolved(result) =
            resolve_entity(&catalog, &query).expect("resolution")
        else {
            panic!("expected FIXTURE_TECNICA resolution");
        };
        assert_eq!(
            result.registry_id(),
            &RegistryEntryId::new(FIXTURE_TECNICA_REGISTRY_A).expect("ID")
        );
        assert_eq!(
            result.explanation().rank(),
            ResolutionRank::ExternalEntityId
        );

        let alias_catalog = snapshot(vec![
            entity(
                FIXTURE_TECNICA_REGISTRY_A,
                "fixture_tecnica.target_a",
                "FIXTURE_TECNICA_ALIAS",
                &["FIXTURE_TECNICA_SHARED"],
                "fixture_tecnica",
            ),
            entity(
                FIXTURE_TECNICA_REGISTRY_B,
                "fixture_tecnica.target_b",
                "FIXTURE_TECNICA_SHARED",
                &[],
                "fixture_tecnica",
            ),
        ]);
        let alias_query = full_query("FIXTURE_TECNICA_SHARED", Vec::new());
        let EntityResolution::Resolved(result) =
            resolve_entity(&alias_catalog, &alias_query).expect("resolution")
        else {
            panic!("expected alias resolution");
        };
        assert_eq!(
            result.explanation().rank(),
            ResolutionRank::ExplicitRegistryAlias
        );
    }

    #[test]
    fn display_alone_abstains_and_equal_aliases_clarify_in_stable_order() {
        let first = entity(
            FIXTURE_TECNICA_REGISTRY_B,
            "fixture_tecnica.target_b",
            "FIXTURE_TECNICA_DISPLAY",
            &["FIXTURE_TECNICA_SHARED"],
            "fixture_tecnica",
        );
        let second = entity(
            FIXTURE_TECNICA_REGISTRY_A,
            "fixture_tecnica.target_a",
            "FIXTURE_TECNICA_DISPLAY",
            &["FIXTURE_TECNICA_SHARED"],
            "fixture_tecnica",
        );
        let catalog = snapshot(vec![first, second]);

        let display_query = full_query("FIXTURE_TECNICA_DISPLAY", Vec::new());
        assert_eq!(
            resolve_entity(&catalog, &display_query),
            Ok(EntityResolution::Abstained(
                ResolutionAbstention::DisplayNameRequiresIndependentConstraint
            ))
        );

        let alias_query = full_query("FIXTURE_TECNICA_SHARED", Vec::new());
        let EntityResolution::Clarification(clarification) =
            resolve_entity(&catalog, &alias_query).expect("clarification")
        else {
            panic!("expected FIXTURE_TECNICA clarification");
        };
        assert_eq!(clarification.options().len(), 2);
        assert!(
            clarification.options()[0].registry_id() < clarification.options()[1].registry_id()
        );
    }

    #[test]
    fn exact_nfc_matches_but_case_accent_compatibility_and_confusables_do_not() {
        const FIXTURE_TECNICA_PREFIX: &str = "FIXTURE_TECNICA_";
        let decomposed = format!("{FIXTURE_TECNICA_PREFIX}a\u{301}");
        let composed = format!("{FIXTURE_TECNICA_PREFIX}\u{e1}");
        let catalog = snapshot(vec![entity(
            FIXTURE_TECNICA_REGISTRY_A,
            "fixture_tecnica.target_a",
            "FIXTURE_TECNICA_OTHER",
            &[&decomposed],
            "fixture_tecnica",
        )]);
        assert!(matches!(
            resolve_entity(&catalog, &full_query(&composed, Vec::new())),
            Ok(EntityResolution::Resolved(_))
        ));

        for negative in [
            "fixture_tecnica_\u{e1}",
            "FIXTURE_TECNICA_a",
            "FIXTURE_TECNICA_\u{ff41}",
            "FIXTURE_TECNICA_\u{430}",
        ] {
            assert_eq!(
                resolve_entity(&catalog, &full_query(negative, Vec::new())),
                Ok(EntityResolution::Abstained(ResolutionAbstention::NoMatch))
            );
        }
    }

    #[test]
    fn every_constraint_is_mandatory_and_every_factor_keeps_its_source_span() {
        let capability =
            CapabilityId::new("fixture_tecnica:state_a").expect("FIXTURE_TECNICA capability");
        let mut parts = entity(
            FIXTURE_TECNICA_REGISTRY_A,
            "fixture_tecnica.target_a",
            "FIXTURE_TECNICA_DISPLAY",
            &[],
            "fixture_tecnica",
        )
        .into_parts();
        parts.capabilities.push(capability.clone());
        let catalog = CatalogSnapshot::build(CatalogSnapshotInput {
            generation: generation(),
            floors: Vec::new(),
            areas: Vec::new(),
            devices: Vec::new(),
            capability_descriptors: vec![CapabilityDescriptorInput {
                id: capability.clone(),
                enabled: true,
                state_query_domains: vec![Domain::new("fixture_tecnica").expect("domain")],
            }],
            entities: vec![EntityInput::new(parts)],
        })
        .expect("snapshot");

        let source =
            RequestText::new("FIXTURE_TECNICA_DISPLAY DOMAIN CAP".into()).expect("request");
        let mention = source.span(0, 23).expect("mention");
        let domain_span = source.span(24, 30).expect("domain evidence");
        let capability_span = source.span(31, 34).expect("capability evidence");
        let query = EntityQuery::new(
            &source,
            generation(),
            mention,
            vec![
                EntityConstraint::domain(
                    Domain::new("fixture_tecnica").expect("domain"),
                    domain_span,
                ),
                EntityConstraint::capability(capability, capability_span),
            ],
        )
        .expect("query");
        let EntityResolution::Resolved(result) =
            resolve_entity(&catalog, &query).expect("resolution")
        else {
            panic!("expected qualified display resolution");
        };
        assert_eq!(result.explanation().factors().len(), 3);
        assert!(
            result
                .explanation()
                .factors()
                .iter()
                .all(|factor| factor.evidence().belongs_to(&source))
        );

        let contradictory_source =
            RequestText::new("FIXTURE_TECNICA_DISPLAY D1 D2".into()).expect("request");
        let error = EntityQuery::new(
            &contradictory_source,
            generation(),
            contradictory_source.span(0, 23).expect("mention"),
            vec![
                EntityConstraint::domain(
                    Domain::new("fixture_tecnica").expect("domain"),
                    contradictory_source.span(24, 26).expect("evidence"),
                ),
                EntityConstraint::domain(
                    Domain::new("fixture_tecnica_other").expect("other domain"),
                    contradictory_source.span(27, 29).expect("evidence"),
                ),
            ],
        )
        .expect_err("contradictory domain constraints");
        assert_eq!(error, CatalogError::ContradictoryConstraint);
    }

    #[test]
    fn foreign_spans_and_stale_generations_fail_closed() {
        let catalog = snapshot(vec![entity(
            FIXTURE_TECNICA_REGISTRY_A,
            "fixture_tecnica.target_a",
            "FIXTURE_TECNICA_DISPLAY",
            &[],
            "fixture_tecnica",
        )]);
        let source = RequestText::new("FIXTURE_TECNICA_DISPLAY".into()).expect("source");
        let foreign = RequestText::new("FIXTURE_TECNICA_DISPLAY".into()).expect("foreign");
        assert_eq!(
            EntityQuery::new(
                &source,
                generation(),
                foreign.span(0, foreign.len() as u64).expect("foreign span"),
                Vec::new()
            )
            .expect_err("foreign mention"),
            CatalogError::SpanSourceMismatch
        );

        let stale = EntityQuery::new(
            &source,
            CatalogGeneration::new(8).expect("stale generation"),
            source.span(0, source.len() as u64).expect("span"),
            Vec::new(),
        )
        .expect("query");
        assert_eq!(
            resolve_entity(&catalog, &stale),
            Err(CatalogError::StaleCatalogGeneration)
        );
    }
}
