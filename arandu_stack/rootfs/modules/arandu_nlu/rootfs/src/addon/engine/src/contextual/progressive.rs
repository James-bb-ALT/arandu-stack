//! Additive v5 progressive interpretation (`PartialUnderstanding`) and deterministic `review_proposal`.
use super::{
    catalogs,
    contract::{
        ACTIONS, Catalog, ClauseConstraints, Operation, OriginContext, Parameters, PartialClause,
        PartialRequest, PartialUnderstanding, Response, ReviewProposalRequest,
        ReviewProposalResponse, SlotState,
    },
    grammar, interpret_index, resolver, slots,
};
use serde_json::Value;
use std::{
    collections::{BTreeMap, BTreeSet},
    time::Instant,
};

fn slot(
    value: Value,
    status: &str,
    evidence: Vec<String>,
    source: Option<&str>,
    candidates: Vec<Value>,
) -> SlotState {
    SlotState {
        value,
        status: status.to_owned(),
        evidence,
        source: source.map(str::to_owned),
        candidates,
    }
}

fn contains_phrase(haystack: &str, needle: &str) -> bool {
    if needle.is_empty() {
        return false;
    }
    format!(" {haystack} ").contains(&format!(" {needle} "))
}

fn extract_excluded_targets(clause: &str, catalog: &Catalog) -> (String, Vec<String>) {
    for marker in [
        " com excecao de ",
        " com excecao do ",
        " com excecao da ",
        " com excecao ",
        " menos ",
        " exceto ",
        " tirando ",
        " sem ",
    ] {
        if let Some((head, tail)) = clause.split_once(marker) {
            let tail_norm = slots::normalize(tail);
            let mut excluded = Vec::new();
            for entity in &catalog.entities {
                let matched = std::iter::once(&entity.name)
                    .chain(entity.aliases.iter())
                    .map(|n| slots::normalize(n))
                    .any(|n| !n.is_empty() && contains_phrase(&tail_norm, &n));
                if matched {
                    excluded.push(entity.registry_id.clone());
                }
            }
            excluded.sort();
            excluded.dedup();
            return (head.trim().to_owned(), excluded);
        }
    }
    (clause.to_owned(), Vec::new())
}

fn extract_area(clause: &str, catalog: &Catalog) -> Option<(String, String)> {
    let mut best: Option<(usize, String, String)> = None;
    for area in &catalog.areas {
        for name in &area.names {
            let norm = slots::normalize(name);
            if !norm.is_empty() && contains_phrase(clause, &norm) {
                let score = norm.len();
                if best.as_ref().is_none_or(|(b, _, _)| score > *b) {
                    best = Some((score, area.area_id.clone(), norm));
                }
            }
        }
    }
    best.map(|(_, id, norm)| (id, norm))
}

fn has_unknown_spatial_qualifier(clause: &str, catalog: &Catalog) -> bool {
    let words: Vec<&str> = clause.split_whitespace().collect();
    if words.len() < 2 {
        return false;
    }
    let area_tokens: BTreeSet<String> = catalog
        .areas
        .iter()
        .flat_map(|a| a.names.iter())
        .flat_map(|n| slots::normalize(n).split_whitespace().map(str::to_owned).collect::<Vec<_>>())
        .collect();
    let entity_tokens: BTreeSet<String> = catalog
        .entities
        .iter()
        .flat_map(|e| std::iter::once(&e.name).chain(e.aliases.iter()))
        .flat_map(|n| slots::normalize(n).split_whitespace().map(str::to_owned).collect::<Vec<_>>())
        .collect();
    words.windows(2).any(|pair| {
        matches!(pair[0], "no" | "na" | "nos" | "nas" | "do" | "da" | "dos" | "das" | "em")
            && pair[1].len() >= 3
            && !matches!(
                pair[1],
                "aqui" | "comodo" | "ambiente" | "mesmo" | "meu" | "minha" | "seu" | "sua"
            )
            && !area_tokens.contains(pair[1])
            && !entity_tokens.contains(pair[1])
    })
}

