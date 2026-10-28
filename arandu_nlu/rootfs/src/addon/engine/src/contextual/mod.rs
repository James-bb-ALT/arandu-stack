//! Local, passive capability-driven interpretation. Wire v4 is additive to v1-v3.
pub mod contract;
mod grammar;
mod resolver;
pub mod slots;

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
    if text
        .split_whitespace()
        .any(|token| matches!(token, "nao" | "nunca" | "jamais"))
    {
        return Response::status("cancel");
    }
    let first = text.split_whitespace().next().unwrap_or_default();
    if text.starts_with("se ")
        || text.contains(" exceto ")
        || (text.contains(" se ")
            && ["liga", "desliga", "ativa", "abre", "fecha", "coloca"].contains(&first))
    {
        return Response::failure("unavailable", "conditional_plan", "condition_not_supported");
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
            let hint = index.domain_hint(clause);
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
        let resolution = index.resolve(&command, request);
        *response.timings.entry("resolution_ms".into()).or_default() +=
            resolution_start.elapsed().as_secs_f64() * 1_000.0;
        match resolution {
            Ok((targets, evidence, area_id)) => {
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
