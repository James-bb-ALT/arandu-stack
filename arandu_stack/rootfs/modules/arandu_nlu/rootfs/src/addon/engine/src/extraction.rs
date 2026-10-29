//! Mention extraction: utterance plus snapshot reference to anchored mentions.
//!
//! Implements the ADR-0053 contract as a pure function. It performs no
//! matching, ranking, or disambiguation: spans, inheritance links, area
//! constraints, and explicitly-mentioned-but-unlinked references are emitted
//! with verifiable evidence, and resolution downstream decides. Any
//! structurally invalid input yields no extraction output at all, never a
//! partial mention list.
//!
//! Grammar reuse (single PT-BR grammar): verb segmentation, starter
//! detection, article/preposition stripping, and percentage-tail detection
//! are shared with the parser; area names come from the ER snapshot, never
//! from fused MLP alias lists.

use std::collections::BTreeSet;

use serde::{Deserialize, Serialize};

use crate::{
    ResolutionCatalog,
    model::{MAX_MENTIONS_PER_SEGMENT, MAX_TEXT_BYTES, MAX_TEXT_CHARS, MAX_TOTAL_MENTIONS},
    normalize::{AREA_PREPOSITIONS, normalize_with_spans, strip_article},
    parser::{effect_segments_spanned, percentage_range, starts_effect},
    resolution::{Snapshot, index_snapshot},
};

#[derive(Clone, Copy, Debug, Deserialize, Eq, PartialEq, Serialize)]
#[serde(rename_all = "snake_case")]
pub enum ConstraintKind {
    Area,
    Domain,
    Capability,
}

#[derive(Clone, Copy, Debug, Deserialize, Eq, PartialEq, Serialize)]
#[serde(tag = "kind", rename_all = "snake_case")]
pub enum ConstraintEvidence {
    MentionSubspan { span: [usize; 2] },
    Inherited { from_mention: usize },
}

#[derive(Clone, Debug, Deserialize, Eq, PartialEq, Serialize)]
#[serde(deny_unknown_fields)]
pub struct ExtractionConstraint {
    #[serde(rename = "type")]
    pub kind: ConstraintKind,
    pub value: String,
    pub evidence: ConstraintEvidence,
}

#[derive(Clone, Copy, Debug, Deserialize, Eq, PartialEq, Serialize)]
#[serde(rename_all = "snake_case")]
pub enum UnlinkedKind {
    Area,
    Domain,
    Capability,
}

#[derive(Clone, Debug, Deserialize, Eq, PartialEq, Serialize)]
#[serde(deny_unknown_fields)]
pub struct UnlinkedRef {
    pub kind: UnlinkedKind,
    pub text: String,
    pub span: [usize; 2],
}

#[derive(Clone, Copy, Debug, Deserialize, Eq, PartialEq, Serialize)]
#[serde(rename_all = "snake_case")]
pub enum InheritanceVia {
    Coordination,
}

#[derive(Clone, Debug, Deserialize, Eq, PartialEq, Serialize)]
#[serde(deny_unknown_fields)]
pub struct Inheritance {
    pub noun: String,
    pub noun_span: [usize; 2],
    pub from_mention: usize,
    pub via: InheritanceVia,
}

#[derive(Clone, Debug, Deserialize, Eq, PartialEq, Serialize)]
#[serde(deny_unknown_fields)]
pub struct Mention {
    pub id: usize,
    pub text: String,
    pub span: [usize; 2],
    pub constraints: Vec<ExtractionConstraint>,
    pub unlinked: Vec<UnlinkedRef>,
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub inherits: Option<Inheritance>,
}

#[derive(Clone, Debug, Deserialize, Eq, PartialEq, Serialize)]
#[serde(deny_unknown_fields)]
pub struct OperationSegment {
    pub verb_text: String,
    pub verb_span: [usize; 2],
    pub mentions: Vec<Mention>,
}

