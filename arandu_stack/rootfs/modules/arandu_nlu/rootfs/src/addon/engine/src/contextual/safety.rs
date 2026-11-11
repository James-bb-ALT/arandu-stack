//! Shared, versioned restriction vocabulary. No service execution or inference.
use super::{contract::Catalog, slots};
use serde::Deserialize;
use std::{collections::BTreeSet, sync::OnceLock};

#[derive(Deserialize)]
struct Policy {
    negation: Vec<String>,
    exclusion: Vec<String>,
    condition: Vec<String>,
    effects: Vec<String>,
    queries: Vec<String>,
    name_prefix: Vec<String>,
    speech: Vec<String>,
}

pub(super) fn refusal(text: &str, catalog: &Catalog) -> Option<&'static str> {
    static POLICY: OnceLock<Policy> = OnceLock::new();
    let policy = POLICY.get_or_init(|| {
        serde_json::from_str(include_str!("../../data/semantic_policy.json"))
            .expect("reviewed semantic policy")
    });
    let mut value = format!(" {text} ");
    let temporal_units = ["horas", "hora", "minutos", "minuto", "segundos", "segundo"];
    let has_schedule = |value: &str| {
        let words: Vec<&str> = value.split_whitespace().collect();
        words.iter().any(|word| temporal_units.contains(word))
            || words
                .windows(2)
                .any(|pair| pair[0] == "as" && pair[1].starts_with(|c: char| c.is_ascii_digit()))
    };
    if !has_schedule(&value)
        && !policy
            .condition
            .iter()
            .chain(&policy.exclusion)
            .chain(&policy.negation)
            .any(|phrase| value.contains(&format!(" {phrase} ")))
    {
        return None;
    }
    let mut labels: Vec<String> = catalog
        .entities
        .iter()
        .flat_map(|row| std::iter::once(&row.name).chain(row.aliases.iter()))
        .chain(catalog.areas.iter().flat_map(|row| row.names.iter()))
        .map(|name| slots::normalize(name))
        .collect();
    labels.sort_by(|a, b| b.len().cmp(&a.len()).then_with(|| a.cmp(b)));
    labels.dedup();
    let effects: BTreeSet<&str> = policy.effects.iter().map(String::as_str).collect();
    let mut allowed = effects.clone();
    allowed.extend(policy.name_prefix.iter().map(String::as_str));
    for label in labels {
        if label.is_empty() || label.split_whitespace().any(|word| effects.contains(word)) {
            continue;
        }
        let needle = format!(" {label} ");
        let positions: Vec<usize> = value.match_indices(&needle).map(|(i, _)| i).collect();
        for position in positions.into_iter().rev() {
            let prefix: Vec<&str> = value[..position].split_whitespace().collect();
            if !prefix.is_empty() && prefix.iter().all(|word| allowed.contains(word)) {
                value.replace_range(position..position + needle.len(), " alvo ");
            }
        }
    }
    if !value.split_whitespace().any(|word| effects.contains(word))
        && policy
            .queries
            .iter()
            .any(|query| value.starts_with(&format!(" {query} ")))
    {
        return None;
    }
    let words: BTreeSet<&str> = value.split_whitespace().collect();
    if words.is_disjoint(&effects)
        && policy
            .speech
            .iter()
            .any(|word| words.contains(word.as_str()))
    {
        return None;
    }
    let calendar = (words.contains("agenda") || words.contains("calendario"))
        && [" coloca ", " cria ", " marca "]
            .iter()
            .any(|prefix| value.starts_with(prefix))
        && words.intersection(&effects).all(|word| *word == "coloca");
    let timer = ["timer", "temporizador", "alarme"]
        .iter()
        .any(|word| words.contains(word))
        && words
            .intersection(&effects)
            .all(|word| ["coloca", "ajusta", "cancela", "cancele", "pausa"].contains(word));
    if !words.is_disjoint(&effects) && has_schedule(&value) && !calendar && !timer {
        return Some("semantic_condition");
    }
    let mut sequence = value.clone();
    for effect in &effects {
        sequence = sequence.replace(
            &format!(" e depois {effect} "),
            &format!(" e sequencia {effect} "),
        );
    }
    value = sequence;
    for (phrases, reason) in [
        (&policy.condition, "semantic_condition"),
        (&policy.exclusion, "semantic_exclusion"),
        (&policy.negation, "semantic_negation"),
    ] {
        if phrases.iter().any(|phrase| {
            !(calendar && phrase == "amanha") && value.contains(&format!(" {phrase} "))
        }) {
            return Some(reason);
        }
    }
    None
}
