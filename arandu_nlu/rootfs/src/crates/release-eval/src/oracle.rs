use std::collections::BTreeMap;

use nlu_data::sha256_hex;
use serde_json::Value;

use crate::{
    Result,
    error::{invalid_dataset, invalid_projection, reconciliation, resource_limit},
    frozen::{find_intent, find_shape, projected_slot},
    schema::{
        CatalogEntity, EvaluationCase, EvaluationSplit, ExpectedGraph, ExpectedNode, ExpectedSlot,
        ExpectedValue, P02ExpectedSlot, P02Row, P09Projection, P11Projection, SlotProjection,
    },
    word_count,
};

const AREAS: [&str; 20] = [
    "sala",
    "cozinha",
    "quarto",
    "escritorio",
    "corredor",
    "varanda",
    "garagem",
    "lavanderia",
    "biblioteca",
    "atelie",
    "copa",
    "despensa",
    "banheiro",
    "suite",
    "jardim",
    "porao",
    "sotao",
    "oficina",
    "estudio",
    "academia",
];

#[derive(Clone, Copy)]
pub(crate) struct GeneratorContract {
    pub(crate) external: &'static str,
    pub(crate) slug: &'static str,
    pub(crate) target_noun: &'static str,
    pub(crate) domain: &'static str,
    pub(crate) slot_kind: &'static str,
    pub(crate) graph_shape: &'static str,
    heldout_template: &'static str,
    performance_template: &'static str,
}

macro_rules! generator_contract {
    (
        $external:literal,
        $slug:literal,
        $target_noun:literal,
        $domain:literal,
        $slot_kind:literal,
        $graph_shape:literal,
        $heldout_template:literal,
        $performance_template:literal
        $(,)?
    ) => {
        GeneratorContract {
            external: $external,
            slug: $slug,
            target_noun: $target_noun,
            domain: $domain,
            slot_kind: $slot_kind,
            graph_shape: $graph_shape,
            heldout_template: $heldout_template,
            performance_template: $performance_template,
        }
    };
}

const CONTRACTS: [GeneratorContract; 20] = [
    generator_contract!(
        "HassTurnOff",
        "hass_turn_off",
        "interruptor",
        "switch",
        "entity",
        "single",
        "quero %{target} desligado",
        "desligar %{target}",
    ),
    generator_contract!(
        "HassTurnOn",
        "hass_turn_on",
        "luz",
        "light",
        "entity_pair",
        "parallel_pair",
        "quero %{target} e %{target2} ligados",
        "ligar %{target} e %{target2}",
    ),
    generator_contract!(
        "HassToggle",
        "hass_toggle",
        "ventilador",
        "fan",
        "entity",
        "single",
        "inverta o estado de %{target}",
        "alternar %{target}",
    ),
    generator_contract!(
        "HassGetState",
        "hass_get_state",
        "sensor",
        "sensor",
        "entity",
        "single",
        "informe como está %{target}",
        "estado de %{target}",
    ),
    generator_contract!(
        "HassNevermind",
        "hass_nevermind",
        "pedido",
        "conversation",
        "pending_action",
        "single",
        "desconsidere %{target}",
        "ignorar %{target}",
    ),
    generator_contract!(
        "HassSetPosition",
        "hass_set_position",
        "persiana",
        "cover",
        "position",
        "single",
        "deixe %{target} na posição de %{position} por cento",
        "posição de %{target} em %{position} por cento",
    ),
    generator_contract!(
        "HassStopMoving",
        "hass_stop_moving",
        "cortina",
        "cover",
        "entity",
        "single",
        "faça %{target} parar de se mover",
        "parar %{target}",
    ),
    generator_contract!(
        "HassStartTimer",
        "hass_start_timer",
        "temporizador",
        "timer",
        "duration",
        "ordered_pair",
        "programe %{target} para %{minutes} minutos antes de informar o estado",
        "iniciar %{target} por %{minutes} minutos e obter estado",
    ),
    generator_contract!(
        "HassCancelTimer",
        "hass_cancel_timer",
        "temporizador",
        "timer",
        "timer_name",
        "single",
        "quero %{target} cancelado",
        "cancelar %{target}",
    ),
    generator_contract!(
        "HassCancelAllTimers",
        "hass_cancel_all_timers",
        "temporizador",
        "timer",
        "area",
        "single",
        "não deixe nenhum temporizador ativo no setor %{scope}",
        "cancelar temporizadores do setor %{scope}",
    ),
    generator_contract!(
        "HassIncreaseTimer",
        "hass_increase_timer",
        "temporizador",
        "timer",
        "duration_delta",
        "single",
        "faça %{target} durar mais %{minutes} minutos",
        "aumentar %{target} em %{minutes} minutos",
    ),
    generator_contract!(
        "HassDecreaseTimer",
        "hass_decrease_timer",
        "temporizador",
        "timer",
        "duration_delta",
        "single",
        "faça %{target} durar menos %{minutes} minutos",
        "reduzir %{target} em %{minutes} minutos",
    ),
    generator_contract!(
        "HassPauseTimer",
        "hass_pause_timer",
        "temporizador",
        "timer",
        "timer_name",
        "single",
        "deixe %{target} pausado",
        "pausar %{target}",
    ),
    generator_contract!(
        "HassUnpauseTimer",
        "hass_unpause_timer",
        "temporizador",
        "timer",
        "timer_name",
        "single",
        "faça %{target} voltar a contar",
        "retomar %{target}",
    ),
    generator_contract!(
        "HassTimerStatus",
        "hass_timer_status",
        "temporizador",
        "timer",
        "timer_name",
        "single",
        "informe quanto resta em %{target}",
        "estado de %{target}",
    ),
    generator_contract!(
        "HassGetCurrentDate",
        "hass_get_current_date",
        "painel",
        "date",
        "display_context",
        "single",
        "informe a data de hoje em %{target}",
        "data atual em %{target}",
    ),
    generator_contract!(
        "HassGetCurrentTime",
        "hass_get_current_time",
        "relógio",
        "time",
        "display_context",
        "single",
        "informe a hora atual em %{target}",
        "hora atual em %{target}",
    ),
    generator_contract!(
        "HassRespond",
        "hass_respond",
        "resposta",
        "conversation",
        "response_text",
        "single",
        "a resposta deve ser %{message}",
        "responder %{message}",
    ),
    generator_contract!(
        "HassBroadcast",
        "hass_broadcast",
        "alto-falante",
        "notify",
        "message",
        "single",
        "anuncie %{message} usando %{target}",
        "transmitir %{message} em %{target}",
    ),
    generator_contract!(
        "HassClimateGetTemperature",
        "hass_climate_get_temperature",
        "termômetro",
        "climate",
        "entity",
        "single",
        "informe quantos graus registra %{target}",
        "temperatura em %{target}",
    ),
];