#[derive(Clone, Debug, Deserialize, Eq, PartialEq, Serialize)]
#[serde(deny_unknown_fields)]
pub struct MentionExtraction {
    pub text: String,
    pub catalog_id: String,
    pub generation: String,
    pub operation_segments: Vec<OperationSegment>,
}

struct IndexedAreas {
    entries: Vec<(String, Vec<String>)>,
}

fn find_word(haystack: &str, needle: &str, from: usize) -> Option<usize> {
    let mut search = from;
    while search + needle.len() <= haystack.len() {
        let found = haystack[search..].find(needle)? + search;
        let before = found == 0 || haystack.as_bytes()[found - 1] == b' ';
        let end = found + needle.len();
        let after = end == haystack.len() || haystack.as_bytes()[end] == b' ';
        if before && after {
            return Some(found);
        }
        search = found + 1;
    }
    None
}

#[must_use]
pub fn extract(text: &str, snapshot: &ResolutionCatalog) -> Option<MentionExtraction> {
    if text.is_empty()
        || text.len() > MAX_TEXT_BYTES
        || text.chars().count() > MAX_TEXT_CHARS
        || text.chars().any(char::is_control)
    {
        return None;
    }
    let indexed = index_snapshot(snapshot)?;
    let areas = IndexedAreas {
        entries: indexed
            .areas
            .iter()
            .map(|area| (area.area_id.clone(), area.normalized_names.clone()))
            .collect(),
    };
    let (normalized, map) = normalize_with_spans(text);
    let ranges = effect_segments_spanned(&normalized)?;
    let project = |start: usize, end: usize| -> Option<(usize, usize)> {
        project_range(&map, text, start, end)
    };
    let mut segments = Vec::with_capacity(ranges.len());
    let mut next_id = 0_usize;
    for (segment_start, segment_end) in ranges {
        let segment = &normalized[segment_start..segment_end];
        let verb_len = segment.find(' ').unwrap_or(segment.len());
        let (verb_start, verb_end) = project(segment_start, segment_start + verb_len)?;
        if !starts_effect(&normalized[segment_start..segment_start + verb_len]) {
            return None;
        }
        let rest_start = segment_start + verb_len + 1;
        if rest_start >= segment_end {
            return None;
        }
        let mut region_end = segment_end;
        if let Some((tail, _)) = percentage_range(&normalized[rest_start..segment_end]) {
            region_end = rest_start + tail;
            let trimmed = normalized[rest_start..region_end].trim_end();
            if trimmed.is_empty() {
                return None;
            }
            region_end = rest_start + trimmed.len();
        }
        let scope = MentionScope {
            normalized: &normalized,
            map: &map,
            text,
            areas: &areas,
            snapshot: &indexed,
            next_id,
        };
        let (mentions, advanced) = extract_mentions(scope, rest_start, region_end)?;
        next_id = advanced;
        segments.push(OperationSegment {
            verb_text: text[verb_start..verb_end].to_owned(),
            verb_span: [verb_start, verb_end],
            mentions,
        });
    }
    if next_id > MAX_TOTAL_MENTIONS {
        return None;
    }
    Some(MentionExtraction {
        text: text.to_owned(),
        catalog_id: snapshot.catalog_id.clone(),
        generation: snapshot.generation.clone(),
        operation_segments: segments,
    })
}

struct MentionScope<'n, 'c> {
    normalized: &'n str,
    map: &'n [usize],
    text: &'n str,
    areas: &'n IndexedAreas,
    snapshot: &'n Snapshot<'c>,
    next_id: usize,
}

