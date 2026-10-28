use std::collections::BTreeSet;

use crate::{
    Action, Catalog, InterpretRequest, InterpretResponse, Operation,
    model::{MAX_OPERATIONS, MAX_TARGET_CLAUSES, MAX_TARGETS_PER_OPERATION, validate_request},
    normalize::{normalize, strip_article, strip_preposition},
};

const EFFECT_STARTERS: &[&str] = &[
    "acenda", "acende", "ajuste", "apaga", "apague", "coloque", "defina", "desliga", "desligue",
    "liga", "ligue",
];

#[derive(Debug, PartialEq)]
enum Resolution {
    Ambiguous,
    Match(Vec<String>),
    NoMatch,
}

#[derive(Clone, Copy)]
enum DomainHint {
    Fan,
    Light,
    Sensor,
    Switch,
}

impl DomainHint {
    fn matches(self, domain: &str) -> bool {
        match self {
            Self::Fan => domain == "fan",
            Self::Light => domain == "light",
            Self::Sensor => matches!(domain, "binary_sensor" | "sensor"),
            Self::Switch => domain == "switch",
        }
    }
}

#[must_use]
pub fn interpret(request: &InterpretRequest) -> InterpretResponse {
    let mut request = request.clone();
    if !validate_request(&mut request) {
        return InterpretResponse::invalid_request();
    }

    let text = normalize(&request.text);
    if text.is_empty() {
        return InterpretResponse::invalid_request();
    }

    if looks_like_query(&text) {
        if has_effect_boundary(&text) {
            return InterpretResponse::no_match();
        }
        let Some(target) = query_target(&text) else {
            return InterpretResponse::no_match();
        };
        return match resolve_target(target, Action::GetState, &request.catalog) {
            Resolution::Ambiguous => InterpretResponse::ambiguous(),
            Resolution::NoMatch => InterpretResponse::no_match(),
            Resolution::Match(targets) => InterpretResponse::plan(vec![Operation {
                action: Action::GetState,
                percentage: None,
                targets,
            }]),
        };
    }

    let Some(segments) = effect_segments(&text) else {
        return InterpretResponse::no_match();
    };
    let mut operations = Vec::with_capacity(segments.len());
    for segment in segments {
        let Some((action, percentage, target)) = effect_parts(segment) else {
            return InterpretResponse::no_match();
        };
        match resolve_target(target, action, &request.catalog) {
            Resolution::Ambiguous => return InterpretResponse::ambiguous(),
            Resolution::NoMatch => return InterpretResponse::no_match(),
            Resolution::Match(targets) => operations.push(Operation {
                action,
                percentage,
                targets,
            }),
        }
    }

    if operations.is_empty() || operations.len() > MAX_OPERATIONS {
        return InterpretResponse::no_match();
    }
    let mut affected = BTreeSet::new();
    for operation in &operations {
        if !operation.action.is_effect()
            || operation
                .targets
                .iter()
                .any(|target| !affected.insert(target))
        {
            return InterpretResponse::no_match();
        }
    }
    InterpretResponse::plan(operations)
}

fn looks_like_query(text: &str) -> bool {
    ["como ", "qual ", "quanto "]
        .iter()
        .any(|prefix| text.starts_with(prefix))
}

fn query_target(text: &str) -> Option<&str> {
    const PREFIXES: &[&str] = &[
        "qual e o estado das ",
        "qual e o estado dos ",
        "qual e o estado da ",
        "qual e o estado de ",
        "qual e o estado do ",
        "qual o estado das ",
        "qual o estado dos ",
        "qual o estado da ",
        "qual o estado de ",
        "qual o estado do ",
        "quanto esta a ",
        "quanto esta o ",
        "quanto esta ",
        "como esta a ",
        "como esta o ",
        "como esta ",
        "qual e a ",
        "qual e o ",
        "qual a ",
        "qual o ",
    ];
    PREFIXES
        .iter()
        .find_map(|prefix| text.strip_prefix(prefix))
        .filter(|target| !target.is_empty())
}

