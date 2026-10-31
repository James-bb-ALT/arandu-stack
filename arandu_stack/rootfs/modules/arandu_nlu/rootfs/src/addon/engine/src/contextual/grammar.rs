use super::{
    contract::{Command, Parameters},
    slots,
};
use serde::Deserialize;
use serde_json::Value;
use std::{collections::BTreeMap, sync::OnceLock};

#[derive(Clone, Deserialize)]
struct Rule {
    intent: String,
    operation: String,
    domains: Vec<String>,
    templates: Vec<String>,
    target_slot: Option<String>,
    value_slot: Option<String>,
    device_class: Option<String>,
    metric: Option<String>,
    aggregate: Option<String>,
    relative: Option<f64>,
    state_filter: Option<String>,
    threshold: Option<f64>,
    #[serde(default)]
    plural: bool,
    #[serde(default)]
    origin: bool,
}

#[derive(Clone)]
enum Token {
    Literal(String),
    Slot(String),
}
#[derive(Clone)]
struct Pattern {
    tokens: Vec<Token>,
    rule: usize,
    weight: usize,
}
struct Grammar {
    rules: Vec<Rule>,
    by_first: BTreeMap<String, Vec<Pattern>>,
}
static GRAMMAR: OnceLock<Grammar> = OnceLock::new();

fn grammar() -> &'static Grammar {
    GRAMMAR.get_or_init(|| {
        let rules: Vec<Rule> = serde_json::from_str(include_str!("../../data/grammar.json"))
            .expect("reviewed compile-time grammar");
        let mut by_first: BTreeMap<String, Vec<Pattern>> = BTreeMap::new();
        for (number, rule) in rules.iter().enumerate() {
            for template in &rule.templates {
                let mut variants = vec![template.clone()];
                for suffix in [" da {area}", " do {area}", " na {area}"] {
                    if let Some(short) = template.strip_suffix(suffix) {
                        variants.push(short.to_owned());
                    }
                }
                for variant in variants {
                    let tokens = tokenize_template(&variant);
                    let first = match tokens.first() {
                        Some(Token::Literal(word)) => word.clone(),
                        _ => "*".into(),
                    };
                    let weight = tokens
                        .iter()
                        .filter(|token| matches!(token, Token::Literal(_)))
                        .count();
                    let pattern = Pattern {
                        tokens,
                        rule: number,
                        weight,
                    };
                    if ["a", "o"].contains(&first.as_str()) {
                        let alternate = if first == "a" { "o" } else { "a" };
                        by_first
                            .entry(alternate.into())
                            .or_default()
                            .push(pattern.clone());
                    }
                    by_first.entry(first).or_default().push(pattern);
                }
            }
        }
        Grammar { rules, by_first }
    })
}

fn tokenize_template(template: &str) -> Vec<Token> {
    template
        .split_whitespace()
        .flat_map(|word| {
            if word.starts_with('{') && word.ends_with('}') {
                vec![Token::Slot(word[1..word.len() - 1].to_owned())]
            } else {
                slots::normalize(word)
                    .split_whitespace()
                    .map(|word| {
                        Token::Literal(
                            match word {
                                "ligar" => "liga",
                                "desligar" => "desliga",
                                "abrir" => "abre",
                                "fechar" => "fecha",
                                "trancar" => "tranca",
                                "destrancar" => "destranca",
                                "acabar" => "finaliza",
                                _ => word,
                            }
                            .to_owned(),
                        )
                    })
                    .collect()
            }
        })
        .collect()
}

#[allow(clippy::too_many_lines)] // Bounded capture backtracking with explicit slot-type constraints.
fn captures(
    tokens: &[Token],
    words: &[&str],
    slots: &mut BTreeMap<String, String>,
    budget: &mut usize,
) -> bool {
    if *budget == 0 {
        return false;
    }
    *budget -= 1;
    let Some(token) = tokens.first() else {
        return words.is_empty();
    };
    match token {
        Token::Literal(expected) => {
            words
                .first()
                .is_some_and(|word| literal_match(word, expected))
                && captures(&tokens[1..], &words[1..], slots, budget)
        }
        Token::Slot(name) => {
            for length in (1..=words.len().min(16)).rev() {
                let value = words[..length].join(" ");
                if name == "color"
                    && !matches!(
                        value.as_str(),
                        "azul"
                            | "verde"
                            | "vermelho"
                            | "vermelha"
                            | "amarelo"
                            | "amarela"
                            | "branco"
                            | "branca"
                            | "laranja"
                            | "roxo"
                            | "roxa"
                            | "rosa"
                    )
                {
                    continue;
                }
                if name == "kelvin"
                    && slots::numeric_parameter(&value).is_none_or(|number| number < 1_000.0)
                {
                    continue;
                }
                if matches!(
                    name.as_str(),
                    "brightness" | "humidity_target" | "volume" | "position" | "cover_position"
                ) && (value.contains("kelvin")
                    || slots::numeric_parameter(&value).is_some_and(|number| number > 100.0))
                {
                    continue;
                }
                if matches!(name.as_str(), "hvac_mode" | "swing_mode" | "preset_mode")
                    && slots::numeric_parameter(&value).is_some()
                {
                    continue;
                }
                if name == "date_ref"
                    && !matches!(
                        value.as_str(),
                        "hoje"
                            | "amanha"
                            | "segunda feira"
                            | "terca feira"
                            | "quarta feira"
                            | "quinta feira"
                            | "sexta feira"
                            | "sabado"
                            | "domingo"
                    )
                {
                    continue;
                }
                if matches!(
                    name.as_str(),
                    "device"
                        | "light"
                        | "fan"
                        | "climate"
                        | "cover"
                        | "vacuum"
                        | "mower"
                        | "plug"
                        | "appliance"
                ) && value.split_whitespace().last().is_some_and(|word| {
                    matches!(word, "para" | "em" | "da" | "do" | "de" | "na" | "no")
                }) {
                    continue;
                }
                if name == "swing_mode"
                    && !matches!(
                        value.as_str(),
                        "para cima"
                            | "para baixo"
                            | "cima"
                            | "baixo"
                            | "vertical"
                            | "horizontal"
                            | "ligado"
                            | "desligado"
                            | "on"
                            | "off"
                            | "up"
                            | "down"
                            | "fixo"
                            | "oscilando"
                            | "automatico"
                    )
                {
                    continue;
                }
                if matches!(
                    name.as_str(),
                    "temperature"
                        | "brightness"
                        | "volume"
                        | "humidity_target"
                        | "position"
                        | "cover_position"
                        | "kelvin"
                ) && slots::numeric_parameter(&value).is_none()
                {
                    continue;
                }
                if let Some(previous) = slots.get(name)
                    && previous != &value
                {
                    continue;
                }
                let previous = slots.insert(name.clone(), value);
                if captures(&tokens[1..], &words[length..], slots, budget) {
                    return true;
                }
                if let Some(previous) = previous {
                    slots.insert(name.clone(), previous);
                } else {
                    slots.remove(name);
                }
            }
            false
        }
    }
}