fn extract_target_candidates(
    clause: &str,
    area_id: Option<&str>,
    excluded: &[String],
    catalog: &Catalog,
) -> (Vec<String>, Vec<String>, Option<&'static str>, Vec<String>) {
    let mut exact_matches: Vec<(usize, String, String)> = Vec::new();
    for entity in &catalog.entities {
        if excluded.contains(&entity.registry_id) {
            continue;
        }
        if let Some(area) = area_id
            && entity.area_id.as_deref() != Some(area)
        {
            continue;
        }
        for alias in std::iter::once(&entity.name).chain(entity.aliases.iter()) {
            let norm = slots::normalize(alias);
            if !norm.is_empty() && contains_phrase(clause, &norm) {
                exact_matches.push((norm.len(), entity.registry_id.clone(), norm));
                break;
            }
        }
    }
    if !exact_matches.is_empty() {
        let max_len = exact_matches.iter().map(|(l, _, _)| *l).max().unwrap_or(0);
        let mut ids: Vec<String> = exact_matches
            .iter()
            .filter(|(l, _, _)| *l == max_len)
            .map(|(_, id, _)| id.clone())
            .collect();
        ids.sort();
        ids.dedup();
        let evidence = exact_matches
            .iter()
            .filter(|(l, _, _)| *l == max_len)
            .map(|(_, _, n)| format!("authorized_exact_alias:{n}"))
            .collect();
        let actions = allowed_actions_for_targets(&ids, catalog);
        return (ids, actions, Some("catalog_alias"), evidence);
    }

    // Compositional / distinctive token matching against catalog names/aliases
    let area_tokens: BTreeSet<String> = catalog
        .areas
        .iter()
        .flat_map(|a| a.names.iter())
        .flat_map(|n| slots::normalize(n).split_whitespace().map(str::to_owned).collect::<Vec<_>>())
        .collect();
    let query_tokens: BTreeSet<String> = slots::normalize(clause)
        .split_whitespace()
        .filter(|w| w.len() >= 4 && !area_tokens.contains(*w))
        .filter(|w| {
            !matches!(
                *w,
                "liga" | "ligue" | "ligar" | "desliga" | "desligue" | "desligar"
                    | "acende" | "acenda" | "acender" | "apaga" | "apague" | "apagar"
                    | "abre" | "abra" | "abrir" | "fecha" | "feche" | "fechar"
                    | "ativa" | "ative" | "ativar" | "desativa" | "desative" | "desativar"
                    | "coloca" | "coloque" | "deixa" | "deixe" | "ajusta" | "ajuste"
                    | "aumenta" | "diminui" | "todas" | "todos" | "tudo" | "pode" | "quero"
            )
        })
        .map(str::to_owned)
        .collect();
    if !query_tokens.is_empty() {
        let mut token_hits: Vec<(usize, String, Vec<String>)> = Vec::new();
        for entity in &catalog.entities {
            if excluded.contains(&entity.registry_id) {
                continue;
            }
            if let Some(area) = area_id
                && entity.area_id.as_deref() != Some(area)
            {
                continue;
            }
            let entity_tokens: BTreeSet<String> = std::iter::once(&entity.name)
                .chain(entity.aliases.iter())
                .flat_map(|n| slots::normalize(n).split_whitespace().map(str::to_owned).collect::<Vec<_>>())
                .filter(|w| w.len() >= 4 && !area_tokens.contains(w))
                .collect();
            let shared: Vec<String> = query_tokens.intersection(&entity_tokens).cloned().collect();
            if !shared.is_empty() {
                token_hits.push((shared.len(), entity.registry_id.clone(), shared));
            }
        }
        if !token_hits.is_empty() {
            let max_hits = token_hits.iter().map(|(c, _, _)| *c).max().unwrap_or(0);
            let mut ids: Vec<String> = token_hits
                .iter()
                .filter(|(c, _, _)| *c == max_hits)
                .map(|(_, id, _)| id.clone())
                .collect();
            ids.sort();
            ids.dedup();
            let mut ev = Vec::new();
            for (c, _, toks) in &token_hits {
                if *c == max_hits {
                    for t in toks {
                        ev.push(format!("catalog_token_match:{t}"));
                    }
                }
            }
            ev.sort();
            ev.dedup();
            let actions = allowed_actions_for_targets(&ids, catalog);
            return (ids, actions, Some("catalog_alias"), ev);
        }
    }

    if let Some(domain) = resolver::category_domain(clause) {
        let mut ids: Vec<String> = catalog
            .entities
            .iter()
            .filter(|e| {
                !excluded.contains(&e.registry_id)
                    && e.domain == domain
                    && area_id.is_none_or(|a| e.area_id.as_deref() == Some(a))
            })
            .map(|e| e.registry_id.clone())
            .collect();
        ids.sort();
        ids.dedup();
        if !ids.is_empty() {
            let actions = allowed_actions_for_targets(&ids, catalog);
            return (
                ids,
                actions,
                Some("catalog_alias"),
                vec![format!("category_domain:{domain}")],
            );
        }
    }

    (Vec::new(), Vec::new(), None, Vec::new())
}