fn effect_segments(text: &str) -> Option<Vec<&str>> {
    effect_segments_spanned(text).map(|ranges| {
        ranges
            .into_iter()
            .map(|(start, end)| &text[start..end])
            .collect()
    })
}

pub(crate) fn effect_segments_spanned(text: &str) -> Option<Vec<(usize, usize)>> {
    if !starts_effect(text) {
        return None;
    }
    let mut result = Vec::new();
    let mut segment_start = 0_usize;
    for (delimiter, _) in text.match_indices(" e ") {
        let next_start = delimiter + 3;
        if starts_effect(&text[next_start..]) {
            result.push(trim_range(text, segment_start, delimiter)?);
            segment_start = next_start;
        }
    }
    result.push(trim_range(text, segment_start, text.len())?);
    (result.len() <= MAX_OPERATIONS).then_some(result)
}

fn trim_range(text: &str, start: usize, end: usize) -> Option<(usize, usize)> {
    let slice = text.get(start..end)?;
    let trimmed = slice.trim();
    if trimmed.is_empty() {
        return None;
    }
    let leading = slice.len() - slice.trim_start().len();
    Some((start + leading, start + leading + trimmed.len()))
}

fn has_effect_boundary(text: &str) -> bool {
    text.match_indices(" e ")
        .any(|(delimiter, _)| starts_effect(&text[delimiter + 3..]))
}

pub(crate) fn starts_effect(text: &str) -> bool {
    EFFECT_STARTERS.iter().any(|starter| {
        text == *starter
            || text
                .strip_prefix(starter)
                .is_some_and(|rest| rest.starts_with(' '))
    })
}

fn effect_parts(segment: &str) -> Option<(Action, Option<u8>, &str)> {
    let (verb, target) = segment.split_once(' ')?;
    let (action, needs_percentage) = verb_action(verb)?;
    if target.is_empty() {
        return None;
    }
    if !needs_percentage {
        return Some((action, None, target));
    }
    let (target, percentage) = percentage_parts(target)?;
    Some((action, Some(percentage), target))
}

/// Classify one normalized action verb; shared with v2 interpretation so
/// both paths use a single verb table.
pub(crate) fn verb_action(verb: &str) -> Option<(Action, bool)> {
    if ["acenda", "acende", "liga", "ligue"].contains(&verb) {
        Some((Action::TurnOn, false))
    } else if ["apaga", "apague", "desliga", "desligue"].contains(&verb) {
        Some((Action::TurnOff, false))
    } else if ["ajuste", "coloque", "defina"].contains(&verb) {
        Some((Action::SetFanPercentage, true))
    } else {
        None
    }
}

pub(crate) fn percentage_parts(value: &str) -> Option<(&str, u8)> {
    let (target, raw_percentage) = [" em ", " para "]
        .iter()
        .filter_map(|marker| {
            value
                .rfind(marker)
                .map(|index| (&value[..index], &value[index + marker.len()..]))
        })
        .max_by_key(|(target, _)| target.len())?;
    let digits = raw_percentage.strip_suffix(" por cento")?;
    if digits.is_empty() || !digits.bytes().all(|byte| byte.is_ascii_digit()) {
        return None;
    }
    let percentage: u8 = digits.parse().ok()?;
    if percentage > 100 {
        return None;
    }
    let target = target
        .strip_prefix("a velocidade do ")
        .or_else(|| target.strip_prefix("a velocidade da "))
        .unwrap_or(target);
    (!target.is_empty()).then_some((target, percentage))
}

/// Trailing ` em|para N por cento` marker range, mirroring
/// [`percentage_parts`] without parsing the value.
pub(crate) fn percentage_range(value: &str) -> Option<(usize, usize)> {
    [" em ", " para "]
        .iter()
        .filter_map(|marker| {
            value.rfind(marker).and_then(|index| {
                let after = &value[index + marker.len()..];
                let digits = after.strip_suffix(" por cento")?;
                if digits.is_empty() || !digits.bytes().all(|byte| byte.is_ascii_digit()) {
                    return None;
                }
                let percentage: u8 = digits.parse().ok()?;
                if percentage > 100 {
                    return None;
                }
                Some((index, value.len()))
            })
        })
        .max_by_key(|(index, _)| *index)
}

