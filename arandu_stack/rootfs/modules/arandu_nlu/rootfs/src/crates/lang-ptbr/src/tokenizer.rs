use crate::{MAX_TOKENS, NormalizedSpan, NormalizedText, TextError};
use core::fmt;
use nlu_core::Utf8Span;
use std::collections::BTreeSet;
use unicode_segmentation::UnicodeSegmentation;

const UD_RULE_DATA: &str = include_str!("../../../data/tokenization/p05/rules/ud-portuguese.tsv");
const CLDR_RULE_DATA: &str =
    include_str!("../../../data/tokenization/p05/rules/cldr-portuguese.tsv");

const UNICODE_WORD_RULE: TokenRule = TokenRule::new("unicode17.word-boundary");
const UNICODE_PUNCTUATION_RULE: TokenRule =
    TokenRule::new("unicode17.general-category-ascii-punctuation");
const CLDR_NUMBER_RULE: TokenRule = TokenRule::new("cldr48.pt.latn-decimal-comma");
const CLDR_TIME_RULE: TokenRule = TokenRule::new("cldr48.pt.hh-mm");
const UNSUPPORTED_RULE: TokenRule = TokenRule::new("policy.unsupported-preserve");

const DEFAULT_RULE_TABLES: [RuleTable; 2] = [
    RuleTable {
        prefix: "ud.pt.",
        contents: UD_RULE_DATA,
    },
    RuleTable {
        prefix: "cldr48.pt.",
        contents: CLDR_RULE_DATA,
    },
];

#[derive(Clone, Copy, Debug, Eq, Ord, PartialEq, PartialOrd)]
pub enum TokenClass {
    Number,
    Punctuation,
    Time,
    Unit,
    Contraction,
    Clitic,
    Abbreviation,
    Multiunit,
    Unknown,
}

impl TokenClass {
    #[must_use]
    pub const fn code(self) -> &'static str {
        match self {
            Self::Number => "number",
            Self::Punctuation => "punctuation",
            Self::Time => "time",
            Self::Unit => "unit",
            Self::Contraction => "contraction",
            Self::Clitic => "clitic",
            Self::Abbreviation => "abbreviation",
            Self::Multiunit => "multiunit",
            Self::Unknown => "unknown",
        }
    }
}

#[derive(Clone, Copy, Debug, Eq, Ord, PartialEq, PartialOrd)]
pub enum BoundaryOperation {
    UnicodeBoundary,
    PunctuationBoundary,
    ExactPreserve,
    ExactMerge,
    LogicalDecomposition,
    UnsupportedPreserve,
}

impl BoundaryOperation {
    #[must_use]
    pub const fn code(self) -> &'static str {
        match self {
            Self::UnicodeBoundary => "unicode_boundary",
            Self::PunctuationBoundary => "punctuation_boundary",
            Self::ExactPreserve => "exact_preserve",
            Self::ExactMerge => "exact_merge",
            Self::LogicalDecomposition => "logical_decomposition",
            Self::UnsupportedPreserve => "unsupported_preserve",
        }
    }
}

#[derive(Clone, Copy, Eq, Ord, PartialEq, PartialOrd)]
pub struct TokenRule(&'static str);

impl TokenRule {
    const fn new(id: &'static str) -> Self {
        Self(id)
    }

    #[must_use]
    pub const fn id(self) -> &'static str {
        self.0
    }
}

impl fmt::Debug for TokenRule {
    fn fmt(&self, formatter: &mut fmt::Formatter<'_>) -> fmt::Result {
        formatter.debug_tuple("TokenRule").field(&self.0).finish()
    }
}

#[derive(Clone, Eq, PartialEq)]
pub struct LogicalPart {
    value: &'static str,
    normalized_projection: NormalizedSpan,
    original_projection: Utf8Span,
    rule: TokenRule,
}

impl LogicalPart {
    #[must_use]
    pub const fn value(&self) -> &'static str {
        self.value
    }

    #[must_use]
    pub const fn normalized_projection(&self) -> &NormalizedSpan {
        &self.normalized_projection
    }

    #[must_use]
    pub const fn original_projection(&self) -> &Utf8Span {
        &self.original_projection
    }

    #[must_use]
    pub const fn rule(&self) -> TokenRule {
        self.rule
    }
}

impl fmt::Debug for LogicalPart {
    fn fmt(&self, formatter: &mut fmt::Formatter<'_>) -> fmt::Result {
        formatter
            .debug_struct("LogicalPart")
            .field("normalized_projection", &self.normalized_projection)
            .field("original_projection", &self.original_projection)
            .field("rule", &self.rule)
            .finish_non_exhaustive()
    }
}

