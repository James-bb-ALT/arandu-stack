//! Local, passive capability-driven interpretation. Wire v4 is additive to v1-v3.
pub mod contract;
mod grammar;
pub mod progressive;
mod resolver;
mod safety;
pub mod slots;

pub use progressive::{interpret_partial, review_proposal};

use contract::{Catalog, Command, ContextRequest, Operation, Response};
use resolver::Index;
use std::{
    collections::{BTreeMap, BTreeSet},
    sync::{Arc, Mutex, OnceLock},
    time::Instant,
};

const MAX_CACHED_CATALOGS: usize = 8;
static CATALOGS: OnceLock<Mutex<BTreeMap<String, Arc<Index>>>> = OnceLock::new();

fn catalogs() -> &'static Mutex<BTreeMap<String, Arc<Index>>> {
    CATALOGS.get_or_init(|| Mutex::new(BTreeMap::new()))
}

/// Compile and retain a bounded immutable catalog, never executing a service.
#[must_use]
pub fn register_catalog(catalog: Catalog) -> Response {
    let generation = catalog.generation.clone();
    let Some(index) = Index::new(catalog) else {
        return Response::status("invalid_request");
    };
    let Ok(mut cache) = catalogs().lock() else {
        return Response::status("unavailable");
    };
    if !cache.contains_key(&generation)
        && cache.len() == MAX_CACHED_CATALOGS
        && let Some(key) = cache.keys().next().cloned()
    {
        cache.remove(&key);
    }
    cache.insert(generation.clone(), Arc::new(index));
    let mut response = Response::status("catalog_ready");
    response.generation = Some(generation);
    response.reason = Some("phase5_supported".into());
    response
}

/// Interpret a request against an existing immutable catalog generation.
#[must_use]
pub fn interpret(request: &ContextRequest) -> Response {
    let start = Instant::now();
    if !request.valid() {
        return Response::status("invalid_request");
    }
    let index = catalogs()
        .lock()
        .ok()
        .and_then(|cache| cache.get(&request.generation).cloned());
    let Some(index) = index else {
        return Response::status("stale");
    };
    let normal_start = Instant::now();
    let text = slots::normalize(&request.text);
    let normalization_ms = normal_start.elapsed().as_secs_f64() * 1_000.0;
    let mut result = interpret_index(&text, request, &index);
    result
        .timings
        .insert("normalization_ms".into(), normalization_ms);
    result
        .timings
        .insert("total_ms".into(), start.elapsed().as_secs_f64() * 1_000.0);
    result
}