fn resolve_target(target: &str, action: Action, catalog: &Catalog) -> Resolution {
    let normalized_target = normalize(target);
    let target = strip_article(&normalized_target);
    if let Some((domain, areas, mode)) = area_expression(target) {
        match mode {
            AreaMode::Primary => match resolve_primary_areas(domain, &areas, action, catalog) {
                Resolution::NoMatch => {}
                result => return result,
            },
            AreaMode::All => match resolve_areas(domain, &areas, action, catalog) {
                Resolution::NoMatch => {}
                result => return result,
            },
        }
    }
    resolve_entity_chain(target, action, catalog)
}

enum AreaMode {
    Primary,
    All,
}

fn area_expression(target: &str) -> Option<(DomainHint, Vec<&str>, AreaMode)> {
    let target = target
        .strip_prefix("todas as ")
        .or_else(|| target.strip_prefix("todos os "))
        .unwrap_or(target);
    let (noun, rest) = target.split_once(' ')?;
    let (domain, mode) = match noun {
        "luz" | "lampada" => (DomainHint::Light, AreaMode::Primary),
        "luzes" | "lampadas" | "iluminacao" => (DomainHint::Light, AreaMode::All),
        "interruptor" | "interruptores" | "tomada" | "tomadas" => {
            (DomainHint::Switch, AreaMode::All)
        }
        "ventilador" | "ventiladores" => (DomainHint::Fan, AreaMode::All),
        "sensores" => (DomainHint::Sensor, AreaMode::All),
        _ => return None,
    };
    let clauses: Vec<_> = rest.split(" e ").collect();
    if clauses.is_empty() || clauses.len() > MAX_TARGET_CLAUSES {
        return None;
    }
    let mut areas = Vec::with_capacity(clauses.len());
    for clause in clauses {
        let area = strip_preposition(clause)?;
        if area.is_empty() {
            return None;
        }
        areas.push(area);
    }
    Some((domain, areas, mode))
}

fn resolve_primary_areas(
    domain: DomainHint,
    area_names: &[&str],
    action: Action,
    catalog: &Catalog,
) -> Resolution {
    let mut targets = BTreeSet::new();
    for area_name in area_names {
        let area_ids: BTreeSet<_> = catalog
            .areas
            .iter()
            .filter(|area| area.names.iter().any(|name| normalize(name) == *area_name))
            .map(|area| area.area_id.as_str())
            .collect();
        if area_ids.len() != 1 {
            return if area_ids.is_empty() {
                Resolution::NoMatch
            } else {
                Resolution::Ambiguous
            };
        }
        let area_id = *area_ids.first().expect("one area checked");
        let candidates: Vec<_> = catalog
            .entities
            .iter()
            .filter(|entity| {
                entity.area_id.as_deref() == Some(area_id)
                    && domain.matches(&entity.domain)
                    && entity.supports(action)
            })
            .collect();
        let uses_explicit_primaries = candidates.iter().any(|entity| entity.is_primary.is_some());
        if uses_explicit_primaries {
            let primaries: Vec<_> = candidates
                .iter()
                .filter(|entity| entity.is_primary == Some(true))
                .collect();
            if primaries.len() == 1 {
                targets.insert(primaries[0].registry_id.clone());
            } else if primaries.len() > 1 || candidates.len() > 1 {
                return Resolution::Ambiguous;
            } else {
                return Resolution::NoMatch;
            }
        } else {
            targets.extend(candidates.iter().map(|entity| entity.registry_id.clone()));
        }
    }
    Resolution::Match(targets.into_iter().collect())
}