#[derive(Clone, Eq, PartialEq)]
pub struct Token {
    normalized_span: NormalizedSpan,
    original_span: Utf8Span,
    class: TokenClass,
    operation: BoundaryOperation,
    rule: TokenRule,
    parts: Box<[LogicalPart]>,
}

impl Token {
    #[must_use]
    pub const fn normalized_span(&self) -> &NormalizedSpan {
        &self.normalized_span
    }

    #[must_use]
    pub const fn original_span(&self) -> &Utf8Span {
        &self.original_span
    }

    #[must_use]
    pub const fn class(&self) -> TokenClass {
        self.class
    }

    #[must_use]
    pub const fn operation(&self) -> BoundaryOperation {
        self.operation
    }

    #[must_use]
    pub const fn rule(&self) -> TokenRule {
        self.rule
    }

    #[must_use]
    pub fn parts(&self) -> &[LogicalPart] {
        &self.parts
    }

    pub fn normalized_slice<'a>(&self, text: &'a NormalizedText) -> Result<&'a str, TextError> {
        self.normalized_span.slice(text)
    }

    pub fn original_slice<'a>(&self, text: &'a NormalizedText) -> Result<&'a str, TextError> {
        Ok(self.original_span.slice(text.original())?)
    }
}

impl fmt::Debug for Token {
    fn fmt(&self, formatter: &mut fmt::Formatter<'_>) -> fmt::Result {
        formatter
            .debug_struct("Token")
            .field("normalized_span", &self.normalized_span)
            .field("original_span", &self.original_span)
            .field("class", &self.class)
            .field("operation", &self.operation)
            .field("rule", &self.rule)
            .field("part_count", &self.parts.len())
            .finish()
    }
}

#[derive(Clone, Eq, PartialEq)]
pub struct Tokenization {
    tokens: Box<[Token]>,
}

impl Tokenization {
    #[must_use]
    pub fn tokens(&self) -> &[Token] {
        &self.tokens
    }

    #[must_use]
    pub fn len(&self) -> usize {
        self.tokens.len()
    }

    #[must_use]
    pub fn is_empty(&self) -> bool {
        self.tokens.is_empty()
    }
}

impl fmt::Debug for Tokenization {
    fn fmt(&self, formatter: &mut fmt::Formatter<'_>) -> fmt::Result {
        formatter
            .debug_struct("Tokenization")
            .field("token_count", &self.tokens.len())
            .finish()
    }
}

impl NormalizedText {
    pub fn tokenize(&self) -> Result<Tokenization, TextError> {
        tokenize_with_tables(self, &DEFAULT_RULE_TABLES)
    }
}

#[derive(Clone, Copy)]
struct RuleTable {
    prefix: &'static str,
    contents: &'static str,
}

#[derive(Clone, Copy)]
struct ExactRule {
    id: &'static str,
    surface: &'static str,
    class: TokenClass,
    parts: &'static str,
}

#[derive(Clone, Copy)]
struct Segment {
    start: usize,
    end: usize,
}

#[derive(Clone, Copy)]
struct TokenSpec {
    start: usize,
    end: usize,
    class: TokenClass,
    operation: BoundaryOperation,
    rule: TokenRule,
    part_values: &'static str,
}