#[allow(clippy::too_many_lines)] // Ordered bounded plan construction with fail-closed early exits.
fn interpret_index(text: &str, request: &ContextRequest, index: &Index) -> Response {
    if matches!(text, "nao" | "nao cancela" | "nao pode") {
        return Response::status("cancel");
    }
    if let Some(reason) = safety::refusal(text, &index.catalog)
        && grammar::parse_todo_command(text).is_none()
    {
        return Response::failure(
            if reason == "semantic_negation" {
                "cancel"
            } else {
                "unavailable"
            },
            "semantic_policy",
            reason,
        );
    }
    if let Some(response) = phase5(text, request, index) {
        return response;
    }
    if matches!(text, "sim" | "confirmo" | "pode confirmar" | "isso") {
        return Response::status("confirm");
    }
    if matches!(
        text,
        "cancela" | "cancela isso" | "cancelar" | "esquece" | "deixa pra la"
    ) {
        return Response::status("cancel");
    }
    if matches!(text, "repete" | "repita") {
        return Response::status("repeat_response");
    }
    let parse_start = Instant::now();
    let clauses = grammar::split_clauses(text);
    if clauses.is_empty() || clauses.len() > 4 {
        return Response::status("invalid_request");
    }
    let mut response = Response::status("plan");
    let mut properties = BTreeSet::new();
    let mut inherited = None;
    for clause in &clauses {
        let intent_start = Instant::now();
        let mut context_ms = 0.0;
        let mut command = if clauses.len() == 1 && request.pending.is_some() {
            let mut pending = request.pending.clone().unwrap_or_default();
            if pending.action == "calendar_create" {
                let value = pending
                    .parameters
                    .value
                    .get_or_insert_with(|| serde_json::json!({}));
                let Some(object) = value.as_object_mut() else {
                    return Response::status("invalid_request");
                };
                if let Some(key) = ["summary", "date", "time"]
                    .into_iter()
                    .find(|key| !object.contains_key(*key))
                {
                    object.insert(key.into(), serde_json::Value::String(clause.clone()));
                } else if !object.contains_key("duration") {
                    let Some(seconds) = slots::duration(
                        clause
                            .trim_start_matches("por ")
                            .trim_start_matches("duracao "),
                    ) else {
                        return Response::failure(
                            "clarification",
                            &pending.intent,
                            "invalid_calendar_duration",
                        );
                    };
                    object.insert("duration".into(), serde_json::Value::from(seconds));
                }
            } else if pending.action == "music" && pending.parameters.value.is_none() {
                pending.parameters.value = Some(serde_json::Value::String(clause.clone()));
            } else {
                pending.mention = Some(grammar::followup_target(clause).to_owned());
                pending.area = None;
                if pending.intent.ends_with(".implicit_on") {
                    pending.intent = pending.intent.replace(".implicit_on", ".turn_on");
                    pending.origin = false;
                    pending.area = pending.mention.take();
                }
            }
            pending
        } else {
            let context_start = Instant::now();
            let hint = index
                .domain_hint(clause)
                .or_else(|| index.recent_domain(clause, request));
            context_ms = context_start.elapsed().as_secs_f64() * 1_000.0;
            let Some(command) = grammar::recognize(clause, inherited.as_ref(), hint.as_deref())
            else {
                return Response::status("no_match");
            };
            command
        };
        *response
            .timings
            .entry("intent_and_slots_ms".into())
            .or_default() += (intent_start.elapsed().as_secs_f64() * 1_000.0 - context_ms).max(0.0);
        *response.timings.entry("context_ms".into()).or_default() += context_ms;
        if matches!(
            command.action.as_str(),
            "music" | "todo_add" | "todo_complete" | "calendar_create" | "binding" | "camera_view"
        ) && let Some(value) = command.parameters.value.as_mut()
        {
            if let Some(text) = value.as_str() {
                if let Some(original) = slots::original_slot(&request.text, text) {
                    *value = serde_json::Value::String(original);
                }
            } else if let Some(values) = value.as_object_mut() {
                for key in ["summary", "message", "contact", "person", "camera"] {
                    if let Some(value) = values.get_mut(key)
                        && let Some(text) = value.as_str()
                        && let Some(original) = slots::original_slot(&request.text, text)
                    {
                        *value = serde_json::Value::String(original);
                    }
                }
            }
        }
        index.normalize_area_control(&mut command);
        inherited = Some(command.clone());
        if command.mention.as_deref().is_some_and(|mention| {
            mention.split_whitespace().any(|word| {
                matches!(
                    word,
                    "ventiladores" | "luzes" | "lampadas" | "todos" | "todas"
                )
            })
        }) {
            command.plural = true;
        }
        if command.action == "calendar_create"
            && !command
                .parameters
                .value
                .as_ref()
                .is_some_and(|value| value.get("start").is_some() && value.get("end").is_some())
            && let Some(missing) = ["summary", "date", "time", "duration"]
                .into_iter()
                .find(|key| {
                    command
                        .parameters
                        .value
                        .as_ref()
                        .is_none_or(|value| value.get(*key).is_none())
                })
        {
            let mut response = Response::failure(
                "clarification",
                &command.intent,
                &format!("missing_calendar_{missing}"),
            );
            response.command = Some(command);
            return response;
        }
        if command.action == "music" && command.parameters.value.is_none() {
            let mut response =
                Response::failure("clarification", &command.intent, "missing_media_query");
            response.command = Some(command);
            return response;
        }
        if matches!(
            command.action.as_str(),
            "cancel" | "confirm" | "repeat_response"
        ) {
            if clauses.len() != 1 {
                return Response::status("no_match");
            }
            return Response::status(&command.action);
        }
        let resolution_start = Instant::now();
        if matches!(command.action.as_str(), "turn_on" | "turn_off")
            && command.area.is_none()
            && command.domains.is_empty()
            && !command.plural
            && !command.origin
            && command.mention.as_deref().is_none_or(|mention| {
                mention
                    .split_whitespace()
                    .all(|word| matches!(word, "a" | "o" | "as" | "os"))
            })
        {
            let mut failure = Response::failure("clarification", &command.intent, "missing_target");
            failure.command = Some(command);
            return failure;
        }
        if command.action == "timer_start" && command.parameters.duration.is_none() {
            let mut failure =
                Response::failure("clarification", &command.intent, "missing_timer_duration");
            failure.command = Some(command);
            return failure;
        }
        if command.action == "reminder_create" {
            if command.parameters.title.is_none() {
                let mut failure =
                    Response::failure("clarification", &command.intent, "missing_reminder_title");
                failure.command = Some(command);
                return failure;
            }
            if command.parameters.time.is_none() && command.parameters.delay.is_none() {
                let mut failure =
                    Response::failure("clarification", &command.intent, "missing_reminder_time");
                failure.command = Some(command);
                return failure;
            }
        }
        let resolution = index.resolve(&command, request);
        *response.timings.entry("resolution_ms".into()).or_default() +=
            resolution_start.elapsed().as_secs_f64() * 1_000.0;
        match resolution {
            Ok((targets, evidence, area_id, excluded_targets, excluded_device_ids)) => {
                for target in &targets {
                    if command.action != "query"
                        && !properties
                            .insert((target.clone(), property(&command.action).to_owned()))
                    {
                        return Response::failure(
                            "unavailable",
                            &command.intent,
                            "contradictory_plan",
                        );
                    }
                }
                let mut parameters = command.parameters;
                parameters.excluded_targets = excluded_targets;
                parameters.excluded_device_ids = excluded_device_ids;
                if evidence.iter().any(|item| item == "bulk_area") {
                    if command.domains.len() == 1 {
                        parameters.category = command.domains.first().cloned();
                    }
                    parameters.scope = Some(
                        if area_id.as_deref().is_some_and(|id| index.is_group(id)) {
                            "bulk_floor_group"
                        } else if area_id.is_none() {
                            "bulk_global"
                        } else {
                            "bulk_area"
                        }
                        .into(),
                    );
                }
                if parameters.scope.is_none()
                    && area_id.as_deref().is_some_and(|id| index.is_group(id))
                {
                    parameters.scope = Some("floor_group".into());
                }
                if parameters.scope.as_deref() == Some("floor_group")
                    && area_id.as_deref().is_none_or(|id| !index.is_group(id))
                {
                    parameters.scope = None;
                }
                if command.action == "vacuum_area" {
                    parameters.area = parameters
                        .area
                        .as_deref()
                        .and_then(|name| index.cleaning_area(name));
                    if parameters.area.is_none() {
                        return Response::failure(
                            "clarification",
                            &command.intent,
                            "unknown_cleaning_area",
                        );
                    }
                }
                if command.action == "transfer" {
                    if request.last_targets.len() != 1 {
                        return Response::failure(
                            "unavailable",
                            &command.intent,
                            "missing_music_source",
                        );
                    }
                    parameters
                        .secondary_targets
                        .clone_from(&request.last_targets);
                }
                if parameters.area.is_none() {
                    parameters.area = area_id;
                }
                let number = response.operations.len();
                response.operations.push(Operation {
                    intent: command.intent,
                    action: command.action,
                    targets,
                    parameters,
                    evidence,
                    depends_on: if number == 0 {
                        Vec::new()
                    } else {
                        vec![number - 1]
                    },
                });
            }
            Err(mut failure) => {
                if failure.status == "clarification"
                    || failure.reason.as_deref() == Some("target_not_found")
                {
                    failure.command = Some(Command { ..command });
                }
                return failure;
            }
        }
    }
    response.timings.insert(
        "intent_slots_context_planning_ms".into(),
        parse_start.elapsed().as_secs_f64() * 1_000.0,
    );
    let planning = response.timings["intent_slots_context_planning_ms"]
        - response
            .timings
            .get("intent_and_slots_ms")
            .copied()
            .unwrap_or(0.0)
        - response.timings.get("context_ms").copied().unwrap_or(0.0)
        - response
            .timings
            .get("resolution_ms")
            .copied()
            .unwrap_or(0.0);
    response
        .timings
        .insert("planning_ms".into(), planning.max(0.0));
    response
}