fn extract_mentions(
    mut scope: MentionScope<'_, '_>,
    rest_start: usize,
    region_end: usize,
) -> Option<(Vec<Mention>, usize)> {
    let mut parts = Vec::new();
    let mut part_start = rest_start;
    for (delimiter, _) in scope.normalized[rest_start..region_end].match_indices(" e ") {
        let absolute = rest_start + delimiter;
        let (start, end) = trim_part(&scope.normalized[part_start..absolute], part_start)?;
        parts.push((start, end));
        part_start = absolute + 3;
    }
    let (start, end) = trim_part(&scope.normalized[part_start..region_end], part_start)?;
    parts.push((start, end));
    if parts.len() > MAX_MENTIONS_PER_SEGMENT {
        return None;
    }
    let mut mentions = Vec::with_capacity(parts.len());
    let mut donor: Option<(String, [usize; 2], usize)> = None;
    for (part_start, part_end) in parts {
        let stripped = strip_article(&scope.normalized[part_start..part_end]);
        let skipped = scope.normalized[part_start..part_end].len() - stripped.len();
        if stripped.is_empty() {
            return None;
        }
        let mention_start = part_start + skipped;
        let (text_start, text_end) = project_range(scope.map, scope.text, mention_start, part_end)?;
        let head_end = first_preposition(stripped).unwrap_or(stripped.len());
        let head = stripped[..head_end].trim_end();
        let rigid: Vec<(usize, usize)> = scope
            .snapshot
            .rigid_spans(&scope.normalized[mention_start..part_end])
            .into_iter()
            .map(|(start, end)| (mention_start + start, mention_start + end))
            .collect();
        let constraints = scan_constraints(
            scope.normalized,
            scope.map,
            scope.text,
            scope.areas,
            mention_start,
            part_end,
            &rigid,
        )?;
        let unlinked = scan_unlinked(
            scope.normalized,
            scope.map,
            scope.text,
            scope.areas,
            mention_start,
            part_end,
            &rigid,
        )?;
        let mention = if head.is_empty() {
            let (noun, noun_span, donor_id) = donor.clone()?;
            Mention {
                id: scope.next_id,
                text: scope.text[text_start..text_end].to_owned(),
                span: [text_start, text_end],
                constraints,
                unlinked,
                inherits: Some(Inheritance {
                    noun,
                    noun_span,
                    from_mention: donor_id,
                    via: InheritanceVia::Coordination,
                }),
            }
        } else {
            let head_start = mention_start;
            let head_end = mention_start + head.len();
            let (noun_start, noun_end) =
                project_range(scope.map, scope.text, head_start, head_end)?;
            if donor.is_none() {
                donor = Some((
                    scope.text[noun_start..noun_end].to_owned(),
                    [noun_start, noun_end],
                    scope.next_id,
                ));
            }
            Mention {
                id: scope.next_id,
                text: scope.text[text_start..text_end].to_owned(),
                span: [text_start, text_end],
                constraints,
                unlinked,
                inherits: None,
            }
        };
        scope.next_id += 1;
        mentions.push(mention);
    }
    Some((mentions, scope.next_id))
}

fn trim_part(slice: &str, base: usize) -> Option<(usize, usize)> {
    let trimmed = slice.trim();
    if trimmed.is_empty() {
        return None;
    }
    let leading = slice.len() - slice.trim_start().len();
    Some((base + leading, base + leading + trimmed.len()))
}

fn first_preposition(text: &str) -> Option<usize> {
    let mut best: Option<usize> = None;
    for candidate in AREA_PREPOSITIONS {
        let mut search = 0_usize;
        while search + candidate.len() <= text.len() {
            match text[search..].find(candidate) {
                Some(found) => {
                    let absolute = search + found;
                    if absolute == 0 || text.as_bytes()[absolute - 1] == b' ' {
                        best = Some(best.map_or(absolute, |current| current.min(absolute)));
                        break;
                    }
                    search = absolute + 1;
                }
                None => break,
            }
        }
    }
    best
}