fn allowed_actions_for_targets(targets: &[String], catalog: &Catalog) -> Vec<String> {
    let mut set = BTreeSet::new();
    for entity in &catalog.entities {
        if targets.contains(&entity.registry_id) {
            for action in &entity.actions {
                if ACTIONS.contains(&action.as_str()) {
                    set.insert(action.clone());
                }
            }
        }
    }
    set.into_iter().collect()
}

#[allow(clippy::too_many_lines)]
fn build_clause_from_partial_text(
    clause_id: usize,
    raw_clause: &str,
    catalog: &Catalog,
    trusted_origin_area: Option<&str>,
) -> (PartialClause, bool) {
    let (head_clause, excluded_targets) = extract_excluded_targets(raw_clause, catalog);
    let area_match = extract_area(&head_clause, catalog);
    let unknown_spatial = area_match.is_none() && has_unknown_spatial_qualifier(&head_clause, catalog);
    let context_origin = if unknown_spatial {
        None
    } else {
        trusted_origin_area
    };
    let area_id = area_match
        .as_ref()
        .map(|(id, _)| id.clone())
        .or_else(|| context_origin.map(str::to_owned));

    let (target_ids, allowed_actions, target_source, target_evidence) =
        extract_target_candidates(&head_clause, area_id.as_deref(), &excluded_targets, catalog);

    let recognized_cmd = grammar::recognize(&head_clause, None, None);
    let mut slots_map = BTreeMap::new();
    let mut unresolved = Vec::new();
    let mut has_signal = false;

    if let Some(ref cmd) = recognized_cmd {
        has_signal = true;
        slots_map.insert(
            "action".into(),
            slot(
                Value::String(cmd.action.clone()),
                "resolved",
                vec![format!("nlu_intent:{}", cmd.intent)],
                Some("nlu_rule"),
                Vec::new(),
            ),
        );
    } else {
        unresolved.push("action".into());
        slots_map.insert(
            "action".into(),
            slot(Value::Null, "unknown", Vec::new(), None, Vec::new()),
        );
    }

    match target_ids.len().cmp(&1) {
        std::cmp::Ordering::Equal => {
            has_signal = true;
            slots_map.insert(
                "target".into(),
                slot(
                    Value::String(target_ids[0].clone()),
                    "resolved",
                    target_evidence,
                    target_source,
                    Vec::new(),
                ),
            );
        }
        std::cmp::Ordering::Greater => {
            has_signal = true;
            unresolved.push("target".into());
            let candidates = target_ids.iter().cloned().map(Value::String).collect();
            slots_map.insert(
                "target".into(),
                slot(
                    Value::Null,
                    "conflicting",
                    target_evidence,
                    target_source,
                    candidates,
                ),
            );
        }
        std::cmp::Ordering::Less => {
            unresolved.push("target".into());
            slots_map.insert(
                "target".into(),
                slot(Value::Null, "unknown", Vec::new(), None, Vec::new()),
            );
        }
    }

    if let Some((aid, aname)) = area_match {
        has_signal = true;
        slots_map.insert(
            "area".into(),
            slot(
                Value::String(aid),
                "resolved",
                vec![format!("catalog_area:{aname}")],
                Some("catalog_exact"),
                Vec::new(),
            ),
        );
    } else if target_ids.len() == 1
        && let Some(entity_area) = catalog
            .entities
            .iter()
            .find(|e| e.registry_id == target_ids[0])
            .and_then(|e| e.area_id.clone())
    {
        slots_map.insert(
            "area".into(),
            slot(
                Value::String(entity_area),
                "resolved",
                vec!["entity_area".into()],
                Some("catalog_exact"),
                Vec::new(),
            ),
        );
    } else if let Some(origin_aid) = context_origin {
        slots_map.insert(
            "area".into(),
            slot(
                Value::String(origin_aid.to_owned()),
                "resolved",
                vec![format!("origin_area:{origin_aid}")],
                Some("contextual_reference"),
                Vec::new(),
            ),
        );
    } else {
        slots_map.insert(
            "area".into(),
            slot(Value::Null, "unknown", Vec::new(), None, Vec::new()),
        );
    }

    let mut params = Parameters::default();
    let mut param_ev = vec!["no_parameter_requested".to_owned()];
    if let Some(num) = slots::numeric_parameter(&head_clause) {
        has_signal = true;
        params.value = Some(serde_json::json!(num));
        param_ev = vec!["numeric_parameter".into()];
    }
    if !excluded_targets.is_empty() {
        has_signal = true;
        params.excluded_targets.clone_from(&excluded_targets);
        param_ev.push("excluded_targets".into());
    }
    let param_val = serde_json::to_value(&params).unwrap_or_else(|_| serde_json::json!({}));
    slots_map.insert(
        "parameters".into(),
        slot(
            param_val,
            "resolved",
            param_ev,
            Some("nlu_rule"),
            Vec::new(),
        ),
    );

    let clause = PartialClause {
        clause_id,
        original_text: raw_clause.to_owned(),
        slots: slots_map,
        allowed_actions,
        constraints: ClauseConstraints {
            negated: false,
            excluded_targets,
            conditional: false,
            quoted_content: false,
        },
        unresolved,
    };
    (clause, has_signal)
}