#[derive(Clone, Debug)]
pub(crate) struct RenderedOracle {
    pub(crate) utterance: String,
    pub(crate) parameters: BTreeMap<String, String>,
}

pub(crate) fn contract(external: &str) -> Option<&'static GeneratorContract> {
    CONTRACTS
        .iter()
        .find(|candidate| candidate.external == external)
}

pub(crate) fn render(
    split: EvaluationSplit,
    contract: &GeneratorContract,
    index: u64,
) -> Result<RenderedOracle> {
    if !(1..=240).contains(&index) {
        return Err(invalid_dataset("generator index"));
    }
    let target = target_value(contract.target_noun, index, 0)?;
    let target2 = target_value(contract.target_noun, index, 120)?;
    let area_index =
        usize::try_from(index - 1).map_err(|_| resource_limit("generator area index"))?;
    let area = AREAS[area_index % AREAS.len()];
    let mut parameters = BTreeMap::new();
    parameters.insert("target".to_owned(), target);
    parameters.insert("target2".to_owned(), target2);
    parameters.insert("position".to_owned(), ((index - 1) % 101).to_string());
    parameters.insert("minutes".to_owned(), index.to_string());
    parameters.insert("scope".to_owned(), format!("{index:03}"));
    parameters.insert(
        "message".to_owned(),
        format!("confirmacao {index} do setor {area}"),
    );
    let template = match split {
        EvaluationSplit::Heldout => contract.heldout_template,
        EvaluationSplit::Performance => contract.performance_template,
    };
    let utterance = render_template(template, &parameters)?;
    Ok(RenderedOracle {
        utterance,
        parameters,
    })
}

fn target_value(noun: &str, index: u64, offset: u64) -> Result<String> {
    let adjusted = ((index - 1)
        .checked_add(offset)
        .ok_or_else(|| resource_limit("adjusted target index"))?
        % 240)
        + 1;
    let area_index = usize::try_from(adjusted - 1)
        .map_err(|_| resource_limit("target area index"))?
        % AREAS.len();
    let number = ((adjusted - 1) / AREAS.len() as u64) + 1;
    Ok(format!("{noun} {number} do setor {}", AREAS[area_index]))
}