fn literal_match(actual: &str, expected: &str) -> bool {
    actual == expected
        || [
            &["a", "o"][..],
            &["as", "os"][..],
            &["da", "do"][..],
            &["das", "dos"][..],
            &["na", "no"][..],
            &["nas", "nos"][..],
            &["ta", "esta"][..],
            &["ligada", "ligado"][..],
            &["acesa", "aceso"][..],
            &["aberta", "aberto"][..],
            &["fechada", "fechado"][..],
            &["ocupada", "ocupado"][..],
            &["seca", "seco"][..],
            &["vazia", "vazio"][..],
        ]
        .iter()
        .any(|group| group.contains(&actual) && group.contains(&expected))
}

#[allow(clippy::too_many_lines)] // Template selection and one supplemental grammar fallback.
pub fn recognize(
    text: &str,
    inherited: Option<&Command>,
    domain_hint: Option<&str>,
) -> Option<Command> {
    let mut text = canonicalize(text);
    // Closed, reusable category allowlist. Never derive an implicit action from names.
    if let Some(domain) = implicit_category(&text) {
        let mut result = command("turn_on", Some(domain), None);
        result.intent = format!("{domain}.implicit_on");
        result.plural = true;
        result.origin = true;
        return Some(result);
    }
    if let Some(device) = text
        .strip_prefix("onde esta ")
        .or_else(|| text.strip_prefix("em qual comodo esta "))
        && ["telefone", "celular", "tablet", "notebook", "aparelho"]
            .iter()
            .any(|kind| device.split_whitespace().any(|word| word == *kind))
    {
        let mut result = command("query", None, Some(device));
        result.intent = "device.location_query".into();
        result.domains = ["sensor", "device_tracker"]
            .into_iter()
            .map(str::to_owned)
            .collect();
        result.parameters.metric = Some("location".into());
        result.parameters.scope = Some("device_location".into());
        return Some(result);
    }
    if matches!(
        text.as_str(),
        "liga" | "ligue" | "acende" | "acenda" | "desliga" | "desligue" | "apaga" | "apague"
    ) {
        return Some(command(
            if text.starts_with("deslig") || text.starts_with("apag") {
                "turn_off"
            } else {
                "turn_on"
            },
            None,
            None,
        ));
    }
    if let Some(query) = contextual_query(&text) {
        return Some(query);
    }
    if (text.starts_with("nenhuma luz ") || text.starts_with("nenhuma das luzes "))
        && text
            .split_whitespace()
            .any(|word| matches!(word, "ligada" | "ligadas" | "acesa" | "acesas"))
    {
        let mut result = command("query", Some("light"), None);
        result.plural = true;
        result.mention = query_area_tail(&text);
        result.parameters.aggregate = Some("none".into());
        result.parameters.state_filter = Some("on".into());
        return Some(result);
    }
    if [
        "tem alguma luz ligada",
        "tem alguma luz acesa",
        "tem luz ligada",
        "tem luz acesa",
        "quais luzes",
        "quantas luzes",
        "todas as luzes",
    ]
    .iter()
    .any(|prefix| text.starts_with(prefix))
    {
        return extended(&text, domain_hint);
    }
    if text.starts_with("tem alguma coisa ligada")
        || text.starts_with("tem algum equipamento ligado")
    {
        let mut result = command("query", None, None);
        result.domains = [
            "light",
            "switch",
            "fan",
            "climate",
            "media_player",
            "humidifier",
        ]
        .into_iter()
        .map(str::to_owned)
        .collect();
        result.plural = true;
        result.mention = query_area_tail(&text);
        result.parameters.aggregate = Some("any".into());
        result.parameters.state_filter = Some("on".into());
        return Some(result);
    }
    if text.starts_with("tem algum")
        && text.contains("bateria")
        && (text.contains("acabando") || text.contains("baixa"))
    {
        let mut result = command("query", Some("sensor"), None);
        result.intent = "battery.low_query".into();
        result.plural = true;
        result.device_class = Some("battery".into());
        result.parameters.metric = Some("battery".into());
        result.parameters.aggregate = Some("filter".into());
        result.parameters.threshold = Some(20.0);
        return Some(result);
    }
    if text == "toca no spotify" {
        let mut result = command("music", Some("media_player"), None);
        result.intent = "media.spotify_play".into();
        result.parameters.provider = Some("spotify".into());
        return Some(result);
    }
    if let Some(previous) = inherited
        && let Some(area) = [
            "a do ", "a da ", "o do ", "o da ", "do ", "da ", "na ", "no ",
        ]
        .iter()
        .find_map(|prefix| text.strip_prefix(prefix))
    {
        let mut command = previous.clone();
        command.mention = None;
        command.area = Some(area.to_owned());
        return Some(command);
    }
    let domain = domain_hint.or_else(|| semantic_domain(&text)).or_else(|| {
        if text.contains(" a do ")
            || text.contains(" a da ")
            || text.contains(" o do ")
            || text.contains(" o da ")
        {
            inherited.and_then(|previous| previous.domains.first().map(String::as_str))
        } else {
            None
        }
    });
    if let Some(name) = text.strip_prefix("ativa o modo ") {
        return Some(command(
            "activate",
            domain.filter(|domain| matches!(*domain, "scene" | "script")),
            Some(name),
        ));
    }
    if text.starts_with("coloca no canal ")
        || text.starts_with("proximo canal")
        || text.starts_with("proxima canal")
        || text.starts_with("volta um canal")
    {
        return extended(&text, domain);
    }
    if matches!(text.as_str(), "como esta a casa" | "como esta casa") {
        let mut result = command("query", None, None);
        result.plural = true;
        result.parameters.metric = Some("inventory".into());
        return Some(result);
    }
    let words: Vec<&str> = text.split_whitespace().collect();
    let first = words.first()?;
    let grammar = grammar();
    let mut matches: Vec<(usize, Command)> = Vec::new();
    {
        for pattern in grammar
            .by_first
            .get(*first)
            .into_iter()
            .flatten()
            .chain(grammar.by_first.get("*").into_iter().flatten())
        {
            let rule = &grammar.rules[pattern.rule];
            if rule.operation == "query"
                && text
                    .rsplit_once(" em ")
                    .is_some_and(|(_, value)| slots::numeric_parameter(value).is_some())
                && !text.starts_with("qual ")
                && !text.starts_with("quanto ")
            {
                continue;
            }
            if pattern.weight == 0
                && !domain
                    .is_some_and(|domain| rule.domains.iter().any(|candidate| candidate == domain))
            {
                continue;
            }
            if domain.is_some_and(|domain| {
                !rule.domains.is_empty()
                    && !rule.intent.starts_with("group.")
                    && !(rule.operation == "query"
                        && (rule.device_class.is_some()
                            || rule.target_slot.as_deref() == Some("device")))
                    && !rule.domains.iter().any(|candidate| candidate == domain)
            }) {
                continue;
            }
            let mut values = BTreeMap::new();
            if !captures(&pattern.tokens, &words, &mut values, &mut 256) {
                continue;
            }
            if let Some(command) = from_rule(rule, &values) {
                let specificity = usize::from(domain.is_some_and(|domain| {
                    command.domains.iter().any(|candidate| candidate == domain)
                }));
                let measurement = usize::from(
                    rule.operation == "query"
                        && rule
                            .device_class
                            .as_ref()
                            .is_some_and(|class| !class.is_empty()),
                );
                matches.push((
                    pattern.weight * 2
                        + specificity * 32
                        + measurement * 32
                        + usize::from(values.contains_key("floor_group")),
                    command,
                ));
            }
        }
    }
    if !matches.is_empty() {
        matches.sort_by(|a, b| b.0.cmp(&a.0).then_with(|| a.1.intent.cmp(&b.1.intent)));
        let weight = matches[0].0;
        let mut command = matches[0].1.clone();
        for (_, candidate) in matches.iter().filter(|(rank, _)| *rank == weight).skip(1) {
            if candidate.action != command.action || candidate.parameters != command.parameters {
                return None;
            }
            command.domains.extend(candidate.domains.iter().cloned());
        }
        command.domains.sort();
        command.domains.dedup();
        return Some(command);
    }
    for prefix in ["pode ", "poderia ", "por favor ", "quero "] {
        if let Some(rest) = text.strip_prefix(prefix) {
            text = rest.to_owned();
            break;
        }
    }
    extended(&text, domain).or_else(|| basic(&text, domain))
}