fn tokenize_with_tables(
    text: &NormalizedText,
    rule_tables: &[RuleTable],
) -> Result<Tokenization, TextError> {
    validate_rule_tables(rule_tables)?;
    let input = text.as_str();
    if input.is_empty() {
        return Ok(Tokenization {
            tokens: Box::default(),
        });
    }

    let segments = token_segments(input);
    let mut boundaries: Vec<_> = segments.iter().map(|segment| segment.start).collect();
    boundaries.push(input.len());

    let mut tokens = Vec::new();
    let mut segment_index = 0_usize;
    while segment_index < segments.len() {
        let segment = segments[segment_index];
        let value = &input[segment.start..segment.end];
        if value.chars().all(char::is_whitespace) {
            segment_index += 1;
            continue;
        }

        let connector_end = unsupported_connector_end(input, segment.start).map(|end| {
            match boundaries.binary_search(&end) {
                Ok(_) => end,
                Err(index) => boundaries[index],
            }
        });
        if let Some(rule) = select_exact_rule(
            input,
            segment.start,
            &boundaries,
            connector_end,
            rule_tables,
        )? {
            let end = segment.start + rule.surface.len();
            let operation = if rule.parts.is_empty() {
                if rule.surface.split_word_bound_indices().count() > 1 {
                    BoundaryOperation::ExactMerge
                } else {
                    BoundaryOperation::ExactPreserve
                }
            } else {
                BoundaryOperation::LogicalDecomposition
            };
            push_token(
                &mut tokens,
                text,
                TokenSpec {
                    start: segment.start,
                    end,
                    class: rule.class,
                    operation,
                    rule: TokenRule::new(rule.id),
                    part_values: rule.parts,
                },
            )?;
            segment_index = advance_segments(&segments, segment_index, end)?;
            continue;
        }

        if let Some(connector_end) = connector_end {
            if let Some(end) = source_pattern_end(input, segment.start, connector_end, is_cldr_time)
            {
                push_token(
                    &mut tokens,
                    text,
                    TokenSpec {
                        start: segment.start,
                        end,
                        class: TokenClass::Time,
                        operation: BoundaryOperation::ExactMerge,
                        rule: CLDR_TIME_RULE,
                        part_values: "",
                    },
                )?;
                segment_index = advance_segments(&segments, segment_index, end)?;
                continue;
            }

            if let Some(end) =
                source_pattern_end(input, segment.start, connector_end, is_cldr_number)
            {
                push_token(
                    &mut tokens,
                    text,
                    TokenSpec {
                        start: segment.start,
                        end,
                        class: TokenClass::Number,
                        operation: BoundaryOperation::ExactPreserve,
                        rule: CLDR_NUMBER_RULE,
                        part_values: "",
                    },
                )?;
                segment_index = advance_segments(&segments, segment_index, end)?;
                continue;
            }

            push_token(
                &mut tokens,
                text,
                TokenSpec {
                    start: segment.start,
                    end: connector_end,
                    class: TokenClass::Unknown,
                    operation: BoundaryOperation::UnsupportedPreserve,
                    rule: UNSUPPORTED_RULE,
                    part_values: "",
                },
            )?;
            segment_index = advance_segments(&segments, segment_index, connector_end)?;
            continue;
        }

        if is_cldr_number(value) {
            push_token(
                &mut tokens,
                text,
                TokenSpec {
                    start: segment.start,
                    end: segment.end,
                    class: TokenClass::Number,
                    operation: BoundaryOperation::ExactPreserve,
                    rule: CLDR_NUMBER_RULE,
                    part_values: "",
                },
            )?;
        } else if value.chars().all(is_admitted_ascii_punctuation) {
            for (relative_start, grapheme) in value.grapheme_indices(true) {
                let start = segment.start + relative_start;
                push_token(
                    &mut tokens,
                    text,
                    TokenSpec {
                        start,
                        end: start + grapheme.len(),
                        class: TokenClass::Punctuation,
                        operation: BoundaryOperation::PunctuationBoundary,
                        rule: UNICODE_PUNCTUATION_RULE,
                        part_values: "",
                    },
                )?;
            }
        } else {
            push_token(
                &mut tokens,
                text,
                TokenSpec {
                    start: segment.start,
                    end: segment.end,
                    class: TokenClass::Unknown,
                    operation: BoundaryOperation::UnicodeBoundary,
                    rule: UNICODE_WORD_RULE,
                    part_values: "",
                },
            )?;
        }
        segment_index += 1;
    }

    Ok(Tokenization {
        tokens: tokens.into_boxed_slice(),
    })
}

fn validate_rule_tables(rule_tables: &[RuleTable]) -> Result<(), TextError> {
    let mut ids = BTreeSet::new();
    let mut surfaces = BTreeSet::new();
    for table in rule_tables {
        for line in rule_lines(table.contents) {
            let rule = parse_rule(line)?;
            if !rule.id.starts_with(table.prefix)
                || rule.surface.chars().any(char::is_whitespace)
                || !ids.insert(rule.id)
                || !surfaces.insert(rule.surface)
            {
                return Err(TextError::TokenizationInvariant);
            }
            let part_count = if rule.parts.is_empty() {
                0
            } else {
                rule.parts.split('|').count()
            };
            if matches!(rule.class, TokenClass::Contraction | TokenClass::Clitic) {
                if !(2..=3).contains(&part_count)
                    || rule.parts.split('|').any(|part| part.is_empty())
                {
                    return Err(TextError::TokenizationInvariant);
                }
            } else if part_count != 0 {
                return Err(TextError::TokenizationInvariant);
            }
        }
    }
    Ok(())
}