fn render_template(template: &str, parameters: &BTreeMap<String, String>) -> Result<String> {
    let mut utterance = String::with_capacity(template.len().saturating_add(64));
    let mut cursor = 0_usize;
    while let Some(relative) = template[cursor..].find("%{") {
        let start = cursor + relative;
        utterance.push_str(&template[cursor..start]);
        let name_start = start + 2;
        let close = template[name_start..]
            .find('}')
            .map(|relative| name_start + relative)
            .ok_or_else(|| invalid_projection("generator template"))?;
        let name = &template[name_start..close];
        let parameter = parameters
            .get(name)
            .ok_or_else(|| invalid_projection("generator parameter"))?;
        utterance.push_str(parameter);
        cursor = close + 1;
    }
    utterance.push_str(&template[cursor..]);
    Ok(utterance)
}

pub(crate) fn project(
    rows: &[P02Row],
    split: EvaluationSplit,
    p09: &P09Projection,
    p11: &P11Projection,
) -> Result<Vec<EvaluationCase>> {
    rows.iter()
        .enumerate()
        .map(|(offset, row)| {
            let ordinal = u64::try_from(offset)
                .map_err(|_| resource_limit("release case ordinal"))?
                .saturating_add(1);
            project_row(row, split, p09, p11, ordinal)
        })
        .collect()
}

fn project_row(
    row: &P02Row,
    split: EvaluationSplit,
    p09: &P09Projection,
    p11: &P11Projection,
    ordinal: u64,
) -> Result<EvaluationCase> {
    let contract =
        contract(&row.expected.intent).ok_or_else(|| invalid_projection("generator contract"))?;
    let index = ((ordinal - 1) % 240) + 1;
    let rendered = render(split, contract, index)?;
    if rendered.utterance.as_bytes() != row.utterance.as_bytes()
        || row.dimensions.slot_kind != contract.slot_kind
    {
        return Err(invalid_dataset("generator projection"));
    }
    let intent_projection = find_intent(p09, &row.expected.intent)
        .ok_or_else(|| invalid_projection("intent projection"))?;
    let shape_projection = find_shape(p11, &row.dimensions.graph_shape)
        .ok_or_else(|| invalid_projection("graph projection"))?;
    if shape_projection
        .required_external_intent
        .as_deref()
        .is_some_and(|required| required != row.expected.intent)
    {
        return Err(invalid_projection("graph intent binding"));
    }

    let mut catalog = BTreeMap::<String, CatalogEntity>::new();
    let mut nodes = Vec::with_capacity(row.expected.nodes.len());
    for (node_index, source_node) in row.expected.nodes.iter().enumerate() {
        let id = shape_projection
            .node_ids
            .get(node_index)
            .ok_or_else(|| invalid_projection("projected node"))?
            .clone();
        let intent = if row.dimensions.graph_shape == "ordered_pair" && node_index == 1 {
            "ha:hass_timer_status".to_owned()
        } else {
            intent_projection.intent_id.clone()
        };
        let mut slots = Vec::with_capacity(source_node.slots.len());
        for source_slot in &source_node.slots {
            let occurrence =
                if row.dimensions.graph_shape == "parallel_pair" && source_slot.kind == "entity" {
                    Some(u16::try_from(node_index).map_err(|_| resource_limit("slot occurrence"))?)
                } else {
                    None
                };
            let projection = projected_slot(
                intent_projection,
                &source_slot.id,
                &source_slot.kind,
                occurrence,
            )?;
            let parameter = rendered
                .parameters
                .get(&projection.parameter)
                .ok_or_else(|| invalid_projection("rendered slot parameter"))?;
            let value = expected_value(
                row,
                source_node.capability.as_str(),
                source_slot,
                projection,
                parameter,
                &mut catalog,
            )?;
            slots.push(ExpectedSlot {
                id: projection.slot_id.clone(),
                value,
            });
        }
        slots.sort_by(|left, right| left.id.cmp(&right.id));
        nodes.push(ExpectedNode {
            id,
            intent,
            capability: source_node.capability.clone(),
            operation: source_node.operation.clone(),
            polarity: "affirmed".to_owned(),
            slots,
        });
    }
    nodes.sort_by(|left, right| left.id.cmp(&right.id));

    let relations = shape_projection
        .relations
        .iter()
        .map(|relation| {
            (
                relation.from.clone(),
                relation.to.clone(),
                relation.kind.clone(),
            )
        })
        .collect::<Vec<_>>();
    if relations.len() != row.expected.relations.len()
        || relations
            .iter()
            .zip(&row.expected.relations)
            .any(|((_, _, projected_kind), source)| projected_kind != &source.kind)
    {
        return Err(reconciliation("relation projection"));
    }
    let independent_pairs = shape_projection
        .independent_pairs
        .iter()
        .map(|pair| (pair.left.clone(), pair.right.clone()))
        .collect();
    let argument_shares = shape_projection
        .argument_shares
        .iter()
        .map(|share| {
            (
                share.from_node.clone(),
                share.from_slot.clone(),
                share.to_node.clone(),
                share.to_slot.clone(),
            )
        })
        .collect();

    Ok(EvaluationCase {
        ordinal,
        utterance: row.utterance.clone(),
        word_count: word_count::count(&row.utterance)?,
        dimensions: row.dimensions.clone(),
        expected: ExpectedGraph {
            catalog_generation: row.expected.catalog_generation,
            execution_class: shape_projection.execution_class.clone(),
            nodes,
            relations,
            independent_pairs,
            argument_shares,
        },
        catalog_entities: catalog.into_values().collect(),
    })
}