#[allow(clippy::too_many_lines)] // Explicit slot contracts share one reviewed rule table.
fn from_rule(rule: &Rule, values: &BTreeMap<String, String>) -> Option<Command> {
    let mut command = Command {
        intent: rule.intent.clone(),
        action: rule.operation.clone(),
        domains: rule.domains.clone(),
        mention: None,
        area: values
            .get("area")
            .or_else(|| values.get("floor_group"))
            .cloned(),
        device_class: rule.device_class.clone().filter(|value| !value.is_empty()),
        plural: rule.plural,
        origin: rule.origin,
        parameters: Parameters {
            metric: rule.metric.clone(),
            aggregate: rule.aggregate.clone(),
            relative: rule.relative,
            state_filter: rule.state_filter.clone(),
            threshold: rule.threshold,
            ..Parameters::default()
        },
    };
    if let Some(group) = values.get("group_type") {
        command.domains = if matches!(
            group.as_str(),
            "aparelhos" | "dispositivos" | "equipamentos"
        ) {
            [
                "light",
                "switch",
                "fan",
                "media_player",
                "climate",
                "humidifier",
            ]
            .into_iter()
            .map(str::to_owned)
            .collect()
        } else {
            vec![
                match group.as_str() {
                    "luzes" | "lampadas" | "iluminacao" => "light",
                    "ventiladores" => "fan",
                    "tomadas" => "switch",
                    "cortinas" | "persianas" => "cover",
                    _ => return None,
                }
                .into(),
            ]
        };
    }
    if values.contains_key("floor_group") {
        command.parameters.scope = Some("floor_group".into());
    }
    let targets = [
        "device",
        "light",
        "fan",
        "climate",
        "cover",
        "gate",
        "lock",
        "door",
        "window",
        "plug",
        "appliance",
        "humidifier",
        "dehumidifier",
        "vacuum",
        "mower",
        "person",
        "scene",
        "routine",
        "camera",
    ];
    command.mention = rule
        .target_slot
        .as_ref()
        .and_then(|slot| values.get(slot))
        .cloned()
        .or_else(|| targets.iter().find_map(|slot| values.get(*slot).cloned()));
    if command.mention.as_ref().is_some_and(|mention| {
        mention
            .split_whitespace()
            .any(|word| matches!(word, "aqui" | "daqui" | "isso" | "esse" | "essa"))
    }) {
        command.origin = command
            .mention
            .as_deref()
            .is_some_and(|mention| mention.contains("aqui"));
    }
    if matches!(rule.intent.as_str(), "satellite.device_here_off") {
        command.mention = Some("isso".into());
    }
    if rule.intent.starts_with("dehumidifier.") {
        if command.mention.as_ref().is_some_and(|mention| {
            mention
                .split_whitespace()
                .any(|word| word == "umidificador")
        }) {
            return None;
        }
        command.device_class = Some("dehumidifier".into());
    }
    if rule.intent.starts_with("humidifier.") {
        if command.mention.as_ref().is_some_and(|mention| {
            mention
                .split_whitespace()
                .any(|word| word == "desumidificador")
        }) {
            return None;
        }
        command.device_class = Some("humidifier".into());
    }
    if rule.intent == "door.status" {
        if command
            .mention
            .as_ref()
            .is_some_and(|name| name.contains("janela"))
        {
            return None;
        }
        command.device_class = Some("door".into());
    }
    if rule.intent == "window.status" {
        if command
            .mention
            .as_ref()
            .is_some_and(|name| name.contains("porta"))
        {
            return None;
        }
        command.device_class = Some("window".into());
    }
    if matches!(
        rule.intent.as_str(),
        "gate.open" | "gate.close" | "gate.status"
    ) && command.mention.is_none()
    {
        command.mention = Some("portao".into());
    }
    if rule.intent.starts_with("calendar.") {
        command.parameters.date = values.get("date_ref").cloned().or_else(|| {
            rule.intent
                .eq("calendar.today_query")
                .then(|| "hoje".into())
        });
        if let Some(title) = values.get("event_title") {
            command.parameters.value = Some(Value::String(title.clone()));
        }
        if rule.operation == "calendar_create" {
            let mut value = serde_json::Map::new();
            for (key, slot) in [
                ("summary", "event_title"),
                ("date", "date_ref"),
                ("time", "time_ref"),
            ] {
                if let Some(text) = values.get(slot) {
                    value.insert(key.into(), Value::String(text.clone()));
                }
            }
            command.parameters.value = Some(Value::Object(value));
        }
    }
    if matches!(rule.operation.as_str(), "binding" | "camera_view") {
        let mut value = serde_json::Map::new();
        for (slot, key) in [
            ("event_title", "summary"),
            ("date_ref", "date"),
            ("time_ref", "time"),
            ("message", "message"),
            ("contact", "contact"),
            ("person", "person"),
            ("camera", "camera"),
            ("area", "area"),
        ] {
            if let Some(text) = values.get(slot) {
                value.insert(key.into(), Value::String(text.clone()));
            }
        }
        if !value.is_empty() {
            command.parameters.value = Some(Value::Object(value));
        }
    }
    if rule.operation == "music" {
        let query = ["media", "artist", "playlist"]
            .iter()
            .find_map(|slot| values.get(*slot))
            .cloned();
        command.parameters.value = query.map(Value::String);
        if rule.intent == "media.spotify_play" {
            command.parameters.provider = Some("spotify".into());
        }
        command.area = values.get("media_destination").cloned().or(command.area);
        return Some(command);
    }
    if rule.operation == "transfer" {
        command.mention = values.get("media_player").cloned();
        command.area = values.get("media_destination").cloned();
        return Some(command);
    }
    if let Some(slot) = &rule.value_slot {
        let raw = values.get(slot).or_else(|| {
            if slot == "position" {
                values.get("cover_position")
            } else if slot == "volume" {
                values.get("sat_volume")
            } else {
                None
            }
        });
        if let Some(raw) = raw {
            if matches!(slot.as_str(), "duration" | "delta_duration" | "seek_time") {
                let value = slots::duration(raw)?;
                if rule.relative.is_some() && slot != "duration" {
                    command.parameters.relative = Some(value * rule.relative.unwrap_or(1.0));
                } else {
                    command.parameters.value = Some(Value::from(value));
                }
            } else if matches!(
                slot.as_str(),
                "brightness"
                    | "volume"
                    | "sat_volume"
                    | "fan_speed"
                    | "temperature"
                    | "kelvin"
                    | "humidity_target"
                    | "position"
                    | "cover_position"
            ) && !matches!(rule.operation.as_str(), "fan_mode")
            {
                command.parameters.value = Some(slots::qualitative(raw).map_or_else(
                    || slots::numeric_parameter(raw).map(Value::from),
                    |kind| Some(Value::String(kind.into())),
                )?);
            } else if matches!(slot.as_str(), "date" | "date_ref") {
                command.parameters.date = Some(raw.clone());
            } else if slot == "area" {
                command.parameters.area = Some(raw.clone());
                command.area = None;
            } else {
                command.parameters.value = Some(Value::String(raw.clone()));
            }
        } else if matches!(
            rule.operation.as_str(),
            "temperature"
                | "brightness"
                | "percentage"
                | "humidity"
                | "position"
                | "color"
                | "color_temperature"
                | "volume"
                | "timer_start"
        ) && rule.relative.is_none()
        {
            return None;
        }
    }
    Some(command)
}