fn property(action: &str) -> &str {
    match action {
        "turn_on" | "turn_off" | "open" | "close" | "lock" | "unlock" | "activate" | "arm"
        | "disarm" => "power",
        "pause" | "play" | "stop" | "music" => "playback",
        "mute" | "unmute" => "mute",
        "repeat_one" | "repeat_all" | "repeat_off" => "repeat",
        "shuffle_on" | "shuffle_off" => "shuffle",
        _ => action,
    }
}

fn phase5(text: &str, request: &ContextRequest, index: &Index) -> Option<Response> {
    let quoted = request
        .text
        .trim_start()
        .starts_with(['\'', '"', '“', '‘', '`', '«']);
    if let Some(command) = grammar::parse_phase5(text) {
        if quoted
            && !matches!(
                command.action.as_str(),
                "query" | "todo_list" | "history_query"
            )
        {
            return Some(Response::failure(
                "unavailable",
                &command.intent,
                "semantic_quoted_command",
            ));
        }
        if command.parameters.aggregate.as_deref() == Some("ambiguous") {
            return Some(Response::failure(
                "clarification",
                &command.intent,
                "power_or_energy",
            ));
        }
        let targets = match index.resolve_phase5(&command, request) {
            Ok(targets) => targets,
            Err(reason) => {
                return Some(Response::failure("clarification", &command.intent, reason));
            }
        };
        let mut parameters = command.parameters;
        let first_slot = after_word(&request.text, 0);
        let mut cursor = first_slot;
        for value in [&mut parameters.old_item, &mut parameters.new_item] {
            if let Some(normal) = value.as_deref() {
                if let Some((original, end)) =
                    slots::original_slot_after(&request.text, normal, cursor)
                {
                    *value = Some(original);
                    cursor = after_word(&request.text, end);
                } else {
                    *value = None;
                }
            }
        }
        if let Some(value_text) = parameters
            .value
            .as_ref()
            .and_then(serde_json::Value::as_str)
            && let Some((original, _)) =
                slots::original_slot_after(&request.text, value_text, first_slot)
        {
            parameters.value = Some(serde_json::Value::String(original));
        }
        return Some(single_phase5(
            command.intent,
            command.action,
            targets,
            parameters,
        ));
    }
    if let Some(command) = parse_context_ellipsis(text, request, index) {
        if quoted {
            return Some(Response::failure(
                "unavailable",
                "context.ellipsis",
                "semantic_quoted_command",
            ));
        }
        return Some(command);
    }
    if quoted
        && (text.starts_with("modo ") || matches!(text, "boa noite" | "bom dia" | "hora de dormir"))
    {
        return Some(Response::failure(
            "no_match",
            "scene.invoke_exact",
            "semantic_quoted_command",
        ));
    }
    parse_scene_command(text, index)
}