fn origin_from_request(request: &PartialRequest) -> OriginContext {
    OriginContext {
        area_id: request.origin_area.clone(),
        source: request
            .origin_area_source
            .clone()
            .unwrap_or_else(|| "unknown".into()),
        context_id: request.origin_context_id.clone(),
    }
}

/// Interpret a request into an additive v5 `PartialUnderstanding` without executing HA services.
#[must_use]
#[allow(clippy::too_many_lines)]
pub fn interpret_partial(request: &PartialRequest) -> PartialUnderstanding {
    let start = Instant::now();
    let origin = origin_from_request(request);
    if !request.valid() {
        return PartialUnderstanding {
            version: 5,
            request_id: request.request_id.clone(),
            catalog_generation: request.generation.clone(),
            identity_scope_hash: request.identity_scope_hash.clone(),
            origin,
            request_kind: "unknown".into(),
            clauses: Vec::new(),
            safety_flags: vec!["invalid_request".into()],
            literal_segments: Vec::new(),
            criticality: "normal".into(),
            freshness: "invalid".into(),
            v4_outcome: Some(Response::status("invalid_request")),
            timings: BTreeMap::new(),
        };
    }

    let index = catalogs()
        .lock()
        .ok()
        .and_then(|cache| cache.get(&request.generation).cloned());
    let Some(index) = index else {
        return PartialUnderstanding {
            version: 5,
            request_id: request.request_id.clone(),
            catalog_generation: request.generation.clone(),
            identity_scope_hash: request.identity_scope_hash.clone(),
            origin,
            request_kind: "unknown".into(),
            clauses: Vec::new(),
            safety_flags: Vec::new(),
            literal_segments: Vec::new(),
            criticality: "normal".into(),
            freshness: "stale".into(),
            v4_outcome: Some(Response::status("stale")),
            timings: BTreeMap::new(),
        };
    };

    let norm_text = slots::normalize(&request.text);
    let v4_req = request.to_v4();
    let v4_outcome = interpret_index(&norm_text, &v4_req, &index);

    let trusted_origin_area = request
        .origin_area
        .as_deref()
        .filter(|_| matches!(request.origin_area_source.as_deref(), Some("satellite" | "device")));
    let mut timings = v4_outcome.timings.clone();
    let mut safety_flags = Vec::new();
    let mut clauses = Vec::new();
    let request_kind: String;

    if v4_outcome.status == "plan" {
        request_kind = "resolved_plan".into();
        for (idx, op) in v4_outcome.operations.iter().enumerate() {
            let mut slots_map = BTreeMap::new();
            slots_map.insert(
                "action".into(),
                slot(
                    Value::String(op.action.clone()),
                    "resolved",
                    op.evidence.clone(),
                    Some("nlu_rule"),
                    Vec::new(),
                ),
            );
            let target_val = if op.targets.len() == 1 {
                Value::String(op.targets[0].clone())
            } else {
                Value::Array(op.targets.iter().cloned().map(Value::String).collect())
            };
            slots_map.insert(
                "target".into(),
                slot(
                    target_val,
                    "resolved",
                    op.evidence.clone(),
                    Some("catalog_exact"),
                    Vec::new(),
                ),
            );
            slots_map.insert(
                "area".into(),
                slot(
                    op.parameters
                        .area
                        .clone()
                        .map_or(Value::Null, Value::String),
                    "resolved",
                    vec!["nlu_plan".into()],
                    Some("nlu_rule"),
                    Vec::new(),
                ),
            );
            slots_map.insert(
                "parameters".into(),
                slot(
                    serde_json::to_value(&op.parameters).unwrap_or_else(|_| serde_json::json!({})),
                    "resolved",
                    vec!["nlu_plan".into()],
                    Some("nlu_rule"),
                    Vec::new(),
                ),
            );
            clauses.push(PartialClause {
                clause_id: idx,
                original_text: request.text.clone(),
                slots: slots_map,
                allowed_actions: allowed_actions_for_targets(&op.targets, &index.catalog),
                constraints: ClauseConstraints {
                    negated: false,
                    excluded_targets: op.parameters.excluded_targets.clone(),
                    conditional: false,
                    quoted_content: false,
                },
                unresolved: Vec::new(),
            });
        }
    } else if v4_outcome.status == "cancel"
        || v4_outcome
            .reason
            .as_deref()
            .is_some_and(|r| r.starts_with("semantic_") && r != "semantic_exclusion")
    {
        request_kind = "safety_refusal".into();
        let reason = v4_outcome
            .reason
            .clone()
            .unwrap_or_else(|| "semantic_negation".into());
        safety_flags.push(reason.clone());
        let mut slots_map = BTreeMap::new();
        slots_map.insert(
            "action".into(),
            slot(
                Value::Null,
                "forbidden",
                vec![reason.clone()],
                Some("nlu_rule"),
                Vec::new(),
            ),
        );
        clauses.push(PartialClause {
            clause_id: 0,
            original_text: request.text.clone(),
            slots: slots_map,
            allowed_actions: Vec::new(),
            constraints: ClauseConstraints {
                negated: reason == "semantic_negation" || v4_outcome.status == "cancel",
                excluded_targets: Vec::new(),
                conditional: reason == "semantic_condition",
                quoted_content: reason == "semantic_quoted_command",
            },
            unresolved: Vec::new(),
        });
    } else if v4_outcome.status == "clarification" {
        request_kind = "clarification_needed".into();
        let raw_clauses = grammar::split_clauses(&norm_text);
        let first_clause = raw_clauses
            .first()
            .map_or(norm_text.as_str(), String::as_str);
        let (mut partial_clause, _) = build_clause_from_partial_text(
            0,
            first_clause,
            &index.catalog,
            trusted_origin_area,
        );
        if !v4_outcome.candidates.is_empty() {
            partial_clause.allowed_actions =
                allowed_actions_for_targets(&v4_outcome.candidates, &index.catalog);
            partial_clause.slots.insert(
                "target".into(),
                slot(
                    Value::Null,
                    "conflicting",
                    vec![
                        v4_outcome
                            .reason
                            .clone()
                            .unwrap_or_else(|| "ambiguous_target".into()),
                    ],
                    Some("catalog_alias"),
                    v4_outcome
                        .candidates
                        .iter()
                        .cloned()
                        .map(Value::String)
                        .collect(),
                ),
            );
            if !partial_clause.unresolved.iter().any(|s| s == "target") {
                partial_clause.unresolved.push("target".into());
            }
        }
        clauses.push(partial_clause);
    } else {
        let raw_clauses = grammar::split_clauses(&norm_text);
        let mut any_signal = false;
        for (idx, raw) in raw_clauses.iter().enumerate() {
            let (clause, sig) =
                build_clause_from_partial_text(idx, raw, &index.catalog, trusted_origin_area);
            any_signal |= sig;
            clauses.push(clause);
        }
        request_kind = if any_signal {
            "possible_device_action".into()
        } else {
            "unknown".into()
        };
    }

    timings.insert(
        "partial_total_ms".into(),
        start.elapsed().as_secs_f64() * 1_000.0,
    );
    PartialUnderstanding {
        version: 5,
        request_id: request.request_id.clone(),
        catalog_generation: request.generation.clone(),
        identity_scope_hash: request.identity_scope_hash.clone(),
        origin,
        request_kind,
        clauses,
        safety_flags,
        literal_segments: Vec::new(),
        criticality: "normal".into(),
        freshness: "current".into(),
        v4_outcome: Some(v4_outcome),
        timings,
    }
}