pub fn semantic_domain(text: &str) -> Option<&'static str> {
    if let Some(domain) = super::resolver::category_domain(text) {
        return Some(domain);
    }
    let words: Vec<&str> = text.split_whitespace().collect();
    if text.contains("ar condicionado")
        || words.contains(&"split")
        || words.contains(&"ar") && !text.contains("qualidade do ar")
    {
        return Some("climate");
    }
    for (domain, synonyms) in [
        ("lawn_mower", &["cortador", "grama"][..]),
        ("humidifier", &["umidificador", "desumidificador"][..]),
        ("vacuum", &["aspirador", "robo"][..]),
        ("fan", &["ventilador", "ventiladores"][..]),
        (
            "light",
            &[
                "luz",
                "luzes",
                "iluminacao",
                "lampada",
                "lampadas",
                "abajur",
            ][..],
        ),
        (
            "media_player",
            &[
                "tv",
                "televisao",
                "televisores",
                "musica",
                "volume",
                "som",
                "spotify",
            ][..],
        ),
        (
            "cover",
            &[
                "cortina",
                "cortinas",
                "persiana",
                "persianas",
                "portao",
                "portoes",
            ][..],
        ),
        ("lock", &["fechadura", "fechaduras"][..]),
        (
            "switch",
            &[
                "tomada",
                "tomadas",
                "interruptor",
                "interruptores",
                "cafeteira",
            ][..],
        ),
        ("timer", &["timer", "temporizador", "timers"][..]),
        ("calendar", &["calendario", "agenda", "compromisso"][..]),
        ("alarm_control_panel", &["seguranca"][..]),
        ("camera", &["camera"][..]),
    ] {
        if synonyms.iter().any(|word| words.contains(word)) {
            return Some(domain);
        }
    }
    None
}