fn after_word(source: &str, start: usize) -> usize {
    source
        .char_indices()
        .filter(|(i, _)| *i >= start)
        .skip_while(|(_, ch)| ch.is_whitespace())
        .find(|(_, ch)| ch.is_whitespace())
        .map_or(source.len(), |(i, ch)| i + ch.len_utf8())
}

fn single_phase5(
    intent: String,
    action: String,
    targets: Vec<String>,
    parameters: contract::Parameters,
) -> Response {
    let mut response = Response::status("plan");
    response.operations.push(Operation {
        intent,
        action,
        targets,
        parameters,
        evidence: vec!["phase5_exact".into()],
        depends_on: vec![],
    });
    response
}

fn parse_scene_command(text: &str, index: &Index) -> Option<Response> {
    if !(text.starts_with("modo ") || matches!(text, "boa noite" | "bom dia" | "hora de dormir")) {
        return None;
    }
    let label = text.strip_prefix("modo ").unwrap_or(text);
    let mut targets: Vec<_> = index
        .catalog
        .entities
        .iter()
        .filter(|row| {
            row.domain == "scene"
                && row.actions.iter().any(|a| a == "activate")
                && std::iter::once(&row.name)
                    .chain(&row.aliases)
                    .any(|name| slots::normalize(name) == label || slots::normalize(name) == text)
        })
        .map(|row| row.registry_id.clone())
        .collect();
    targets.sort();
    if targets.len() != 1 {
        return Some(Response::failure(
            if targets.is_empty() {
                "no_match"
            } else {
                "clarification"
            },
            "phase5.scene",
            "scene_not_unique",
        ));
    }
    Some(single_phase5(
        "scene.invoke_exact".into(),
        "activate".into(),
        targets,
        contract::Parameters::default(),
    ))
}