fn scan_constraints(
    normalized: &str,
    map: &[usize],
    text: &str,
    areas: &IndexedAreas,
    start: usize,
    end: usize,
    preset: &[(usize, usize)],
) -> Option<Vec<ExtractionConstraint>> {
    let mut candidates: Vec<(String, String)> = Vec::new();
    for (area_id, names) in &areas.entries {
        for name in names {
            candidates.push((area_id.clone(), name.clone()));
        }
    }
    candidates.sort_by(|left, right| {
        right
            .1
            .len()
            .cmp(&left.1.len())
            .then_with(|| left.0.cmp(&right.0))
    });
    let mut consumed: Vec<(usize, usize)> = preset.to_vec();
    let mut constraints = Vec::new();
    let mut seen: BTreeSet<(String, (usize, usize))> = BTreeSet::new();
    for (area_id, name) in candidates {
        let mut search = start;
        while let Some(found) = find_word(&normalized[start..end], &name, search - start) {
            let absolute = start + found;
            let absolute_end = absolute + name.len();
            if consumed
                .iter()
                .any(|(used, used_end)| absolute < *used_end && *used < absolute_end)
            {
                search = absolute + 1;
                continue;
            }
            let (original_start, original_end) = project_range(map, text, absolute, absolute_end)?;
            if seen.insert((area_id.clone(), (original_start, original_end))) {
                consumed.push((absolute, absolute_end));
                constraints.push(ExtractionConstraint {
                    kind: ConstraintKind::Area,
                    value: area_id.clone(),
                    evidence: ConstraintEvidence::MentionSubspan {
                        span: [original_start, original_end],
                    },
                });
            }
            search = absolute + 1;
        }
    }
    constraints.sort_by(|left, right| {
        left.value
            .cmp(&right.value)
            .then_with(|| match (&left.evidence, &right.evidence) {
                (
                    ConstraintEvidence::MentionSubspan { span: left_span },
                    ConstraintEvidence::MentionSubspan { span: right_span },
                ) => left_span.cmp(right_span),
                _ => std::cmp::Ordering::Equal,
            })
    });
    Some(constraints)
}

fn scan_unlinked(
    normalized: &str,
    map: &[usize],
    text: &str,
    areas: &IndexedAreas,
    start: usize,
    end: usize,
    preset: &[(usize, usize)],
) -> Option<Vec<UnlinkedRef>> {
    let mut consumed: Vec<(usize, usize)> = preset.to_vec();
    for (_, names) in &areas.entries {
        for name in names {
            let mut search = start;
            while let Some(found) = find_word(&normalized[start..end], name, search - start) {
                consumed.push((start + found, start + found + name.len()));
                search = start + found + 1;
            }
        }
    }
    let mut unlinked = Vec::new();
    let mut seen: BTreeSet<(usize, usize)> = BTreeSet::new();
    for preposition in AREA_PREPOSITIONS {
        let mut search = start;
        while search + preposition.len() <= end {
            let found = match normalized[search..end].find(preposition) {
                Some(found) => search + found,
                None => break,
            };
            if found != start && normalized.as_bytes()[found - 1] != b' ' {
                search = found + 1;
                continue;
            }
            let phrase_start = found + preposition.len();
            let mut phrase_end = end;
            if let Some((delimiter, _)) = normalized[phrase_start..end].match_indices(" e ").next()
            {
                phrase_end = phrase_start + delimiter;
            }
            for candidate in AREA_PREPOSITIONS {
                if let Some(next) = normalized[phrase_start..phrase_end].find(candidate)
                    && (next == 0 || normalized.as_bytes()[phrase_start + next - 1] == b' ')
                {
                    phrase_end = phrase_start + next;
                    break;
                }
            }
            let trimmed = normalized[phrase_start..phrase_end].trim();
            if trimmed.is_empty() {
                search = found + 1;
                continue;
            }
            let leading = normalized[phrase_start..phrase_end].len()
                - normalized[phrase_start..phrase_end].trim_start().len();
            let absolute_start = phrase_start + leading;
            let absolute_end = absolute_start + trimmed.len();
            if consumed
                .iter()
                .any(|(used, used_end)| absolute_start < *used_end && *used < absolute_end)
            {
                search = found + 1;
                continue;
            }
            if starts_with_known_area(&normalized[absolute_start..absolute_end], areas) {
                search = found + 1;
                continue;
            }
            let (original_start, original_end) =
                project_range(map, text, absolute_start, absolute_end)?;
            if seen.insert((original_start, original_end)) {
                unlinked.push(UnlinkedRef {
                    kind: UnlinkedKind::Area,
                    text: text[original_start..original_end].to_owned(),
                    span: [original_start, original_end],
                });
            }
            search = found + 1;
        }
    }
    unlinked.sort_by_key(|entry| entry.span);
    Some(unlinked)
}