fn implicit_category(text: &str) -> Option<&'static str> {
    match text {
        "luz" | "luzes" | "iluminacao" | "lampadas" => Some("light"),
        "ventilador" | "ventiladores" => Some("fan"),
        _ => None,
    }
}

pub(super) fn canonicalize(text: &str) -> String {
    let mut text = text.strip_prefix("por favor ").unwrap_or(text).to_owned();
    for (from, to) in [
        ("ligar ", "liga "),
        ("desligar ", "desliga "),
        ("acender ", "acende "),
        ("apagar ", "apaga "),
        ("ativar ", "ativa "),
        ("desativar ", "desativa "),
        ("aciona ", "liga "),
        ("acione ", "liga "),
        ("aumentar ", "aumenta "),
        ("diminuir ", "diminui "),
        ("ajuste ", "ajusta "),
        ("coloque ", "coloca "),
        ("abrir ", "abre "),
        ("fechar ", "fecha "),
        ("trancar ", "tranca "),
        ("destrancar ", "destranca "),
        ("acabar ", "finaliza "),
    ] {
        for prefix in ["", "pode ", "poderia ", "quero "] {
            if let Some(rest) = text.strip_prefix(&format!("{prefix}{from}")) {
                text = format!("{prefix}{to}{rest}");
                break;
            }
        }
    }
    if let Some(rest) = text.strip_prefix("quero ") {
        if let Some(target) = rest
            .strip_suffix(" ligado")
            .or_else(|| rest.strip_suffix(" ligada"))
            .or_else(|| rest.strip_suffix(" acesa"))
            .or_else(|| rest.strip_suffix(" aceso"))
        {
            return format!("liga {target}");
        }
        if let Some(target) = rest
            .strip_suffix(" desligado")
            .or_else(|| rest.strip_suffix(" desligada"))
        {
            return format!("desliga {target}");
        }
    }
    text
}

fn command(action: &str, domain: Option<&str>, target: Option<&str>) -> Command {
    Command {
        intent: format!("{}.{}", domain.unwrap_or("device"), action),
        action: action.into(),
        domains: domain.into_iter().map(str::to_owned).collect(),
        mention: target.map(str::to_owned),
        ..Command::default()
    }
}

fn basic(text: &str, domain: Option<&str>) -> Option<Command> {
    // Resolver's spatial hint bypasses device templates, not the closed action map.
    let domain = domain.filter(|domain| *domain != "area");
    let (verb, target) = text.split_once(' ')?;
    let action = match verb {
        "liga" | "ligue" | "acende" | "acenda" | "ativa" | "ative" => "turn_on",
        "desliga" | "desligue" | "apaga" | "apague" | "desativa" | "desative" => "turn_off",
        "abre" | "abra" => "open",
        "fecha" | "feche" => "close",
        "tranca" | "tranque" => "lock",
        "destranca" | "destranque" => "unlock",
        _ => return None,
    };
    if target.starts_with("para ") || target.starts_with("pra ") {
        return None;
    }
    let domain = if matches!(action, "lock" | "unlock") {
        Some("lock")
    } else if domain.is_none() && matches!(verb, "acende" | "acenda" | "apaga" | "apague") {
        Some("light")
    } else {
        domain
    };
    let action = match (action, domain) {
        ("turn_on", Some("vacuum")) => "vacuum_start",
        ("turn_off", Some("vacuum")) => "vacuum_stop",
        ("turn_on", Some("lawn_mower")) => "mower_start",
        ("turn_on", Some("scene" | "script")) => "activate",
        _ => action,
    };
    let mut result = command(action, domain, Some(target));
    result.plural = target.split_whitespace().any(|word| {
        matches!(
            word,
            "todas"
                | "todos"
                | "tudo"
                | "luzes"
                | "ventiladores"
                | "cortinas"
                | "persianas"
                | "interruptores"
                | "tomadas"
        )
    });
    Some(result)
}