fn rule_lines(contents: &'static str) -> impl Iterator<Item = &'static str> {
    contents
        .lines()
        .filter(|line| !line.is_empty() && !line.starts_with('#'))
}

fn parse_rule(line: &'static str) -> Result<ExactRule, TextError> {
    let mut fields = line.split('\t');
    let id = fields.next().ok_or(TextError::TokenizationInvariant)?;
    let surface = fields.next().ok_or(TextError::TokenizationInvariant)?;
    let class = match fields.next().ok_or(TextError::TokenizationInvariant)? {
        "time" => TokenClass::Time,
        "unit" => TokenClass::Unit,
        "contraction" => TokenClass::Contraction,
        "clitic" => TokenClass::Clitic,
        "abbreviation" => TokenClass::Abbreviation,
        "multiunit" => TokenClass::Multiunit,
        _ => return Err(TextError::TokenizationInvariant),
    };
    let parts = fields.next().ok_or(TextError::TokenizationInvariant)?;
    if fields.next().is_some() || id.is_empty() || surface.is_empty() {
        return Err(TextError::TokenizationInvariant);
    }
    Ok(ExactRule {
        id,
        surface,
        class,
        parts,
    })
}

fn select_exact_rule(
    input: &str,
    start: usize,
    boundaries: &[usize],
    connector_end: Option<usize>,
    rule_tables: &[RuleTable],
) -> Result<Option<ExactRule>, TextError> {
    let mut selected: Option<ExactRule> = None;
    for table in rule_tables {
        for line in rule_lines(table.contents) {
            let candidate = parse_rule(line)?;
            let end = start + candidate.surface.len();
            if !input[start..].starts_with(candidate.surface)
                || boundaries.binary_search(&end).is_err()
                || connector_end.is_some_and(|complete_end| {
                    !has_only_terminal_punctuation(input, end, complete_end)
                })
            {
                continue;
            }
            let replace = selected.is_none_or(|current| {
                candidate.surface.len() > current.surface.len()
                    || (candidate.surface.len() == current.surface.len()
                        && candidate.id < current.id)
            });
            if replace {
                selected = Some(candidate);
            }
        }
    }
    Ok(selected)
}

fn advance_segments(
    segments: &[Segment],
    mut index: usize,
    end: usize,
) -> Result<usize, TextError> {
    while index < segments.len() && segments[index].end <= end {
        index += 1;
    }
    if index < segments.len() && segments[index].start < end {
        return Err(TextError::TokenizationInvariant);
    }
    Ok(index)
}

fn token_segments(input: &str) -> Vec<Segment> {
    let mut segments = Vec::new();
    for (segment_start, value) in input.split_word_bound_indices() {
        let mut part_start = segment_start;
        for (relative, character) in value.char_indices() {
            if character != '_' {
                continue;
            }
            let punctuation_start = segment_start + relative;
            if part_start < punctuation_start {
                segments.push(Segment {
                    start: part_start,
                    end: punctuation_start,
                });
            }
            let punctuation_end = punctuation_start + character.len_utf8();
            segments.push(Segment {
                start: punctuation_start,
                end: punctuation_end,
            });
            part_start = punctuation_end;
        }
        let segment_end = segment_start + value.len();
        if part_start < segment_end {
            segments.push(Segment {
                start: part_start,
                end: segment_end,
            });
        }
    }
    segments
}

fn push_token(
    tokens: &mut Vec<Token>,
    text: &NormalizedText,
    spec: TokenSpec,
) -> Result<(), TextError> {
    if tokens.len() >= MAX_TOKENS {
        return Err(TextError::TokenLimit {
            limit: MAX_TOKENS as u32,
        });
    }
    let normalized_span = text.span(spec.start as u64, spec.end as u64)?;
    let original_span = text.to_original_span(&normalized_span)?;
    let parts = if spec.part_values.is_empty() {
        Box::default()
    } else {
        spec.part_values
            .split('|')
            .map(|value| LogicalPart {
                value,
                normalized_projection: normalized_span.clone(),
                original_projection: original_span.clone(),
                rule: spec.rule,
            })
            .collect::<Vec<_>>()
            .into_boxed_slice()
    };
    tokens.push(Token {
        normalized_span,
        original_span,
        class: spec.class,
        operation: spec.operation,
        rule: spec.rule,
        parts,
    });
    Ok(())
}

fn unsupported_connector_end(input: &str, start: usize) -> Option<usize> {
    let mut saw_word = false;
    let mut saw_connector = false;
    let mut saw_unsupported_connector = false;
    let mut pending_connector = false;
    let mut connected = false;
    let mut end = start;

    for (relative, character) in input[start..].char_indices() {
        if character.is_alphanumeric() {
            if pending_connector {
                connected = true;
            }
            saw_word = true;
            pending_connector = false;
            end = start + relative + character.len_utf8();
        } else if is_connector_character(character) {
            saw_connector = true;
            saw_unsupported_connector |= character == '+';
            pending_connector = true;
            end = start + relative + character.len_utf8();
        } else {
            break;
        }
    }
    (saw_word && saw_connector && (connected || saw_unsupported_connector)).then_some(end)
}

fn is_connector_character(character: char) -> bool {
    is_admitted_ascii_punctuation(character) || character == '+'
}

fn has_only_terminal_punctuation(input: &str, start: usize, end: usize) -> bool {
    start <= end && input[start..end].chars().all(is_admitted_ascii_punctuation)
}

fn source_pattern_end(
    input: &str,
    start: usize,
    mut end: usize,
    accepts: fn(&str) -> bool,
) -> Option<usize> {
    loop {
        if accepts(&input[start..end]) {
            return Some(end);
        }
        let trailing = input[start..end].chars().next_back()?;
        if !is_admitted_ascii_punctuation(trailing) {
            return None;
        }
        end -= trailing.len_utf8();
        if end == start {
            return None;
        }
    }
}

fn is_cldr_number(value: &str) -> bool {
    let mut fields = value.split(',');
    let integer = fields.next().unwrap_or_default();
    let fraction = fields.next();
    !integer.is_empty()
        && integer.bytes().all(|byte| byte.is_ascii_digit())
        && fraction.is_none_or(|digits| {
            !digits.is_empty() && digits.bytes().all(|byte| byte.is_ascii_digit())
        })
        && fields.next().is_none()
}

fn is_cldr_time(value: &str) -> bool {
    let bytes = value.as_bytes();
    bytes.len() == 5
        && bytes[0].is_ascii_digit()
        && bytes[1].is_ascii_digit()
        && bytes[2] == b':'
        && bytes[3].is_ascii_digit()
        && bytes[4].is_ascii_digit()
}

fn is_admitted_ascii_punctuation(character: char) -> bool {
    matches!(
        character,
        '!' | '"'
            | '#'
            | '%'
            | '&'
            | '\''
            | '('
            | ')'
            | '*'
            | ','
            | '-'
            | '.'
            | '/'
            | ':'
            | ';'
            | '?'
            | '@'
            | '['
            | '\\'
            | ']'
            | '_'
            | '{'
            | '}'
    )
}

#[cfg(test)]
mod tests {
    use super::*;

    const UD_UPSTREAM: &str =
        include_str!("../../../data/tokenization/p05/ud-docs-bdd95cf/index.md");
    const UD_PRON_UPSTREAM: &str =
        include_str!("../../../data/tokenization/p05/ud-docs-bdd95cf/PRON.md");
    const CLDR_UPSTREAM: &str = include_str!("../../../data/tokenization/p05/cldr-48/pt.xml");
    const CLDR_NUMBERING_SYSTEMS: &str =
        include_str!("../../../data/tokenization/p05/cldr-48/numberingSystems.xml");
    const UNICODE_DATA: &str = include_str!("../../../data/unicode/17.0.0/ucd/UnicodeData.txt");
    const WORD_BREAK_TEST: &str =
        include_str!("../../../data/unicode/17.0.0/ucd/auxiliary/WordBreakTest.txt");

    fn surfaces<'a>(tokenization: &'a Tokenization, text: &'a NormalizedText) -> Vec<&'a str> {
        tokenization
            .tokens()
            .iter()
            .map(|token| {
                token
                    .normalized_slice(text)
                    .expect("same normalized source")
            })
            .collect()
    }

    #[test]
    fn source_allowlists_cover_every_required_exact_class() {
        let input = [
            "do",
            "pelo",
            "dele",
            "no",
            "contar-lhe-ei",
            "sentem-se",
            "i.e.",
            "quilômetro",
            "km/h",
        ]
        .join(" ");
        let text = NormalizedText::new(input).expect("admitted source examples");
        let tokenization = text.tokenize().expect("source examples tokenize");
        assert_eq!(tokenization.len(), 9);
        assert_eq!(
            tokenization
                .tokens()
                .iter()
                .map(Token::class)
                .collect::<Vec<_>>(),
            [
                TokenClass::Contraction,
                TokenClass::Contraction,
                TokenClass::Contraction,
                TokenClass::Contraction,
                TokenClass::Clitic,
                TokenClass::Clitic,
                TokenClass::Abbreviation,
                TokenClass::Unit,
                TokenClass::Multiunit,
            ]
        );
        assert_eq!(
            tokenization.tokens()[0]
                .parts()
                .iter()
                .map(LogicalPart::value)
                .collect::<Vec<_>>(),
            ["de", "o"]
        );
        assert_eq!(
            tokenization.tokens()[4]
                .parts()
                .iter()
                .map(LogicalPart::value)
                .collect::<Vec<_>>(),
            ["contar", "lhe", "ei"]
        );
        assert_eq!(
            tokenization.tokens()[0].operation(),
            BoundaryOperation::LogicalDecomposition
        );
        assert_eq!(
            tokenization.tokens()[8].operation(),
            BoundaryOperation::ExactMerge
        );
    }

    #[test]
    fn every_derived_literal_is_present_in_its_exact_upstream_source() {
        for line in rule_lines(UD_RULE_DATA) {
            let rule = parse_rule(line).expect("valid admitted UD rule");
            assert!(UD_UPSTREAM.contains(rule.surface), "{}", rule.id);
            for part in rule.parts.split('|').filter(|part| !part.is_empty()) {
                assert!(UD_UPSTREAM.contains(part), "{} part", rule.id);
            }
            if rule.class == TokenClass::Clitic {
                assert!(
                    rule.parts.split('|').any(|part| {
                        UD_PRON_UPSTREAM.contains(&format!("clitic pronouns: {part}"))
                            || UD_PRON_UPSTREAM.contains(&format!(", {part}"))
                    }),
                    "{} clitic source",
                    rule.id
                );
            }
        }
        for line in rule_lines(CLDR_RULE_DATA) {
            let rule = parse_rule(line).expect("valid admitted CLDR rule");
            assert!(CLDR_UPSTREAM.contains(rule.surface), "{}", rule.id);
        }
        assert!(UD_RULE_DATA.starts_with("# Modified by the NLU project:"));
        assert!(CLDR_RULE_DATA.starts_with("# Derived by the NLU project:"));
        assert!(CLDR_UPSTREAM.contains("<decimal>,</decimal>"));
        assert!(
            CLDR_NUMBERING_SYSTEMS
                .contains("<numberingSystem id=\"latn\" type=\"numeric\" digits=\"0123456789\"/>")
        );
        assert!(CLDR_UPSTREAM.contains("<dateFormatItem id=\"EHm\">E, HH:mm</dateFormatItem>"));
        assert!(CLDR_UPSTREAM.contains("{0} quilômetro"));
        assert!(CLDR_UPSTREAM.contains("{0}km/h"));
        assert!(WORD_BREAK_TEST.contains("÷ 0031 × 002C × 0030 ÷"));
    }

    #[test]
    fn maps_normalized_tokens_and_logical_parts_to_exact_original_bytes() {
        let text = NormalizedText::new("quilo\u{302}metro do".into()).expect("decomposed source");
        let tokenization = text.tokenize().expect("tokenization");
        assert_eq!(surfaces(&tokenization, &text), ["quilômetro", "do"]);
        assert_eq!(
            tokenization.tokens()[0].original_slice(&text),
            Ok("quilo\u{302}metro")
        );
        let contraction = &tokenization.tokens()[1];
        assert_eq!(contraction.original_slice(&text), Ok("do"));
        for part in contraction.parts() {
            assert_eq!(
                part.normalized_projection().slice(&text),
                Ok("do"),
                "{}",
                part.value()
            );
            assert_eq!(
                part.original_projection().slice(text.original()),
                Ok("do"),
                "{}",
                part.value()
            );
        }
    }

    #[test]
    fn handles_public_numeric_and_punctuation_boundaries() {
        let text = NormalizedText::new("12,5!".into()).expect("public boundary form");
        let tokenization = text.tokenize().expect("tokenization");
        assert_eq!(surfaces(&tokenization, &text), ["12,5", "!"]);
        assert_eq!(tokenization.tokens()[0].class(), TokenClass::Number);
        assert_eq!(
            tokenization.tokens()[0].rule().id(),
            "cldr48.pt.latn-decimal-comma"
        );
        assert_eq!(tokenization.tokens()[1].class(), TokenClass::Punctuation);
        assert_eq!(
            tokenization.tokens()[1].rule().id(),
            "unicode17.general-category-ascii-punctuation"
        );
        assert_eq!(
            tokenization.tokens()[1].operation(),
            BoundaryOperation::PunctuationBoundary
        );
    }

    #[test]
    fn exact_rules_require_boundaries_and_preserve_unsupported_connectors() {
        let text = NormalizedText::new(
            "do doFIXTURE_TECNICA i.e., i.e./FIXTURE_TECNICA 12:30 9:30 km/h km/FIXTURE_TECNICA do@FIXTURE_TECNICA do\\FIXTURE_TECNICA i.e.\\FIXTURE_TECNICA FIXTURE_TECNICA-X-X"
                .into(),
        )
        .expect("input");
        let tokenization = text.tokenize().expect("tokenization");
        assert_eq!(
            surfaces(&tokenization, &text),
            [
                "do",
                "doFIXTURE_TECNICA",
                "i.e.",
                ",",
                "i.e./FIXTURE_TECNICA",
                "12:30",
                "9:30",
                "km/h",
                "km/FIXTURE_TECNICA",
                "do@FIXTURE_TECNICA",
                "do\\FIXTURE_TECNICA",
                "i.e.\\FIXTURE_TECNICA",
                "FIXTURE_TECNICA-X-X"
            ]
        );
        assert_eq!(tokenization.tokens()[0].class(), TokenClass::Contraction);
        assert_eq!(tokenization.tokens()[1].class(), TokenClass::Unknown);
        assert_eq!(tokenization.tokens()[2].class(), TokenClass::Abbreviation);
        assert_eq!(tokenization.tokens()[3].class(), TokenClass::Punctuation);
        assert_eq!(tokenization.tokens()[5].class(), TokenClass::Time);
        assert_eq!(tokenization.tokens()[5].rule().id(), "cldr48.pt.hh-mm");
        assert_eq!(tokenization.tokens()[7].class(), TokenClass::Multiunit);
        for index in [4_usize, 6, 8, 9, 10, 11, 12] {
            let token = &tokenization.tokens()[index];
            assert_eq!(token.class(), TokenClass::Unknown);
            assert_eq!(token.operation(), BoundaryOperation::UnsupportedPreserve);
        }
    }

    #[test]
    fn source_patterns_are_narrow_and_fail_closed() {
        let text = NormalizedText::new(
            "0 12,5 12.5 12,5,6 １２ 12:30 99:99 9:30 123:45 km quilômetro FIXTURE_TECNICA.m. FIXTURE_TECNICA/ou FIXTURE_TECNICA/A +12 @FIXTURE_TECNICA 1.234,5 12:30,5"
                .into(),
        )
        .expect("scope counterexamples");
        let tokenization = text.tokenize().expect("tokenization");
        let actual = tokenization
            .tokens()
            .iter()
            .map(|token| {
                (
                    token.normalized_slice(&text).expect("same source"),
                    token.class(),
                )
            })
            .collect::<Vec<_>>();
        assert!(actual.contains(&("0", TokenClass::Number)));
        assert!(actual.contains(&("12,5", TokenClass::Number)));
        assert!(actual.contains(&("12:30", TokenClass::Time)));
        assert!(actual.contains(&("99:99", TokenClass::Time)));
        assert!(actual.contains(&("quilômetro", TokenClass::Unit)));
        for unsupported in [
            "12.5",
            "12,5,6",
            "１２",
            "9:30",
            "123:45",
            "km",
            "FIXTURE_TECNICA.m.",
            "FIXTURE_TECNICA/ou",
            "FIXTURE_TECNICA/A",
            "+12",
            "@FIXTURE_TECNICA",
            "1.234,5",
            "12:30,5",
        ] {
            assert!(
                actual.contains(&(unsupported, TokenClass::Unknown)),
                "{unsupported}"
            );
        }
    }

    #[test]
    fn exact_forms_leave_only_terminal_punctuation() {
        let text = NormalizedText::new(
            "km/h. 12:30. i.e.. contar-lhe-ei. km/h,, do_ 12_ 12,5_ 12:30_ quilômetro_ km/h_"
                .into(),
        )
        .expect("terminal punctuation");
        let tokenization = text.tokenize().expect("tokenization");
        assert_eq!(
            surfaces(&tokenization, &text),
            [
                "km/h",
                ".",
                "12:30",
                ".",
                "i.e.",
                ".",
                "contar-lhe-ei",
                ".",
                "km/h",
                ",",
                ",",
                "do",
                "_",
                "12",
                "_",
                "12,5",
                "_",
                "12:30",
                "_",
                "quilômetro",
                "_",
                "km/h",
                "_"
            ]
        );
        for index in [0_usize, 2, 4, 6, 8, 11, 13, 15, 17, 19, 21] {
            assert_ne!(tokenization.tokens()[index].class(), TokenClass::Unknown);
        }
        for index in [1_usize, 3, 5, 7, 9, 10, 12, 14, 16, 18, 20, 22] {
            assert_eq!(
                tokenization.tokens()[index].class(),
                TokenClass::Punctuation
            );
        }
    }

    #[test]
    fn terminal_plus_and_underscore_bridges_remain_complete_unknowns() {
        const FIXTURE_TECNICA_FORMS: [&str; 6] = [
            "do+",
            "12+",
            "quilômetro+",
            "km/h+",
            "do_FIXTURE_TECNICA",
            "12:30_FIXTURE_TECNICA",
        ];
        for input in FIXTURE_TECNICA_FORMS {
            let text = NormalizedText::new(input.to_owned()).expect("technical fixture");
            let tokenization = text.tokenize().expect("fail-closed tokenization");
            assert_eq!(tokenization.len(), 1, "{input}");
            assert_eq!(tokenization.tokens()[0].class(), TokenClass::Unknown);
            assert_eq!(
                tokenization.tokens()[0].operation(),
                BoundaryOperation::UnsupportedPreserve
            );
            assert_eq!(surfaces(&tokenization, &text), [input]);
        }
    }

    #[test]
    fn punctuation_allowlist_is_exactly_source_backed() {
        let admitted = [
            '!', '"', '#', '%', '&', '\'', '(', ')', '*', ',', '-', '.', '/', ':', ';', '?', '@',
            '[', '\\', ']', '_', '{', '}',
        ];
        for character in admitted {
            assert!(is_admitted_ascii_punctuation(character));
            let prefix = format!("{:04X};", u32::from(character));
            let row = UNICODE_DATA
                .lines()
                .find(|line| line.starts_with(&prefix))
                .expect("admitted code point in UnicodeData");
            let category = row.split(';').nth(2).expect("general category");
            assert!(category.starts_with('P'), "{row}");
        }
        for excluded in ['$', '+', '<', '=', '>', '^', '`', '|', '~'] {
            assert!(!is_admitted_ascii_punctuation(excluded));
        }
    }

    #[test]
    fn output_is_reproducible_and_rule_table_order_independent() {
        let text = NormalizedText::new("12:30, do quilômetro km/h".into()).expect("input");
        let first = text.tokenize().expect("first");
        let second = text.tokenize().expect("second");
        assert_eq!(first, second);

        let reversed = [DEFAULT_RULE_TABLES[1], DEFAULT_RULE_TABLES[0]];
        assert_eq!(
            tokenize_with_tables(&text, &reversed).expect("permuted tables"),
            first
        );
    }

    #[test]
    fn every_emitted_token_is_a_rebased_fixed_point() {
        let text =
            NormalizedText::new("12,5! do i.e. FIXTURE_TECNICA.M. 12:30 quilômetro km/h".into())
                .expect("mixed input");
        let tokenization = text.tokenize().expect("tokenization");
        for token in tokenization.tokens() {
            let surface = token
                .normalized_slice(&text)
                .expect("same source")
                .to_owned();
            let isolated = NormalizedText::new(surface).expect("isolated token");
            let retokenized = isolated.tokenize().expect("fixed point");
            assert_eq!(retokenized.len(), 1);
            let repeated = &retokenized.tokens()[0];
            assert_eq!(repeated.class(), token.class());
            assert_eq!(repeated.operation(), token.operation());
            assert_eq!(repeated.rule(), token.rule());
            assert_eq!(
                repeated
                    .parts()
                    .iter()
                    .map(LogicalPart::value)
                    .collect::<Vec<_>>(),
                token
                    .parts()
                    .iter()
                    .map(LogicalPart::value)
                    .collect::<Vec<_>>()
            );
        }
    }

    #[test]
    fn enforces_the_limit_on_final_punctuation_tokens() {
        let exact = NormalizedText::new("!".repeat(MAX_TOKENS)).expect("exact grapheme input");
        assert_eq!(
            exact.tokenize().expect("exact token limit").len(),
            MAX_TOKENS
        );

        let over = NormalizedText::new("!".repeat(MAX_TOKENS + 1)).expect("one-over input");
        assert_eq!(
            over.tokenize(),
            Err(TextError::TokenLimit {
                limit: MAX_TOKENS as u32
            })
        );
    }

    #[test]
    fn accepts_empty_or_space_only_input_without_tokens() {
        let empty = NormalizedText::new(String::new()).expect("empty");
        assert!(empty.tokenize().expect("empty tokenization").is_empty());
        let spaces = NormalizedText::new("   ".into()).expect("spaces");
        assert!(spaces.tokenize().expect("space tokenization").is_empty());
    }

    #[test]
    fn debug_output_never_discloses_request_text() {
        let canary = "FIXTURE_TECNICA_PRIVATE/CANARY";
        let text = NormalizedText::new(canary.into()).expect("technical canary");
        let tokenization = text.tokenize().expect("tokenization");
        assert!(!format!("{tokenization:?}").contains(canary));
        assert!(!format!("{:?}", tokenization.tokens()[0]).contains(canary));
    }

    #[test]
    fn malformed_or_duplicate_rule_tables_fail_closed() {
        let malformed = [RuleTable {
            prefix: "FIXTURE_TECNICA.",
            contents: "FIXTURE_TECNICA.rule\tFIXTURE_TECNICA\tunknown\t",
        }];
        let text = NormalizedText::new("FIXTURE_TECNICA".into()).expect("technical fixture");
        assert_eq!(
            tokenize_with_tables(&text, &malformed),
            Err(TextError::TokenizationInvariant)
        );

        let duplicate = [RuleTable {
            prefix: "FIXTURE_TECNICA.",
            contents: concat!(
                "FIXTURE_TECNICA.a\tFIXTURE_TECNICA_A\tabbreviation\t\n",
                "FIXTURE_TECNICA.b\tFIXTURE_TECNICA_A\tabbreviation\t\n"
            ),
        }];
        assert_eq!(
            tokenize_with_tables(&text, &duplicate),
            Err(TextError::TokenizationInvariant)
        );
    }
}
