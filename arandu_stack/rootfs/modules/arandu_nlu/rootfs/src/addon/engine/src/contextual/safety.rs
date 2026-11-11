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

#[allow(clippy::too_many_lines)]
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
    let permitted_temporal = is_permitted_temporal(&value);
    let permitted_exclusion = is_permitted_exclusion(&value);
    let timer = permitted_temporal
        || (["timer", "temporizador", "alarme"]
            .iter()
            .any(|word| words.contains(word))
            && words
                .intersection(&effects)
                .all(|word| ["coloca", "ajusta", "cancela", "cancele", "pausa"].contains(word)));
    if !words.is_disjoint(&effects)
        && has_schedule(&value)
        && !calendar
        && !timer
        && !permitted_temporal
    {
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
            if calendar && phrase == "amanha" {
                return false;
            }
            if permitted_temporal
                && reason == "semantic_condition"
                && matches!(
                    phrase.as_str(),
                    "amanha" | "daqui a" | "depois" | "mais tarde" | "antes de" | "apos"
                )
            {
                return false;
            }
            if permitted_exclusion && reason == "semantic_exclusion" {
                return false;
            }
            value.contains(&format!(" {phrase} "))
        }) {
            return Some(reason);
        }
    }
    None
}

fn has_prohibited_condition(value: &str) -> bool {
    let prohibited = [
        " se ",
        " caso ",
        " quando ",
        " desde que ",
        " contanto que ",
        " a menos que ",
        " assim que ",
        " enquanto ",
        " ate que ",
        " logo que ",
        " passar de ",
        " acima de ",
        " abaixo de ",
        " maior que ",
        " menor que ",
    ];
    prohibited.iter().any(|pattern| value.contains(pattern))
}

fn is_permitted_temporal(value: &str) -> bool {
    if has_prohibited_condition(value) {
        return false;
    }
    let words: Vec<&str> = value.split_whitespace().collect();
    // 1. Timers
    if words.iter().any(|w| {
        matches!(
            *w,
            "timer" | "temporizador" | "timers" | "temporizadores" | "contagem"
        )
    }) {
        return true;
    }
    // 2. Despertadores / Alarme de relógio
    if words
        .iter()
        .any(|w| matches!(*w, "despertador" | "despertadores" | "acorda"))
        || (words.contains(&"alarme")
            && !value.contains("alarme da casa")
            && !value.contains("da casa")
            && !value.contains("seguranca"))
    {
        return true;
    }
    // 3. Lembretes
    if words.iter().any(|w| matches!(*w, "lembrete" | "lembretes"))
        || value.contains(" me lembra ")
        || value.contains(" lembra de ")
        || value.contains(" lembrar de ")
    {
        return true;
    }
    // 4. Ações agendadas e sleep timer
    if value.contains("sleep timer")
        || value.contains("tarefa de")
        || value.contains("acoes estao agendadas")
        || value.contains("acoes agendadas")
    {
        return true;
    }
    let has_action = words.iter().any(|w| {
        matches!(
            *w,
            "desliga"
                | "desligue"
                | "desligar"
                | "apaga"
                | "apague"
                | "apagar"
                | "liga"
                | "ligue"
                | "ligar"
                | "acende"
                | "acenda"
                | "acender"
                | "para"
                | "pare"
                | "parar"
                | "pausa"
                | "pausar"
        )
    });
    let has_delay = slots::relative_delay(value).is_some()
        || value.contains(" daqui a ")
        || value.contains(" daqui ")
        || value.contains(" depois de ")
        || value.contains(" apos ")
        || (value.contains(" em ")
            && words
                .iter()
                .any(|w| matches!(*w, "minuto" | "minutos" | "hora" | "horas")));
    let has_clock = slots::clock_time(value).is_some()
        || (value.contains(" as ")
            && words.iter().any(|w| {
                matches!(
                    *w,
                    "noite"
                        | "tarde"
                        | "manha"
                        | "onze"
                        | "dez"
                        | "nove"
                        | "oito"
                        | "sete"
                        | "seis"
                        | "22h"
                        | "22:00"
                        | "23:00"
                ) || w.ends_with('h')
                    || w.chars().all(|c| c.is_ascii_digit())
            }));
    has_action && (has_delay || has_clock)
}

fn is_permitted_exclusion(value: &str) -> bool {
    let has_exclusion_marker = value.contains(" menos ")
        || value.contains(" exceto ")
        || value.contains(" com excecao de ")
        || value.contains(" com excecao ");
    if !has_exclusion_marker {
        return false;
    }
    let words: Vec<&str> = value.split_whitespace().collect();
    let has_bulk_verb = words.iter().any(|w| {
        matches!(
            *w,
            "desliga"
                | "desligue"
                | "desligar"
                | "apaga"
                | "apague"
                | "apagar"
                | "liga"
                | "ligue"
                | "ligar"
                | "acende"
                | "acenda"
                | "acender"
        )
    });
    let has_bulk_noun = words
        .iter()
        .any(|w| matches!(*w, "tudo" | "toda" | "todas" | "todo" | "todos"));
    has_bulk_verb && has_bulk_noun
}