// Metric syntax is independent of registry names and spatial prepositions.
fn contextual_query(text: &str) -> Option<Command> {
    let spatial_front =
        !text.starts_with("me ") && !text.starts_with("diga ") && !text.starts_with("fala ");
    let (body, front_area) = if let Some((area, question)) =
        text.split_once(" qual ").filter(|_| spatial_front)
    {
        (format!("qual {question}"), Some(area))
    } else if let Some((area, question)) = text.split_once(" quanto ").filter(|_| spatial_front) {
        (format!("quanto {question}"), Some(area))
    } else {
        (text.to_owned(), None)
    };
    let first = body.split_whitespace().next()?;
    if !matches!(first, "qual" | "quanto")
        || body.contains(" alvo")
        || body.contains(" ajustada")
        || body.contains(" dispositivo")
        || body.contains(" mais quente")
    {
        return None;
    }
    let padded = format!(" {body} ");
    let (name, metric, class) = [
        ("temperatura", "temperature", "temperature"),
        ("umidade", "humidity", "humidity"),
        ("luminosidade", "illuminance", "illuminance"),
        ("iluminancia", "illuminance", "illuminance"),
        ("co2", "co2", "carbon_dioxide"),
        ("dioxido de carbono", "co2", "carbon_dioxide"),
        ("qualidade do ar", "air_quality", "aqi"),
        ("bateria", "battery", "battery"),
    ]
    .into_iter()
    .find(|(name, _, _)| padded.contains(&format!(" {name} ")))?;
    let (_, tail) = body.split_once(name)?;
    let tail = tail.trim();
    let battery_device_tail = metric == "battery" && tail.starts_with("esta ");
    let tail = tail.strip_prefix("ambiente ").unwrap_or(tail);
    let tail = tail.strip_prefix("esta ").unwrap_or(tail);
    let target = [
        "no ", "na ", "nos ", "nas ", "do ", "da ", "dos ", "das ", "de ", "em ",
    ]
    .iter()
    .find_map(|prefix| tail.strip_prefix(prefix))
    .unwrap_or(tail);
    if !battery_device_tail
        && !target.is_empty()
        && target == tail
        && !matches!(target, "aqui" | "daqui" | "nesse comodo" | "neste comodo")
    {
        return None;
    }
    let mut result = command(
        "query",
        Some("sensor"),
        (!target.is_empty()).then_some(target),
    );
    result.intent = format!("query.{metric}");
    if metric == "temperature" {
        result.domains.push("climate".into());
    }
    result.device_class = Some(class.into());
    result.parameters.metric = Some(metric.into());
    result.origin = matches!(target, "aqui" | "daqui" | "nesse comodo" | "neste comodo");
    if let Some(area) = front_area {
        result.mention = None;
        result.area = Some(area.to_owned());
    } else if tail.starts_with("no ") || tail.starts_with("na ") || tail.starts_with("em ") {
        result.mention = None;
        result.area = Some(target.to_owned());
    }
    Some(result)
}