fn resolve_areas(
    domain: DomainHint,
    area_names: &[&str],
    action: Action,
    catalog: &Catalog,
) -> Resolution {
    let mut area_ids = Vec::with_capacity(area_names.len());
    for area_name in area_names {
        let matches: BTreeSet<_> = catalog
            .areas
            .iter()
            .filter(|area| area.names.iter().any(|name| normalize(name) == *area_name))
            .map(|area| area.area_id.as_str())
            .collect();
        if matches.len() > 1 {
            return Resolution::Ambiguous;
        }
        let Some(area_id) = matches.first() else {
            return Resolution::NoMatch;
        };
        area_ids.push(*area_id);
    }

    let mut targets = BTreeSet::new();
    for area_id in area_ids {
        let matches: Vec<_> = catalog
            .entities
            .iter()
            .filter(|entity| {
                entity.area_id.as_deref() == Some(area_id)
                    && domain.matches(&entity.domain)
                    && entity.supports(action)
            })
            .map(|entity| entity.registry_id.clone())
            .collect();
        if matches.is_empty() {
            return Resolution::NoMatch;
        }
        targets.extend(matches);
    }
    if targets.is_empty() || targets.len() > MAX_TARGETS_PER_OPERATION {
        return Resolution::NoMatch;
    }
    Resolution::Match(targets.into_iter().collect())
}

fn resolve_entity_chain(target: &str, action: Action, catalog: &Catalog) -> Resolution {
    match resolve_entity_clause(target, action, catalog) {
        Resolution::Match(targets) => return Resolution::Match(targets),
        Resolution::Ambiguous => return Resolution::Ambiguous,
        Resolution::NoMatch => {}
    }

    let clauses: Vec<_> = target.split(" e ").collect();
    if clauses.len() < 2 || clauses.len() > MAX_TARGET_CLAUSES {
        return Resolution::NoMatch;
    }
    let mut targets = BTreeSet::new();
    for clause in clauses {
        match resolve_entity_clause(strip_article(clause), action, catalog) {
            Resolution::Ambiguous => return Resolution::Ambiguous,
            Resolution::NoMatch => return Resolution::NoMatch,
            Resolution::Match(matches) => {
                targets.extend(matches);
            }
        }
    }
    if targets.len() > MAX_TARGETS_PER_OPERATION {
        Resolution::NoMatch
    } else {
        Resolution::Match(targets.into_iter().collect())
    }
}

fn resolve_entity_clause(clause: &str, action: Action, catalog: &Catalog) -> Resolution {
    let matches: BTreeSet<_> = catalog
        .entities
        .iter()
        .filter(|entity| {
            entity.supports(action)
                && action.supports_domain(&entity.domain)
                && entity
                    .names
                    .iter()
                    .any(|name| strip_article(&normalize(name)) == clause)
        })
        .map(|entity| entity.registry_id.clone())
        .collect();
    match matches.len() {
        0 => Resolution::NoMatch,
        1 => Resolution::Match(matches.into_iter().collect()),
        _ => Resolution::Ambiguous,
    }
}

#[cfg(test)]
mod tests {
    use crate::{
        Action, Catalog, CatalogEntity, InterpretRequest, InterpretResponse, Operation, interpret,
    };

    use super::{Resolution, effect_segments, percentage_parts, resolve_target};

    #[test]
    fn splits_only_action_conjunctions() {
        assert_eq!(
            effect_segments("apague a luz da sala e do quarto e ligue a cafeteira"),
            Some(vec![
                "apague a luz da sala e do quarto",
                "ligue a cafeteira"
            ])
        );
    }

    #[test]
    fn percentage_is_strictly_bounded() {
        assert_eq!(
            percentage_parts("o ventilador em 100 por cento"),
            Some(("o ventilador", 100))
        );
        assert_eq!(percentage_parts("o ventilador em 101 por cento"), None);
    }

    #[test]
    fn entity_resolution_is_ambiguous_on_duplicate_alias() {
        let catalog = Catalog {
            areas: vec![],
            entities: vec![entity("one", "light.one"), entity("two", "light.two")],
            version: 1,
        };
        assert!(matches!(
            resolve_target("abajur", Action::TurnOn, &catalog),
            Resolution::Ambiguous
        ));
    }

