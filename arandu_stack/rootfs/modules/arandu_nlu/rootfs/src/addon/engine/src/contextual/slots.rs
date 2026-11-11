//! Typed PT-BR numeric and duration extraction; qualitative values are explicit.
use crate::normalize::normalize as base_normalize;

#[must_use]
pub fn normalize(input: &str) -> String {
    let chars: Vec<char> = input.chars().collect();
    let mut text = String::with_capacity(input.len());
    for (i, ch) in chars.iter().enumerate() {
        if matches!(ch, ',' | '.')
            && i > 0
            && i + 1 < chars.len()
            && chars[i - 1].is_ascii_digit()
            && chars[i + 1].is_ascii_digit()
        {
            text.push_str(" decimal ");
        } else {
            text.push(*ch);
        }
    }
    base_normalize(&text)
}

#[must_use]
#[allow(clippy::too_many_lines)] // Explicit bounded Portuguese number lexicon.
pub fn number(text: &str) -> Option<f64> {
    let value = text.trim();
    if value.ends_with(" e") || value.contains(" e e ") {
        return None;
    }
    if let Some((integer, decimal)) = value.split_once(" decimal ") {
        let integer = number(integer)?;
        if decimal.is_empty() || !decimal.bytes().all(|ch| ch.is_ascii_digit()) {
            return None;
        }
        return format!("{integer}.{decimal}")
            .parse::<f64>()
            .ok()
            .filter(|n| n.is_finite());
    }
    if let Ok(value) = value.parse::<f64>() {
        return value.is_finite().then_some(value);
    }
    if matches!(value, "metade" | "meio") {
        return Some(50.0);
    }
    if matches!(value, "baixa" | "baixo" | "velocidade baixa") {
        return Some(25.0);
    }
    if matches!(value, "media" | "medio" | "velocidade media") {
        return Some(50.0);
    }
    if matches!(value, "alta" | "alto" | "velocidade alta") {
        return Some(75.0);
    }
    if matches!(
        value,
        "maximo" | "maxima" | "no maximo" | "velocidade maxima"
    ) {
        return Some(100.0);
    }
    if matches!(
        value,
        "minimo" | "minima" | "no minimo" | "velocidade minima"
    ) {
        return Some(1.0);
    }
    let mut total: f64 = 0.0;
    let mut any = false;
    let mut last_tens = false;
    for token in value.split_whitespace() {
        if token == "e" && last_tens {
            continue;
        }
        let n = match token {
            "zero" => 0,
            "um" | "uma" => 1,
            "dois" | "duas" => 2,
            "tres" => 3,
            "quatro" => 4,
            "cinco" => 5,
            "seis" => 6,
            "sete" => 7,
            "oito" => 8,
            "nove" => 9,
            "dez" => 10,
            "onze" => 11,
            "doze" => 12,
            "treze" => 13,
            "quatorze" | "catorze" => 14,
            "quinze" => 15,
            "dezesseis" => 16,
            "dezessete" => 17,
            "dezoito" => 18,
            "dezenove" => 19,
            "vinte" => 20,
            "trinta" => 30,
            "quarenta" => 40,
            "cinquenta" => 50,
            "sessenta" => 60,
            "setenta" => 70,
            "oitenta" => 80,
            "noventa" => 90,
            "cem" | "cento" => 100,
            "duzentos" => 200,
            "trezentos" => 300,
            "quatrocentos" => 400,
            "quinhentos" => 500,
            "seiscentos" => 600,
            "setecentos" => 700,
            "oitocentos" => 800,
            "novecentos" => 900,
            "mil" => {
                total = total.max(1.0) * 1_000.0;
                any = true;
                last_tens = true;
                continue;
            }
            _ => return None,
        };
        if any && !last_tens && total < 100.0 {
            return None;
        }
        total += f64::from(n);
        any = true;
        last_tens = n >= 20;
    }
    any.then_some(total)
}

#[must_use]
pub fn duration(text: &str) -> Option<f64> {
    let text = text.trim().trim_start_matches("de ");
    if text.ends_with(" e") {
        return None;
    }
    let mut total = 0.0;
    let mut part = String::new();
    for word in text.split_whitespace() {
        let multiplier = match word {
            "segundo" | "segundos" => 1.0,
            "minuto" | "minutos" => 60.0,
            "hora" | "horas" => 3_600.0,
            _ => {
                if word != "e" || !part.is_empty() {
                    part.push_str(word);
                    part.push(' ');
                }
                continue;
            }
        };
        total += number(part.trim())? * multiplier;
        part.clear();
    }
    (part.is_empty() && total > 0.0 && total <= 604_800.0).then_some(total)
}

#[must_use]
pub fn numeric_parameter(text: &str) -> Option<f64> {
    let text = text.trim();
    for suffix in [
        " por cento",
        " graus celsius",
        " graus",
        " grau",
        " kelvin",
        " por cento de velocidade",
    ] {
        if let Some(value) = text.strip_suffix(suffix) {
            return number(value);
        }
    }
    number(text)
}