#[allow(clippy::too_many_lines)] // Supplemental PT-BR forms beyond the lexical reference corpus.
fn extended(text: &str, domain: Option<&str>) -> Option<Command> {
    if text.starts_with("qual comodo esta mais quente")
        || text.starts_with("qual ambiente esta mais quente")
    {
        let mut result = command("query", Some("sensor"), None);
        result.device_class = Some("temperature".into());
        result.plural = true;
        result.parameters.metric = Some("temperature".into());
        result.parameters.aggregate = Some("max".into());
        return Some(result);
    }
    if text.starts_with("tem alguma luz ligada")
        || text.starts_with("tem alguma luz acesa")
        || text.starts_with("tem luz ligada")
        || text.starts_with("tem luz acesa")
        || text.starts_with("quais luzes")
        || text.starts_with("tem alguma janela aberta")
        || text.starts_with("tem alguma porta aberta")
        || text.starts_with("quantas luzes")
        || text.starts_with("todas as luzes")
        || text.starts_with("quais portas")
        || text.starts_with("quais janelas")
        || text.starts_with("todos os ventiladores")
    {
        let lighting = text.contains("luz");
        let fan = text.contains("ventiladores");
        let mut result = command(
            "query",
            Some(if lighting {
                "light"
            } else if fan {
                "fan"
            } else {
                "binary_sensor"
            }),
            None,
        );
        result.plural = true;
        result.mention = query_area_tail(text);
        result.origin = text
            .split_whitespace()
            .any(|word| matches!(word, "aqui" | "daqui"));
        result.parameters.aggregate = Some(
            if text.starts_with("quantas") {
                "count"
            } else if text.starts_with("quais") {
                "filter"
            } else if fan || text.starts_with("todas as luzes") {
                "all"
            } else {
                "any"
            }
            .into(),
        );
        result.parameters.state_filter = Some(
            if text.contains("desligad") || text.contains("apagad") {
                "off"
            } else {
                "on"
            }
            .into(),
        );
        if !lighting && !fan {
            result.parameters.metric = Some("opening".into());
            result.device_class = Some(
                if text.contains("janela") {
                    "window"
                } else {
                    "door"
                }
                .into(),
            );
        }
        return Some(result);
    }
    if let Some(rest) = text
        .strip_prefix("qual e o estado ")
        .or_else(|| text.strip_prefix("qual o estado "))
    {
        let mut result = command("query", domain, Some(rest));
        if text.contains("velocidade") {
            result.parameters.metric = Some("speed".into());
        }
        return Some(result);
    }
    if let Some((verb, target)) = text.split_once(' ') {
        if matches!(verb, "ativa" | "desativa") && target.contains("oscilacao") {
            let mut result = command(
                "oscillate",
                Some("fan"),
                Some(target.trim_start_matches("a oscilacao do ")),
            );
            result.parameters.value = Some(Value::Bool(verb == "ativa"));
            return Some(result);
        }
        if matches!(verb, "para" | "pare") && domain == Some("cover") {
            return Some(command("cover_stop", domain, Some(target)));
        }
        if matches!(verb, "pressiona" | "pressione" | "aperta")
            && matches!(domain, Some("button" | "input_button"))
        {
            return Some(command("press", domain, Some(target)));
        }
    }
    for (prefix, action, domain) in [
        ("adiciona ", "todo_add", "todo"),
        ("inclui ", "todo_add", "todo"),
        ("marca como concluido ", "todo_complete", "todo"),
    ] {
        if let Some(rest) = text.strip_prefix(prefix)
            && let Some((item, list)) = rest.rsplit_once(" na lista ")
        {
            let mut result = command(action, Some(domain), Some(list));
            result.parameters.value = Some(Value::String(item.into()));
            return Some(result);
        }
    }
    if let Some(list) = text.strip_prefix("o que tem na lista ") {
        return Some(command("todo_list", Some("todo"), Some(list)));
    }
    if let Some(rest) = text.strip_prefix("seleciona ")
        && let Some((value, target)) = rest.rsplit_once(" em ")
    {
        let mut result = command("select", domain, Some(target));
        result.parameters.value = Some(Value::String(value.into()));
        return Some(result);
    }
    if let Some(rest) = text
        .strip_prefix("coloca ")
        .or_else(|| text.strip_prefix("deixa "))
    {
        for (marker, action) in [
            (" no efeito ", "effect"),
            (" no modo ", "preset"),
            (" na direcao ", "direction"),
        ] {
            if let Some((target, value)) = rest.split_once(marker) {
                let action = if action == "preset" && domain == Some("climate") {
                    "hvac_mode"
                } else {
                    action
                };
                let mut result = command(action, domain, Some(target));
                result.parameters.value = Some(Value::String(value.into()));
                return Some(result);
            }
        }
    }
    if (text.starts_with("qual ")
        || text.starts_with("quanto ")
        || text.starts_with("quantos graus")
        || text.starts_with("ta quente")
        || text.starts_with("esta quente"))
        && (text.contains("temperatura")
            || text.contains("graus")
            || text.contains("quente")
            || text.starts_with("quanto ta "))
    {
        let mut result = command("query", Some("sensor"), None);
        result.domains.push("climate".into());
        result.parameters.metric = Some("temperature".into());
        result.device_class = Some("temperature".into());
        result.mention = query_area_tail(text);
        if text.contains("quente") {
            result.parameters.aggregate = Some("compare".into());
        }
        return Some(result);
    }
    if let Some(area) = text.strip_prefix("o que esta ligado ") {
        let mut result = command("query", None, Some(area));
        result.plural = true;
        result.parameters.aggregate = Some("filter".into());
        result.parameters.state_filter = Some("on".into());
        return Some(result);
    }
    if text.starts_with("o que eu tenho no calendario ") {
        let mut result = command("calendar", Some("calendar"), None);
        result.parameters.date = text
            .strip_prefix("o que eu tenho no calendario ")
            .map(str::to_owned);
        return Some(result);
    }
    if let Some(name) = text
        .strip_prefix("ativa o modo ")
        .or_else(|| text.strip_prefix("ativa a cena "))
    {
        return Some(command("activate", Some("scene"), Some(name)));
    }
    if text.starts_with("proximo canal")
        || text.starts_with("proxima canal")
        || text.starts_with("volta um canal")
    {
        let mut result = command("remote_key", Some("media_player"), None);
        if let Some((_, target)) = text.split_once(" da ").or_else(|| text.split_once(" do ")) {
            result.mention = Some(target.into());
        }
        result.parameters.value = Some(Value::String(
            if text.starts_with("volta") {
                "channel_down"
            } else {
                "channel_up"
            }
            .into(),
        ));
        return Some(result);
    }
    if let Some(value) = text.strip_prefix("coloca no canal ") {
        let mut result = command("channel", Some("media_player"), None);
        result.parameters.value = Some(Value::from(slots::number(value)?));
        return Some(result);
    }
    if let Some(value) = text.strip_prefix("aperta o botao ") {
        let mut result = command("remote_key", Some("remote"), None);
        result.parameters.value = Some(Value::String(value.into()));
        return Some(result);
    }
    if matches!(text, "volta" | "abre o menu") {
        let mut result = command("remote_key", Some("remote"), None);
        result.parameters.value = Some(Value::String(
            if text == "volta" { "back" } else { "menu" }.into(),
        ));
        return Some(result);
    }
    if let Some(value) = text.strip_prefix("muda para ") {
        let mut result = command("source", Some("media_player"), None);
        result.parameters.value = Some(Value::String(value.into()));
        return Some(result);
    }
    if let Some(value) = text.strip_prefix("abre o ")
        && ["youtube", "spotify", "netflix"].contains(&value)
    {
        let mut result = command("source", Some("media_player"), None);
        result.parameters.value = Some(Value::String(value.into()));
        return Some(result);
    }
    if text.starts_with("pausa o que esta tocando") {
        return Some(command("pause", Some("media_player"), None));
    }
    // Generic relative/absolute setters, also useful for user-named helpers.
    let relative = if text.starts_with("aumenta ")
        || text.starts_with("sobe ")
        || text.contains("mais forte")
        || text.contains("mais clara")
        || text.starts_with("abre mais ")
    {
        Some(1.0)
    } else if text.starts_with("diminui ")
        || text.starts_with("baixa ")
        || text.contains("mais fraca")
        || text.contains("mais devagar")
        || text.starts_with("esfria ")
    {
        Some(-1.0)
    } else {
        None
    };
    if let Some(sign) = relative {
        let domain = domain.or_else(|| text.contains("graus").then_some("climate"));
        let action = if text.contains("volume") {
            "volume"
        } else {
            match domain {
                Some("light") => "brightness",
                Some("fan") => "percentage",
                Some("climate") => "temperature",
                Some("cover") => "position",
                Some("number" | "input_number") => "number",
                _ => return None,
            }
        };
        let mut result = command(action, domain, None);
        let mut target = text.split_once(' ').map_or("", |(_, tail)| tail);
        for prefix in [
            "um pouco ",
            "o brilho da ",
            "a velocidade do ",
            "a temperatura do ",
            "o volume da ",
            "mais ",
        ] {
            if let Some(rest) = target.strip_prefix(prefix) {
                target = rest;
            }
        }
        for suffix in [" mais fraca", " mais forte", " mais devagar", " mais clara"] {
            target = target.strip_suffix(suffix).unwrap_or(target);
        }
        result.mention = Some(target.to_owned());
        if action == "volume" {
            if [
                "o volume",
                "volume",
                "o som",
                "som",
                "seu volume",
                "seu som",
            ]
            .contains(&target)
            {
                result.mention = None;
            }
            if target.contains("seu") || target.ends_with("aqui") {
                result.origin = true;
                result.mention = None;
            }
        }
        result.parameters.relative = Some(sign);
        if action == "temperature"
            && target.ends_with(" graus")
            && let Some(value) = slots::numeric_parameter(target)
        {
            result.mention = Some("isso".into());
            result.parameters.value = Some(Value::from(value));
        }
        for marker in [" mais ", " menos ", " em "] {
            if let Some((head, amount)) = target.split_once(marker)
                && let Some(value) = slots::numeric_parameter(amount)
            {
                result.mention = Some(head.into());
                result.parameters.relative = Some(sign);
                result.parameters.value = Some(Value::from(value));
            }
        }
        if text.starts_with("esfria ") || text.starts_with("agora ") {
            let amount = text
                .trim_start_matches("esfria ")
                .trim_start_matches("mais ");
            if let Some(value) = slots::numeric_parameter(amount) {
                result.mention = Some("isso".into());
                result.parameters.relative = Some(sign);
                result.parameters.value = Some(Value::from(value));
            }
        }
        return Some(result);
    }
    for prefix in ["coloca ", "deixa ", "ajusta ", "bota "] {
        if let Some(rest) = text.strip_prefix(prefix) {
            if let Some((target, value)) = rest
                .rsplit_once(" em ")
                .or_else(|| rest.rsplit_once(" para "))
            {
                let action = if domain == Some("climate") && target.contains("ventilador") {
                    "fan_mode"
                } else if value.ends_with("graus") {
                    "temperature"
                } else if value.ends_with("kelvin") {
                    "color_temperature"
                } else {
                    match domain {
                        Some("light") => "brightness",
                        Some("fan") => "percentage",
                        Some("cover") => "position",
                        Some("climate" | "water_heater") => "temperature",
                        Some("humidifier") => "humidity",
                        Some("number" | "input_number") => "number",
                        _ => "",
                    }
                };
                if !action.is_empty() {
                    let mut result = command(action, domain, Some(target));
                    if action == "fan_mode" {
                        result.mention = Some(
                            target
                                .trim_start_matches("o ventilador do ")
                                .trim_start_matches("a ventilacao do ")
                                .into(),
                        );
                        result.parameters.value = Some(Value::String(value.into()));
                    } else {
                        result.parameters.value = Some(slots::qualitative(value).map_or_else(
                            || slots::numeric_parameter(value).map(Value::from),
                            |kind| Some(Value::String(kind.into())),
                        )?);
                    }
                    return Some(result);
                }
            }
            for color in [
                "azul", "vermelha", "vermelho", "verde", "amarela", "amarelo", "branca", "branco",
                "laranja", "roxa", "roxo", "rosa",
            ] {
                if let Some(target) = rest.strip_suffix(&format!(" {color}")) {
                    let mut result = command("color", Some("light"), Some(target));
                    result.parameters.value = Some(Value::String(color.into()));
                    return Some(result);
                }
            }
            for (suffix, action) in [
                (" ligada", "turn_on"),
                (" ligado", "turn_on"),
                (" apagada", "turn_off"),
                (" desligado", "turn_off"),
            ] {
                if let Some(target) = rest.strip_suffix(suffix) {
                    return Some(command(action, domain, Some(target)));
                }
            }
        }
    }
    None
}

