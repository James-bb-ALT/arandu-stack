use std::collections::{BTreeMap, BTreeSet};

use ha_catalog::{
    AliasProvenance, CapabilityDescriptorInput, CatalogSnapshot, CatalogSnapshotInput, Domain,
    EntityInput, EntityInputParts, EntityVisibility, ExplicitAlias, ExternalEntityId,
    RegistryEntryId, SensitiveText,
};
use nlu_core::{CapabilityId, CatalogGeneration};
#[cfg(test)]
use nlu_data::canonical_json;
#[cfg(test)]
use serde::Serialize;

use crate::{
    Result,
    error::{invalid_catalog, reconciliation_error},
    oracle::{GoldCase, GoldCatalogEntity},
};

pub(crate) fn build(case: &GoldCase) -> Result<CatalogSnapshot> {
    let generation =
        CatalogGeneration::new(1).map_err(|_| invalid_catalog("catalog generation"))?;
    let mut capability_domains = BTreeMap::<String, BTreeSet<String>>::new();
    let mut entities = case.catalog_entities.clone();
    entities.sort();

    let entity_inputs = entities
        .iter()
        .map(|entity| {
            for capability in &entity.capabilities {
                capability_domains
                    .entry(capability.clone())
                    .or_default()
                    .insert(entity.domain.clone());
            }
            entity_input(generation, entity)
        })
        .collect::<Result<Vec<_>>>()?;
    let descriptors = capability_domains
        .into_iter()
        .map(|(capability, domains)| {
            Ok(CapabilityDescriptorInput {
                id: CapabilityId::new(&capability).map_err(|_| invalid_catalog("capability ID"))?,
                enabled: true,
                state_query_domains: domains
                    .into_iter()
                    .map(|domain| {
                        Domain::new(&domain).map_err(|_| invalid_catalog("descriptor domain"))
                    })
                    .collect::<Result<Vec<_>>>()?,
            })
        })
        .collect::<Result<Vec<_>>>()?;

    CatalogSnapshot::build(CatalogSnapshotInput {
        generation,
        floors: Vec::new(),
        areas: Vec::new(),
        devices: Vec::new(),
        capability_descriptors: descriptors,
        entities: entity_inputs,
    })
    .map_err(|_| invalid_catalog("catalog snapshot"))
}

fn entity_input(generation: CatalogGeneration, entity: &GoldCatalogEntity) -> Result<EntityInput> {
    let external_id =
        ExternalEntityId::new(&entity.external_id).map_err(|_| invalid_catalog("external ID"))?;
    let domain = Domain::new(&entity.domain).map_err(|_| invalid_catalog("entity domain"))?;
    if external_id.domain() != &domain {
        return Err(invalid_catalog("external/domain mismatch"));
    }
    let registry_id =
        RegistryEntryId::new(&entity.registry_id).map_err(|_| invalid_catalog("registry ID"))?;
    let expected_core = format!("ha_entity:id_{}", entity.registry_id);
    if registry_id
        .to_core_entity_id()
        .map_err(|_| invalid_catalog("core entity ID"))?
        .as_str()
        != expected_core
    {
        return Err(reconciliation_error("core entity projection"));
    }
    let capabilities = entity
        .capabilities
        .iter()
        .map(|capability| {
            CapabilityId::new(capability).map_err(|_| invalid_catalog("entity capability"))
        })
        .collect::<Result<Vec<_>>>()?;

    Ok(EntityInput::new(EntityInputParts {
        generation,
        registry_id,
        external_id,
        domain,
        display_name: SensitiveText::new(format!("FIXTURE_TECNICA_CATALOG_{}", entity.registry_id))
            .map_err(|_| invalid_catalog("technical display"))?,
        aliases: vec![ExplicitAlias::new(
            SensitiveText::new(entity.mention.clone())
                .map_err(|_| invalid_catalog("entity alias"))?,
            AliasProvenance::EntityRegistry,
        )],
        capabilities,
        area_id: None,
        floor_id: None,
        device_id: None,
        visibility: EntityVisibility::exposed(),
    }))
}

#[cfg(test)]
#[derive(Serialize)]
struct CatalogView<'a> {
    generation: u64,
    entities: Vec<EntityView<'a>>,
}

#[cfg(test)]
#[derive(Serialize)]
struct EntityView<'a> {
    registry_id: &'a str,
    core_id: &'a str,
    external_id: &'a str,
    domain: &'a str,
    aliases: Vec<&'a str>,
    capabilities: Vec<&'a str>,
}

#[cfg(test)]
fn canonical_fingerprint(snapshot: &CatalogSnapshot) -> Result<Vec<u8>> {
    let entities = snapshot
        .entities()
        .values()
        .map(|entity| EntityView {
            registry_id: entity.registry_id().as_str(),
            core_id: entity.core_id().as_str(),
            external_id: entity.external_id().as_str(),
            domain: entity.domain().as_str(),
            aliases: entity
                .aliases()
                .iter()
                .map(|alias| alias.text().as_str())
                .collect(),
            capabilities: entity
                .capabilities()
                .iter()
                .map(CapabilityId::as_str)
                .collect(),
        })
        .collect();
    let value = serde_json::to_value(CatalogView {
        generation: snapshot.generation().get(),
        entities,
    })
    .map_err(|_| reconciliation_error("catalog fingerprint value"))?;
    canonical_json(&value, "P11 synthetic catalog")
        .map_err(|_| reconciliation_error("catalog fingerprint encoding"))
}

#[cfg(test)]
mod tests {
    use std::path::{Path, PathBuf};

    use super::*;
    use crate::{evaluator::EvaluationSplit, oracle};

    fn repository_root() -> PathBuf {
        Path::new(env!("CARGO_MANIFEST_DIR"))
            .ancestors()
            .find(|candidate| candidate.join("AGENTS.md").is_file())
            .expect("repository root")
            .to_path_buf()
    }

    #[test]
    fn catalog_projection_is_deterministic() {
        let cases = oracle::load(&repository_root(), EvaluationSplit::Train).expect("load oracle");
        let first = build(&cases[48]).expect("first catalog");
        let second = build(&cases[48]).expect("second catalog");
        assert_eq!(
            canonical_fingerprint(&first).expect("first fingerprint"),
            canonical_fingerprint(&second).expect("second fingerprint")
        );
        assert_eq!(first.entities().len(), 2);
    }
}