fn starts_with_known_area(phrase: &str, areas: &IndexedAreas) -> bool {
    for (_, names) in &areas.entries {
        for name in names {
            if phrase.len() >= name.len()
                && phrase.starts_with(name.as_str())
                && (phrase.len() == name.len() || phrase.as_bytes()[name.len()] == b' ')
            {
                return true;
            }
        }
    }
    false
}

fn project_range(map: &[usize], text: &str, start: usize, end: usize) -> Option<(usize, usize)> {
    if start >= end {
        return None;
    }
    let original_start = *map.get(start)?;
    // End at the next kept byte, or at the end of the last kept character:
    // trailing dropped bytes (collapsed whitespace, folded punctuation)
    // must never leak into the slice.
    let original_end = if end < map.len() {
        *map.get(end)?
    } else {
        let last = *map.get(end.checked_sub(1)?)?;
        last + text.get(last..)?.chars().next()?.len_utf8()
    };
    if original_start >= original_end {
        return None;
    }
    text.get(original_start..original_end)?;
    Some((original_start, original_end))
}

#[cfg(test)]
mod tests {
    use super::*;
    use crate::{Action, ResolutionArea, ResolutionCatalog, ResolutionEntity};

    fn area(area_id: &str, names: &[&str]) -> ResolutionArea {
        ResolutionArea {
            area_id: area_id.to_owned(),
            names: names.iter().map(|name| (*name).to_owned()).collect(),
        }
    }

    fn entity(
        registry_id: &str,
        entity_id: &str,
        area_id: &str,
        display: &str,
        aliases: &[&str],
    ) -> ResolutionEntity {
        ResolutionEntity {
            registry_id: registry_id.to_owned(),
            entity_id: entity_id.to_owned(),
            domain: "light".to_owned(),
            area_id: Some(area_id.to_owned()),
            display_name: display.to_owned(),
            aliases: aliases.iter().map(|alias| (*alias).to_owned()).collect(),
            capabilities: vec![Action::TurnOn, Action::TurnOff],
        }
    }

    fn snapshot() -> ResolutionCatalog {
        ResolutionCatalog {
            catalog_id: "test-mx".to_owned(),
            generation: "gen-001".to_owned(),
            areas: vec![
                area("area_sala", &["sala"]),
                area("area_quarto", &["quarto"]),
            ],
            entities: vec![
                entity(
                    "main",
                    "light.luz_sala",
                    "area_sala",
                    "luz da sala",
                    &["luz principal"],
                ),
                entity(
                    "lamp",
                    "light.abajur_sala",
                    "area_sala",
                    "abajur",
                    &["abajur da sala"],
                ),
            ],
        }
    }

    fn area_values(extraction: &MentionExtraction, segment: usize, mention: usize) -> Vec<&str> {
        extraction.operation_segments[segment].mentions[mention]
            .constraints
            .iter()
            .map(|constraint| constraint.value.as_str())
            .collect()
    }