fn query_area_tail(text: &str) -> Option<String> {
    if text.contains("algum comodo") || text.contains("toda a casa") {
        return None;
    }
    for marker in [" no ", " na ", " do ", " da "] {
        if let Some((_, tail)) = text.rsplit_once(marker) {
            return Some(tail.into());
        }
    }
    text.ends_with("aqui").then(|| "aqui".into())
}

pub fn followup_target(text: &str) -> &str {
    ["o do ", "a da ", "o da ", "a do ", "do ", "da "]
        .iter()
        .find_map(|prefix| text.strip_prefix(prefix))
        .unwrap_or(text)
}

pub fn split_clauses(text: &str) -> Vec<String> {
    if [
        "qual ",
        "como ",
        "quanto ",
        "quantos ",
        "quantas ",
        "quais ",
        "onde ",
        "tem ",
        "o que ",
        "a ",
        "o ",
        "quando ",
        "que horas ",
    ]
    .iter()
    .any(|prefix| text.starts_with(prefix))
    {
        return vec![text.trim().to_owned()];
    }
    let text = text
        .replace(" mas deixa ", " e deixa ")
        .replace(" depois ", " e ");
    let mut clauses = Vec::new();
    let mut start = 0;
    for (position, _) in text.match_indices(" e ") {
        let next = position + 3;
        let tail = &text[next..];
        let verb = tail.split_whitespace().next().unwrap_or("");
        if [
            "liga", "ligue", "desliga", "desligue", "apaga", "apague", "acende", "acenda", "ativa",
            "coloca", "deixa", "diminui", "aumenta", "abre", "fecha", "pausa", "toca", "do", "da",
            "no", "na", "a", "o",
        ]
        .contains(&verb)
        {
            clauses.push(text[start..position].trim().to_owned());
            start = next;
        }
    }
    clauses.push(text[start..].trim().trim_start_matches("agora ").to_owned());
    clauses
}