    #[test]
    fn singular_area_uses_explicit_primary_entity() {
        let catalog = area_catalog(vec![
            entity_in_area("primary", "light.primary", "luz da sala", true),
            entity_in_area("lamp", "light.lamp", "abajur da sala", false),
        ]);
        assert_eq!(
            resolve_target("luz da sala", Action::TurnOn, &catalog),
            Resolution::Match(vec!["primary".to_owned()])
        );
    }

    #[test]
    fn plural_area_expands_all_matching_entities() {
        let catalog = area_catalog(vec![
            entity_in_area("primary", "light.primary", "luz da sala", true),
            entity_in_area("lamp", "light.lamp", "abajur da sala", false),
        ]);
        assert_eq!(
            resolve_target("luzes da sala", Action::TurnOn, &catalog),
            Resolution::Match(vec!["lamp".to_owned(), "primary".to_owned()])
        );
        assert_eq!(
            resolve_target("iluminacao da sala", Action::TurnOn, &catalog),
            Resolution::Match(vec!["lamp".to_owned(), "primary".to_owned()])
        );
        assert_eq!(
            resolve_target("todas as luzes da sala", Action::TurnOn, &catalog),
            Resolution::Match(vec!["lamp".to_owned(), "primary".to_owned()])
        );
    }

    #[test]
    fn singular_area_without_unique_primary_is_ambiguous() {
        let catalog = area_catalog(vec![
            entity_in_area("one", "light.one", "luz da sala", false),
            entity_in_area("two", "light.two", "abajur da sala", false),
        ]);
        assert!(matches!(
            resolve_target("luz da sala", Action::TurnOn, &catalog),
            Resolution::Ambiguous
        ));
    }

    #[test]
    fn singular_area_without_candidates_is_no_match() {
        let catalog = area_catalog(vec![entity_in_area(
            "fan",
            "fan.sala",
            "ventilador da sala",
            false,
        )]);
        assert!(matches!(
            resolve_target("luz da sala", Action::TurnOn, &catalog),
            Resolution::NoMatch
        ));
    }

    #[test]
    fn explicit_alias_does_not_expand_to_area() {
        let catalog = area_catalog(vec![
            entity_in_area("lamp", "light.lamp", "abajur da sala", false),
            entity_in_area("other", "light.other", "abajur do quarto", false),
        ]);
        assert_eq!(
            resolve_target("abajur da sala", Action::TurnOn, &catalog),
            Resolution::Match(vec!["lamp".to_owned()])
        );
    }

    #[test]
    fn area_queries_keep_query_action_and_reject_mixed_query_effect() {
        let catalog = area_catalog(vec![entity_in_area(
            "primary",
            "light.primary",
            "luz da sala",
            true,
        )]);
        assert_eq!(
            interpret(&InterpretRequest {
                catalog: catalog.clone(),
                text: "Qual é o estado da luz da sala?".to_owned(),
                version: 1,
            }),
            plan(Action::GetState, vec!["primary"])
        );
        assert_eq!(
            interpret(&InterpretRequest {
                catalog,
                text: "Qual é o estado da luz da sala e acenda o monitor.".to_owned(),
                version: 1,
            }),
            InterpretResponse::NoMatch { version: 1 }
        );
    }

    #[test]
    fn multiple_area_expression_preserves_deterministic_targets() {
        let catalog = Catalog {
            areas: vec![
                crate::Area {
                    area_id: "sala".to_owned(),
                    names: vec!["sala".to_owned()],
                },
                crate::Area {
                    area_id: "quarto".to_owned(),
                    names: vec!["quarto".to_owned()],
                },
            ],
            entities: vec![
                entity_in_area("sala_light", "light.sala", "luz da sala", true),
                entity_in_area_with_area(
                    "quarto_light",
                    "light.quarto",
                    "luz do quarto",
                    true,
                    "quarto",
                ),
            ],
            version: 1,
        };
        assert_eq!(
            resolve_target("luzes da sala e do quarto", Action::TurnOn, &catalog),
            Resolution::Match(vec!["quarto_light".to_owned(), "sala_light".to_owned()])
        );
    }