#[must_use]
pub fn qualitative(text: &str) -> Option<&'static str> {
    match text.trim() {
        "minimo" | "minima" | "no minimo" | "velocidade minima" => Some("minimum"),
        "maximo" | "maxima" | "no maximo" | "velocidade maxima" => Some("maximum"),
        "metade" => Some("half"),
        "baixa" | "baixo" | "velocidade baixa" => Some("low"),
        "media" | "medio" | "velocidade media" => Some("medium"),
        "alta" | "alto" | "velocidade alta" => Some("high"),
        _ => None,
    }
}

#[must_use]
pub fn original_slot(source: &str, value: &str) -> Option<String> {
    let (normalized, spans) = crate::normalize::normalize_with_spans(source);
    let value = base_normalize(value);
    if value.is_empty() {
        return None;
    }
    let (start, matched) = normalized.match_indices(&value).find(|(start, matched)| {
        (*start == 0 || normalized.as_bytes()[start - 1] == b' ')
            && (start + matched.len() == normalized.len()
                || normalized.as_bytes()[start + matched.len()] == b' ')
    })?;
    let first = *spans.get(start)?;
    let last = *spans.get(start + matched.len() - 1)?;
    let mut end = last + source.get(last..)?.chars().next()?.len_utf8();
    while let Some(ch) = source.get(end..)?.chars().next() {
        if !unicode_normalization::char::is_combining_mark(ch) {
            break;
        }
        end += ch.len_utf8();
    }
    source.get(first..end).map(str::to_owned)
}

#[must_use]
pub fn clock_time(text: &str) -> Option<String> {
    let mut text = text.trim();
    for marker in [" para as ", " pra as ", " pras ", " para a ", " pra a ", " as "] {
        if let Some((_, tail)) = text.split_once(marker) {
            if let Some(t) = clock_time(tail) {
                return Some(t);
            }
        }
    }
    for prefix in [
        "para as ", "para a ", "pra as ", "pra a ", "pras ", "as ", "a ", "para ", "pra ",
    ] {
        if let Some(rest) = text.strip_prefix(prefix) {
            text = rest.trim();
            break;
        }
    }
    let is_afternoon = text.contains("da tarde") || text.contains("da noite");
    let is_morning = text.contains("da manha") || text.contains("da madrugada");
    for suffix in [
        " da tarde",
        " da noite",
        " da manha",
        " da madrugada",
        " da manhazinha",
    ] {
        if let Some(rest) = text.strip_suffix(suffix) {
            text = rest.trim();
            break;
        }
    }
    let is_half = text.ends_with(" e meia");
    if is_half {
        text = text.strip_suffix(" e meia")?.trim();
    }
    for suffix in [" horas", " hora"] {
        if let Some(rest) = text.strip_suffix(suffix) {
            text = rest.trim();
            break;
        }
    }
    let (mut hours, mut minutes) = if text == "meio dia" {
        (12, 0)
    } else if text == "meia noite" {
        (0, 0)
    } else if let Some((h_str, m_str)) = text.split_once(':') {
        let h: u32 = h_str.trim().parse().ok()?;
        let m: u32 = m_str.trim().parse().ok()?;
        (h, m)
    } else if let Some((h_str, m_str)) = text.split_once('h') {
        let h: u32 = h_str.trim().parse().ok()?;
        let m: u32 = if m_str.trim().is_empty() {
            0
        } else {
            m_str.trim().parse().ok()?
        };
        (h, m)
    } else if let Some((h_str, m_str)) = text.split_once(' ')
        && let (Ok(h), Ok(m)) = (h_str.trim().parse::<u32>(), m_str.trim().parse::<u32>())
        && h < 24
        && m < 60
    {
        (h, m)
    } else if let Some(n) = number(text) {
        let h = n as u32;
        (h, 0)
    } else {
        return None;
    };
    if is_half {
        minutes = 30;
    }
    if is_afternoon && hours > 0 && hours < 12 {
        hours += 12;
    } else if is_morning && hours == 12 {
        hours = 0;
    }
    if hours > 23 || minutes > 59 {
        return None;
    }
    Some(format!("{hours:02}:{minutes:02}"))
}

#[must_use]
pub fn relative_delay(text: &str) -> Option<f64> {
    let mut text = text.trim();
    for marker in [" depois de ", " apos ", " daqui a ", " daqui ", " em "] {
        if let Some((_, tail)) = text.split_once(marker) {
            if let Some(d) = relative_delay(tail) {
                return Some(d);
            }
        }
    }
    for prefix in ["depois de ", "apos ", "daqui a ", "daqui ", "em ", "por "] {
        if let Some(rest) = text.strip_prefix(prefix) {
            text = rest.trim();
            break;
        }
    }
    duration(text)
}

#[must_use]
pub fn recurrence(text: &str) -> Option<String> {
    let text = text.trim();
    if matches!(
        text,
        "todo dia" | "todos os dias" | "diariamente" | "diario" | "diaria"
    ) {
        Some("daily".into())
    } else if matches!(
        text,
        "de segunda a sexta"
            | "segunda a sexta"
            | "dias uteis"
            | "dias de semana"
            | "segunda a sexta feira"
    ) {
        Some("weekdays".into())
    } else if matches!(
        text,
        "fins de semana" | "fim de semana" | "sabado e domingo" | "sabados e domingos"
    ) {
        Some("weekends".into())
    } else {
        None
    }
}