fn context_area(text: &str) -> Option<&str> {
    [
        "agora a da ",
        "agora a do ",
        "agora o da ",
        "agora o do ",
        "a da ",
        "a do ",
        "o do ",
        "o da ",
        "faz igual no ",
        "faz igual na ",
    ]
    .iter()
    .find_map(|prefix| text.strip_prefix(prefix))
    .map(|s| s.trim_end_matches(" tambem"))
}

fn parse_context_ellipsis(text: &str, request: &ContextRequest, index: &Index) -> Option<Response> {
    if request.pending.is_some() {
        return None;
    }
    let undo = matches!(
        text,
        "volta como estava"
            | "volte como estava"
            | "desfaz"
            | "desfaz a ultima mudanca"
            | "desfaca"
            | "desfaca a ultima mudanca"
    );
    let other = text == "o outro tambem" || text == "a outra tambem";
    let area_text = context_area(text);
    if !undo && !other && area_text.is_none() {
        if matches!(
            text,
            "faz igual" | "o outro" | "a outra" | "agora a" | "agora o"
        ) || text.starts_with("faz igual ")
            || text.starts_with("o outro ")
            || text.starts_with("a outra ")
        {
            return Some(Response::failure(
                "clarification",
                "context.ellipsis",
                "context_insufficient",
            ));
        }
        return None;
    }
    let fail = || Response::failure("clarification", "phase5.context", "context_insufficient");
    let Some(prior) = request.antecedent.as_ref() else {
        return Some(fail());
    };
    let Some(source) = index
        .catalog
        .entities
        .iter()
        .find(|row| prior.targets.contains(&row.registry_id))
    else {
        return Some(fail());
    };
    if !matches!(
        source.domain.as_str(),
        "light" | "fan" | "media_player" | "climate" | "switch" | "input_boolean"
    ) {
        return Some(fail());
    }
    if undo {
        return Some(single_phase5(
            "context.undo".into(),
            "contextual_undo".into(),
            prior.targets.clone(),
            contract::Parameters::default(),
        ));
    }
    let areas: Vec<_> = index
        .catalog
        .areas
        .iter()
        .filter(|area| {
            area.names
                .iter()
                .any(|n| Some(slots::normalize(n)).as_deref() == area_text)
        })
        .map(|a| a.area_id.as_str())
        .collect();
    if !other && areas.len() != 1 {
        return Some(fail());
    }
    let mut targets: Vec<_> = index
        .catalog
        .entities
        .iter()
        .filter(|row| {
            row.domain == source.domain
                && row.actions.contains(&prior.action)
                && if other {
                    row.area_id == source.area_id && row.registry_id != source.registry_id
                } else {
                    row.area_id.as_deref() == areas.first().copied()
                }
        })
        .map(|r| r.registry_id.clone())
        .collect();
    targets.sort();
    if targets.len() != 1 {
        return Some(fail());
    }
    Some(single_phase5(
        "context.ellipsis".into(),
        prior.action.clone(),
        targets,
        prior.parameters.clone(),
    ))
}