    #[test]
    fn incompatible_domain_and_invalid_percentage_fail_closed() {
        let mut sensor =
            entity_in_area("sensor", "sensor.temperature", "temperatura da sala", false);
        sensor.domain = "sensor".to_owned();
        let catalog = area_catalog(vec![sensor]);
        assert!(matches!(
            resolve_target("temperatura da sala", Action::TurnOn, &catalog),
            Resolution::NoMatch
        ));
        assert_eq!(percentage_parts("o ventilador em 101 por cento"), None);
        assert_eq!(
            percentage_parts("o ventilador em cinquenta por cento"),
            None
        );
    }

    #[test]
    fn capitalized_alias_articles_match_single_chain_and_query() {
        let single = catalog(vec![entity_with(
            "one",
            "light.one",
            "O Abajur",
            vec![Action::TurnOn],
        )]);
        assert_eq!(
            interpret(&InterpretRequest {
                catalog: single,
                text: "Acenda o abajur.".to_owned(),
                version: 1,
            }),
            plan(Action::TurnOn, vec!["one"])
        );

        let chain = catalog(vec![
            entity_with(
                "one",
                "light.one",
                "O Abajur da Sala",
                vec![Action::TurnOff],
            ),
            entity_with(
                "two",
                "light.two",
                "O Abajur do Quarto",
                vec![Action::TurnOff],
            ),
        ]);
        assert_eq!(
            interpret(&InterpretRequest {
                catalog: chain,
                text: "Apague o abajur da sala e o abajur do quarto.".to_owned(),
                version: 1,
            }),
            plan(Action::TurnOff, vec!["one", "two"])
        );

        let query = catalog(vec![entity_with(
            "one",
            "light.one",
            "A Luz Principal da Sala",
            vec![Action::GetState],
        )]);
        assert_eq!(
            interpret(&InterpretRequest {
                catalog: query,
                text: "Qual é o estado da luz principal da sala?".to_owned(),
                version: 1,
            }),
            plan(Action::GetState, vec!["one"])
        );
    }

    fn entity(registry_id: &str, entity_id: &str) -> CatalogEntity {
        entity_with(registry_id, entity_id, "abajur", vec![Action::TurnOn])
    }

    fn entity_with(
        registry_id: &str,
        entity_id: &str,
        name: &str,
        actions: Vec<Action>,
    ) -> CatalogEntity {
        CatalogEntity {
            actions,
            area_id: None,
            domain: "light".to_owned(),
            entity_id: entity_id.to_owned(),
            names: vec![name.to_owned()],
            registry_id: registry_id.to_owned(),
            is_primary: None,
        }
    }

    fn catalog(entities: Vec<CatalogEntity>) -> Catalog {
        Catalog {
            areas: vec![],
            entities,
            version: 1,
        }
    }

    fn area_catalog(entities: Vec<CatalogEntity>) -> Catalog {
        Catalog {
            areas: vec![crate::Area {
                area_id: "sala".to_owned(),
                names: vec!["sala".to_owned()],
            }],
            entities,
            version: 1,
        }
    }

    fn entity_in_area(
        registry_id: &str,
        entity_id: &str,
        name: &str,
        is_primary: bool,
    ) -> CatalogEntity {
        entity_in_area_with_area(registry_id, entity_id, name, is_primary, "sala")
    }

    fn entity_in_area_with_area(
        registry_id: &str,
        entity_id: &str,
        name: &str,
        is_primary: bool,
        area_id: &str,
    ) -> CatalogEntity {
        CatalogEntity {
            actions: vec![Action::TurnOn, Action::TurnOff, Action::GetState],
            area_id: Some(area_id.to_owned()),
            domain: "light".to_owned(),
            entity_id: entity_id.to_owned(),
            names: vec![name.to_owned()],
            registry_id: registry_id.to_owned(),
            is_primary: Some(is_primary),
        }
    }

    fn plan(action: Action, targets: Vec<&str>) -> InterpretResponse {
        InterpretResponse::Plan {
            operations: vec![Operation {
                action,
                percentage: None,
                targets: targets.into_iter().map(str::to_owned).collect(),
            }],
            version: 1,
        }
    }
}