fn reject_review(reason: &str, start: Instant) -> ReviewProposalResponse {
    let mut timings = BTreeMap::new();
    timings.insert("review_ms".into(), start.elapsed().as_secs_f64() * 1_000.0);
    ReviewProposalResponse {
        version: 5,
        accepted: false,
        status: "rejected".into(),
        reason: Some(reason.to_owned()),
        operations: Vec::new(),
        requires_confirmation: false,
        timings,
    }
}

/// Deterministically validate a Decision/LLM repair proposal against frozen NLU slots and catalog capabilities.
#[must_use]
#[allow(clippy::too_many_lines)]
pub fn review_proposal(request: &ReviewProposalRequest) -> ReviewProposalResponse {
    let start = Instant::now();
    if request.version != 5 || request.partial.version != 5 {
        return reject_review("invalid_version", start);
    }
    if request.repairs.is_empty() {
        return reject_review("empty_repairs", start);
    }
    if !request.partial.safety_flags.is_empty() {
        return reject_review("safety_locked", start);
    }
    let index = catalogs()
        .lock()
        .ok()
        .and_then(|cache| cache.get(&request.partial.catalog_generation).cloned());
    let Some(index) = index else {
        return ReviewProposalResponse {
            version: 5,
            accepted: false,
            status: "stale".into(),
            reason: Some("stale_catalog".into()),
            operations: Vec::new(),
            requires_confirmation: false,
            timings: BTreeMap::new(),
        };
    };

    let mut clauses = request.partial.clauses.clone();
    let mut requires_confirmation = false;

    for repair in &request.repairs {
        if !matches!(repair.source.as_str(), "decision_ranked" | "llm_proposed") {
            return reject_review("invalid_repair_source", start);
        }
        let Some(clause) = clauses.iter_mut().find(|c| c.clause_id == repair.clause_id) else {
            return reject_review("invalid_clause_id", start);
        };
        if clause.constraints.negated
            || clause.constraints.conditional
            || clause.constraints.quoted_content
        {
            return reject_review("constraint_locked", start);
        }
        if !matches!(
            repair.slot.as_str(),
            "action" | "target" | "area" | "parameters"
        ) {
            return reject_review("invalid_slot", start);
        }
        let Some(existing) = clause.slots.get(&repair.slot) else {
            return reject_review("missing_slot", start);
        };
        if matches!(existing.status.as_str(), "resolved" | "forbidden")
            || (existing.status == "conflicting" && repair.source != "llm_proposed")
        {
            return reject_review("immutable_slot", start);
        }

        match repair.slot.as_str() {
            "action" => {
                let Some(action) = repair.value.as_str() else {
                    return reject_review("invalid_action", start);
                };
                if !ACTIONS.contains(&action) {
                    return reject_review("unknown_action", start);
                }
                if !clause.allowed_actions.is_empty()
                    && !clause.allowed_actions.iter().any(|a| a == action)
                {
                    return reject_review("action_not_allowed_for_target", start);
                }
                requires_confirmation = true;
            }
            "target" => {
                let targets: Vec<String> = if let Some(single) = repair.value.as_str() {
                    vec![single.to_owned()]
                } else if let Some(arr) = repair.value.as_array() {
                    arr.iter()
                        .filter_map(|v| v.as_str().map(str::to_owned))
                        .collect()
                } else {
                    Vec::new()
                };
                if targets.is_empty() || targets.len() > 32 {
                    return reject_review("invalid_target", start);
                }
                if targets
                    .iter()
                    .any(|t| clause.constraints.excluded_targets.contains(t))
                {
                    return reject_review("excluded_target_violation", start);
                }
                if repair.source != "llm_proposed" && !existing.candidates.is_empty() {
                    let allowed: BTreeSet<&str> = existing
                        .candidates
                        .iter()
                        .filter_map(Value::as_str)
                        .collect();
                    if targets.iter().any(|t| !allowed.contains(t.as_str())) {
                        return reject_review("target_not_in_candidates", start);
                    }
                }
                for target in &targets {
                    if !index
                        .catalog
                        .entities
                        .iter()
                        .any(|e| e.registry_id == *target)
                    {
                        return reject_review("unauthorized_target", start);
                    }
                }
                let deterministic_target = repair.source == "decision_ranked"
                    && clause
                        .slots
                        .get("action")
                        .is_some_and(|s| s.status == "resolved" && s.source.as_deref() == Some("nlu_rule"))
                    && repair.evidence.iter().any(|ev| {
                        matches!(
                            ev.as_str(),
                            "single_authorized_candidate"
                                | "single_authorized_candidate_in_area"
                                | "catalog_token_match"
                        )
                    });
                if !deterministic_target {
                    requires_confirmation = true;
                }
            }
            "parameters" => {
                if serde_json::from_value::<Parameters>(repair.value.clone()).is_err() {
                    return reject_review("invalid_parameters", start);
                }
                requires_confirmation = true;
            }
            "area" => {
                if repair.value.as_str().is_none_or(str::is_empty) {
                    return reject_review("invalid_area", start);
                }
                let deterministic_area = repair.source == "decision_ranked"
                    && clause
                        .slots
                        .get("action")
                        .is_some_and(|s| s.status == "resolved" && s.source.as_deref() == Some("nlu_rule"))
                    && repair.evidence.iter().any(|ev| ev == "origin_area_match");
                if !deterministic_area {
                    requires_confirmation = true;
                }
            }
            _ => return reject_review("invalid_slot", start),
        }

        clause.slots.insert(
            repair.slot.clone(),
            slot(
                repair.value.clone(),
                "resolved",
                repair.evidence.clone(),
                Some(&repair.source),
                Vec::new(),
            ),
        );
    }

    let mut operations = Vec::new();
    for clause in &clauses {
        let Some(action) = clause
            .slots
            .get("action")
            .filter(|s| s.status == "resolved")
            .and_then(|s| s.value.as_str())
            .map(str::to_owned)
        else {
            return reject_review("missing_resolved_action", start);
        };
        let Some(target_slot) = clause
            .slots
            .get("target")
            .filter(|s| s.status == "resolved")
        else {
            return reject_review("missing_resolved_target", start);
        };
        let targets: Vec<String> = if let Some(single) = target_slot.value.as_str() {
            vec![single.to_owned()]
        } else if let Some(arr) = target_slot.value.as_array() {
            arr.iter()
                .filter_map(|v| v.as_str().map(str::to_owned))
                .collect()
        } else {
            Vec::new()
        };
        if targets.is_empty() {
            return reject_review("missing_resolved_target", start);
        }
        for target in &targets {
            if clause.constraints.excluded_targets.contains(target) {
                return reject_review("excluded_target_violation", start);
            }
            let Some(entity) = index
                .catalog
                .entities
                .iter()
                .find(|e| e.registry_id == *target)
            else {
                return reject_review("unauthorized_target", start);
            };
            if !entity.actions.contains(&action) {
                return reject_review("action_not_supported_by_target", start);
            }
        }
        if matches!(action.as_str(), "unlock" | "lock" | "arm" | "disarm") {
            requires_confirmation = true;
        }
        let mut parameters = clause
            .slots
            .get("parameters")
            .and_then(|s| serde_json::from_value::<Parameters>(s.value.clone()).ok())
            .unwrap_or_default();
        if !clause.constraints.excluded_targets.is_empty() {
            parameters
                .excluded_targets
                .clone_from(&clause.constraints.excluded_targets);
        }
        let mut evidence = Vec::new();
        for s in clause.slots.values() {
            evidence.extend(s.evidence.clone());
        }
        operations.push(Operation {
            intent: format!("progressive.{action}"),
            action,
            targets,
            parameters,
            evidence,
            depends_on: Vec::new(),
        });
    }

    let mut timings = BTreeMap::new();
    timings.insert("review_ms".into(), start.elapsed().as_secs_f64() * 1_000.0);
    ReviewProposalResponse {
        version: 5,
        accepted: true,
        status: "plan".into(),
        reason: None,
        operations,
        requires_confirmation,
        timings,
    }
}