    #[test]
    fn ellipsis_links_noun_and_keeps_local_spans() {
        let extraction =
            extract("Acenda a luz da sala e do quarto.", &snapshot()).expect("extract");
        assert_eq!(extraction.operation_segments.len(), 1);
        let mentions = &extraction.operation_segments[0].mentions;
        assert_eq!(mentions.len(), 2);
        assert_eq!(mentions[0].text, "luz da sala");
        assert_eq!(mentions[0].span, [9, 20]);
        assert_eq!(area_values(&extraction, 0, 0), ["area_sala"]);
        assert!(mentions[0].inherits.is_none());
        assert_eq!(mentions[1].text, "do quarto");
        assert_eq!(mentions[1].span, [23, 32]);
        let link = mentions[1].inherits.as_ref().expect("inherit");
        assert_eq!(link.noun.as_str(), "luz");
        assert_eq!(link.noun_span, [9, 12]);
        assert_eq!(link.from_mention, 0);
        assert_eq!(area_values(&extraction, 0, 1), ["area_quarto"]);
    }

    #[test]
    fn unknown_area_is_unlinked_not_dropped() {
        let extraction = extract("Acenda o abajur da copa.", &snapshot()).expect("extract");
        let mention = &extraction.operation_segments[0].mentions[0];
        assert!(mention.constraints.is_empty());
        assert_eq!(mention.unlinked.len(), 1);
        assert_eq!(mention.unlinked[0].kind, UnlinkedKind::Area);
        assert_eq!(mention.unlinked[0].text, "copa");
        let span = mention.unlinked[0].span;
        assert_eq!(&extraction.text[span[0]..span[1]], "copa");
    }

    #[test]
    fn headless_chain_without_donor_noun_fails_whole() {
        assert!(extract("Acenda da sala e do quarto.", &snapshot()).is_none());
    }

    #[test]
    fn inheritance_never_crosses_segments() {
        assert!(extract("Acenda a luz e ligue do quarto.", &snapshot()).is_none());
    }

    #[test]
    fn percentage_tail_is_excluded_from_mention() {
        let extraction =
            extract("Coloque o ventilador em 50 por cento.", &snapshot()).expect("extract");
        let mention = &extraction.operation_segments[0].mentions[0];
        assert_eq!(mention.text.as_str(), "ventilador");
        assert_eq!(mention.span, [10, 20]);
    }

    #[test]
    fn articles_do_not_leak_into_mention_spans() {
        let extraction = extract("Apague o abajur da sala.", &snapshot()).expect("extract");
        let mention = &extraction.operation_segments[0].mentions[0];
        assert_eq!(mention.text.as_str(), "abajur da sala");
        assert_eq!(mention.span, [9, 23]);
    }

    #[test]
    fn over_limit_parts_segments_and_bad_snapshots_fail() {
        assert!(
            extract(
                "Acenda a luz e o abajur e o ventilador e a sala e o quarto.",
                &snapshot()
            )
            .is_none()
        );
        assert!(extract("Qual e o estado do abajur?", &snapshot()).is_none());
        assert!(extract("Acenda a luz e desligue.", &snapshot()).is_none());
        let mut duplicated = snapshot();
        duplicated.entities.push(duplicated.entities[0].clone());
        assert!(extract("Acenda o abajur.", &duplicated).is_none());
    }

    #[test]
    fn external_ids_suppress_subword_area_reading() {
        let mut catalog = snapshot();
        catalog.entities.push(entity(
            "vip",
            "light.sala_vip",
            "area_quarto",
            "salao vip",
            &["vip"],
        ));
        let extraction = extract("Acenda light.sala_vip.", &catalog).expect("extract");
        let mention = &extraction.operation_segments[0].mentions[0];
        assert!(mention.constraints.is_empty());
        assert!(mention.unlinked.is_empty());
    }

    #[test]
    fn multibyte_mentions_keep_byte_spans() {
        let extraction = extract("Ligue a lâmpada da sala.", &snapshot()).expect("extract");
        let mention = &extraction.operation_segments[0].mentions[0];
        assert_eq!(mention.span, [8, 24]);
        assert_eq!(
            &extraction.text[mention.span[0]..mention.span[1]],
            mention.text
        );
    }
}