fn expected_value(
    row: &P02Row,
    capability: &str,
    expected: &P02ExpectedSlot,
    projection: &SlotProjection,
    parameter: &str,
    catalog: &mut BTreeMap<String, CatalogEntity>,
) -> Result<ExpectedValue> {
    match expected.kind.as_str() {
        "entity" => {
            let external_id = expected
                .value
                .get("id")
                .and_then(Value::as_str)
                .ok_or_else(|| invalid_dataset("entity source value"))?;
            let generation = expected
                .value
                .get("catalog_generation")
                .and_then(Value::as_u64)
                .ok_or_else(|| invalid_dataset("entity source generation"))?;
            let registry_id = projected_registry_id(external_id)?;
            let core_id = format!("ha_entity:id_{registry_id}");
            let entity = catalog
                .entry(external_id.to_owned())
                .or_insert_with(|| CatalogEntity {
                    registry_id: registry_id.clone(),
                    external_id: external_id.to_owned(),
                    domain: row.dimensions.domain.clone(),
                    mention: parameter.to_owned(),
                    capabilities: Vec::new(),
                });
            if entity.registry_id != registry_id
                || entity.domain != row.dimensions.domain
                || entity.mention != parameter
            {
                return Err(reconciliation("catalog projection collision"));
            }
            if !entity
                .capabilities
                .iter()
                .any(|candidate| candidate == capability)
            {
                entity.capabilities.push(capability.to_owned());
                entity.capabilities.sort();
            }
            Ok(ExpectedValue::Entity {
                id: core_id,
                generation,
            })
        }
        "integer" => {
            let value = parameter
                .parse::<i64>()
                .map_err(|_| invalid_projection("integer parameter"))?;
            let transformed = match projection.transform.as_str() {
                "integer" => value,
                "minutes_to_seconds" => value
                    .checked_mul(60)
                    .ok_or_else(|| resource_limit("integer transform"))?,
                _ => return Err(invalid_projection("integer transform")),
            };
            if expected.value.as_i64() != Some(transformed) {
                return Err(invalid_dataset("integer semantic projection"));
            }
            Ok(ExpectedValue::Integer(transformed))
        }
        "text" => {
            if projection.transform != "evidence_text" || expected.value.as_str().is_none() {
                return Err(invalid_projection("text semantic projection"));
            }
            Ok(ExpectedValue::Text(parameter.to_owned()))
        }
        _ => Err(invalid_dataset("expected slot kind")),
    }
}

fn projected_registry_id(external_id: &str) -> Result<String> {
    let mut seed = b"p11-catalog-v1\0".to_vec();
    seed.extend_from_slice(external_id.as_bytes());
    let digest = sha256_hex(&seed).map_err(|_| reconciliation("registry projection"))?;
    digest
        .get(..32)
        .map(ToOwned::to_owned)
        .ok_or_else(|| reconciliation("registry projection"))
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn mechanical_template_projection_is_exact_and_bounded() {
        let parameters =
            BTreeMap::from([("fixture".to_owned(), "FIXTURE_TECNICA_VALUE".to_owned())]);
        let rendered = render_template("FIXTURE_TECNICA %{fixture}", &parameters)
            .expect("FIXTURE_TECNICA template rendering");
        assert_eq!(rendered, "FIXTURE_TECNICA FIXTURE_TECNICA_VALUE");
        assert!(render_template("FIXTURE_TECNICA %{missing}", &parameters).is_err());
    }

    #[test]
    fn projected_registry_identifier_is_deterministic() {
        let first = projected_registry_id("FIXTURE_TECNICA_ENTITY")
            .expect("FIXTURE_TECNICA first registry projection");
        let second = projected_registry_id("FIXTURE_TECNICA_ENTITY")
            .expect("FIXTURE_TECNICA second registry projection");
        assert_eq!(first, second);
        assert_eq!(first.len(), 32);
    }
}
