use crate::{MAX_GRAPHEME_CLUSTERS, MAX_TOKENS, MAX_UNICODE_SCALARS, TextError, UNICODE_VERSION};
use core::{cmp::Ordering, fmt};
use nlu_core::{MAX_REQUEST_BYTES, RequestText, Utf8Span};
use std::sync::Arc;
use unicode_normalization::UnicodeNormalization;
use unicode_segmentation::UnicodeSegmentation;

#[derive(Clone, Copy, Debug, Eq, PartialEq)]
struct Boundary {
    normalized: u32,
    original: u32,
}

#[derive(Clone)]
pub struct NormalizedText {
    original: RequestText,
    normalized: Arc<str>,
    boundaries: Arc<[Boundary]>,
    scalar_count: u32,
    grapheme_count: u32,
    token_count: u32,
}

impl NormalizedText {
    pub fn new(value: String) -> Result<Self, TextError> {
        Self::from_request(RequestText::new(value)?)
    }

    pub fn from_utf8(bytes: Vec<u8>) -> Result<Self, TextError> {
        Self::from_request(RequestText::from_utf8(bytes)?)
    }

    pub fn from_request(original: RequestText) -> Result<Self, TextError> {
        debug_assert!(original.len() <= MAX_REQUEST_BYTES);
        let source = original.as_str();
        let scalar_count = validate_scalars(source)?;

        let token_count = source.unicode_words().take(MAX_TOKENS + 1).count();
        if token_count > MAX_TOKENS {
            return Err(TextError::TokenLimit {
                limit: MAX_TOKENS as u32,
            });
        }

        let mut normalized = String::with_capacity(source.len());
        let mut boundaries = Vec::new();
        boundaries.push(Boundary {
            normalized: 0,
            original: 0,
        });
        let mut grapheme_count = 0_usize;
        for (start, grapheme) in source.grapheme_indices(true) {
            grapheme_count += 1;
            if grapheme_count > MAX_GRAPHEME_CLUSTERS {
                return Err(TextError::GraphemeLimit {
                    limit: MAX_GRAPHEME_CLUSTERS as u32,
                });
            }

            normalized.extend(grapheme.nfc());
            if normalized.len() > MAX_REQUEST_BYTES {
                return Err(TextError::NormalizedByteLimit {
                    limit: MAX_REQUEST_BYTES as u32,
                });
            }
            boundaries.push(Boundary {
                normalized: checked_u32(normalized.len())?,
                original: checked_u32(start + grapheme.len())?,
            });
        }

        let whole: String = source.nfc().collect();
        if whole != normalized
            || unicode_normalization::UNICODE_VERSION != (17, 0, 0)
            || unicode_segmentation::UNICODE_VERSION != (17, 0, 0)
            || UNICODE_VERSION != "17.0.0"
        {
            return Err(TextError::NormalizationInvariant);
        }

        Ok(Self {
            original,
            normalized: Arc::from(normalized),
            boundaries: boundaries.into(),
            scalar_count: scalar_count as u32,
            grapheme_count: grapheme_count as u32,
            token_count: token_count as u32,
        })
    }

    #[must_use]
    pub const fn original(&self) -> &RequestText {
        &self.original
    }

    #[must_use]
    pub fn as_str(&self) -> &str {
        &self.normalized
    }

    #[must_use]
    pub fn as_bytes(&self) -> &[u8] {
        self.normalized.as_bytes()
    }

    #[must_use]
    pub fn len(&self) -> usize {
        self.normalized.len()
    }

    #[must_use]
    pub fn is_empty(&self) -> bool {
        self.normalized.is_empty()
    }

    #[must_use]
    pub const fn scalar_count(&self) -> u32 {
        self.scalar_count
    }

    #[must_use]
    pub const fn grapheme_count(&self) -> u32 {
        self.grapheme_count
    }

    #[must_use]
    pub const fn token_count(&self) -> u32 {
        self.token_count
    }

    pub fn span(&self, start: u64, end: u64) -> Result<NormalizedSpan, TextError> {
        if start > u64::from(u32::MAX) || end > u64::from(u32::MAX) {
            return Err(TextError::OffsetOverflow);
        }
        if start > end {
            return Err(TextError::SpanReversed);
        }
        if start == end {
            return Err(TextError::EmptySpan);
        }

        let start = start as usize;
        let end = end as usize;
        if end > self.normalized.len() {
            return Err(TextError::SpanOutOfRange);
        }
        if !self.normalized.is_char_boundary(start) || !self.normalized.is_char_boundary(end) {
            return Err(TextError::SpanNotCharBoundary);
        }
        if self.normalized_boundary(start as u32).is_none()
            || self.normalized_boundary(end as u32).is_none()
        {
            return Err(TextError::SpanNotMappingBoundary);
        }

        Ok(NormalizedSpan {
            source: Arc::clone(&self.normalized),
            start: start as u32,
            end: end as u32,
        })
    }

    pub fn to_original_span(&self, span: &NormalizedSpan) -> Result<Utf8Span, TextError> {
        if !span.belongs_to(self) {
            return Err(TextError::SpanSourceMismatch);
        }
        let start = self
            .normalized_boundary(span.start)
            .ok_or(TextError::SpanNotMappingBoundary)?;
        let end = self
            .normalized_boundary(span.end)
            .ok_or(TextError::SpanNotMappingBoundary)?;
        Ok(self.original.span(u64::from(start), u64::from(end))?)
    }

    pub fn from_original_span(&self, span: &Utf8Span) -> Result<NormalizedSpan, TextError> {
        if !span.belongs_to(&self.original) {
            return Err(TextError::SpanSourceMismatch);
        }
        let start = self
            .original_boundary(span.start())
            .ok_or(TextError::SpanNotMappingBoundary)?;
        let end = self
            .original_boundary(span.end())
            .ok_or(TextError::SpanNotMappingBoundary)?;
        self.span(u64::from(start), u64::from(end))
    }

    fn normalized_boundary(&self, offset: u32) -> Option<u32> {
        self.boundaries
            .binary_search_by_key(&offset, |boundary| boundary.normalized)
            .ok()
            .map(|index| self.boundaries[index].original)
    }

    fn original_boundary(&self, offset: u32) -> Option<u32> {
        self.boundaries
            .binary_search_by_key(&offset, |boundary| boundary.original)
            .ok()
            .map(|index| self.boundaries[index].normalized)
    }
}

impl fmt::Debug for NormalizedText {
    fn fmt(&self, formatter: &mut fmt::Formatter<'_>) -> fmt::Result {
        formatter
            .debug_struct("NormalizedText")
            .field("original_bytes", &self.original.len())
            .field("normalized_bytes", &self.normalized.len())
            .field("scalar_count", &self.scalar_count)
            .field("grapheme_count", &self.grapheme_count)
            .field("token_count", &self.token_count)
            .finish()
    }
}

#[derive(Clone)]
pub struct NormalizedSpan {
    source: Arc<str>,
    start: u32,
    end: u32,
}

impl NormalizedSpan {
    #[must_use]
    pub const fn start(&self) -> u32 {
        self.start
    }

    #[must_use]
    pub const fn end(&self) -> u32 {
        self.end
    }

    #[must_use]
    pub const fn len(&self) -> u32 {
        self.end - self.start
    }

    #[must_use]
    pub const fn is_empty(&self) -> bool {
        false
    }

    #[must_use]
    pub fn belongs_to(&self, text: &NormalizedText) -> bool {
        Arc::ptr_eq(&self.source, &text.normalized)
    }

    pub fn slice<'a>(&self, text: &'a NormalizedText) -> Result<&'a str, TextError> {
        if !self.belongs_to(text) {
            return Err(TextError::SpanSourceMismatch);
        }
        Ok(&text.normalized[self.start as usize..self.end as usize])
    }
}

impl PartialEq for NormalizedSpan {
    fn eq(&self, other: &Self) -> bool {
        self.start == other.start && self.end == other.end && self.source == other.source
    }
}

impl Eq for NormalizedSpan {}

impl PartialOrd for NormalizedSpan {
    fn partial_cmp(&self, other: &Self) -> Option<Ordering> {
        Some(self.cmp(other))
    }
}

impl Ord for NormalizedSpan {
    fn cmp(&self, other: &Self) -> Ordering {
        (self.start, self.end, self.source.as_ref()).cmp(&(
            other.start,
            other.end,
            other.source.as_ref(),
        ))
    }
}

impl fmt::Debug for NormalizedSpan {
    fn fmt(&self, formatter: &mut fmt::Formatter<'_>) -> fmt::Result {
        formatter
            .debug_struct("NormalizedSpan")
            .field("start", &self.start)
            .field("end", &self.end)
            .field("source_bytes", &self.source.len())
            .finish()
    }
}

fn checked_u32(value: usize) -> Result<u32, TextError> {
    u32::try_from(value).map_err(|_| TextError::OffsetOverflow)
}

fn validate_scalars(source: &str) -> Result<usize, TextError> {
    let mut count = 0_usize;
    for (offset, value) in source.char_indices() {
        count += 1;
        if count > MAX_UNICODE_SCALARS {
            return Err(TextError::ScalarLimit {
                limit: MAX_UNICODE_SCALARS as u32,
            });
        }
        if value.is_control() {
            return Err(TextError::ControlCodePoint {
                offset: checked_u32(offset)?,
            });
        }
        if is_rejected_zero_width(value) {
            return Err(TextError::ZeroWidthCodePoint {
                offset: checked_u32(offset)?,
            });
        }
    }
    Ok(count)
}

fn is_rejected_zero_width(value: char) -> bool {
    matches!(
        value as u32,
        0x00ad
            | 0x034f
            | 0x0600..=0x0605
            | 0x061c
            | 0x06dd
            | 0x070f
            | 0x0890..=0x0891
            | 0x08e2
            | 0x115f..=0x1160
            | 0x17b4..=0x17b5
            | 0x180b..=0x180f
            | 0x200b..=0x200f
            | 0x202a..=0x202e
            | 0x2060..=0x206f
            | 0x3164
            | 0xfe00..=0xfe0f
            | 0xfeff
            | 0xffa0
            | 0xfff0..=0xfffb
            | 0x110bd
            | 0x110cd
            | 0x13430..=0x1343f
            | 0x1bca0..=0x1bca3
            | 0x1d173..=0x1d17a
            | 0xe0001
            | 0xe0020..=0xe007f
            | 0xe0100..=0xe01ef
    )
}

#[cfg(test)]
mod tests {
    use super::*;
    use nlu_core::CoreError;

    const FIXTURE_TECNICA_PREFIX: &str = "FIXTURE_TECNICA_";

    #[test]
    fn preserves_original_bytes_and_normalizes_nfc_idempotently() {
        let original = format!("{FIXTURE_TECNICA_PREFIX}a\u{301}");
        let first = NormalizedText::new(original.clone()).expect("accepted technical fixture");
        assert_eq!(first.original().as_bytes(), original.as_bytes());
        assert_eq!(first.as_str(), format!("{FIXTURE_TECNICA_PREFIX}\u{e1}"));

        let second =
            NormalizedText::new(first.as_str().to_owned()).expect("already-normalized fixture");
        assert_eq!(second.as_str(), first.as_str());
    }

    #[test]
    fn maps_composed_units_to_original_bytes_and_back() {
        let original = format!("{FIXTURE_TECNICA_PREFIX}a\u{301}_Z");
        let text = NormalizedText::new(original).expect("accepted technical fixture");
        let normalized_start = FIXTURE_TECNICA_PREFIX.len() as u64;
        let normalized_end = normalized_start + "\u{e1}".len() as u64;
        let normalized = text
            .span(normalized_start, normalized_end)
            .expect("normalized unit");
        let source = text
            .to_original_span(&normalized)
            .expect("mapped original unit");
        assert_eq!(source.slice(text.original()), Ok("a\u{301}"));
        assert_eq!(
            text.from_original_span(&source).expect("round trip"),
            normalized
        );
    }

    #[test]
    fn canonical_reordering_keeps_one_reversible_unit() {
        // Unicode 17 NormalizationTest.txt Part 0 canonical reordering case.
        let original = format!("{FIXTURE_TECNICA_PREFIX}D\u{307}\u{323}");
        let text = NormalizedText::new(original).expect("accepted technical fixture");
        assert!(text.as_str().ends_with("\u{1e0c}\u{307}"));
        let start = FIXTURE_TECNICA_PREFIX.len() as u64;
        let span = text.span(start, text.len() as u64).expect("mapped unit");
        let source = text.to_original_span(&span).expect("original unit");
        assert_eq!(source.slice(text.original()), Ok("D\u{307}\u{323}"));
    }

    #[test]
    fn rejects_invalid_normalized_and_original_boundaries() {
        let text = NormalizedText::new(format!("{FIXTURE_TECNICA_PREFIX}a\u{301}"))
            .expect("accepted technical fixture");
        let composed_start = FIXTURE_TECNICA_PREFIX.len() as u64;
        assert_eq!(
            text.span(composed_start + 1, text.len() as u64),
            Err(TextError::SpanNotCharBoundary)
        );
        assert_eq!(text.span(2, 1), Err(TextError::SpanReversed));
        assert_eq!(text.span(1, 1), Err(TextError::EmptySpan));
        assert_eq!(
            text.span(0, text.len() as u64 + 1),
            Err(TextError::SpanOutOfRange)
        );
        assert_eq!(
            text.span(u64::from(u32::MAX) + 1, u64::from(u32::MAX) + 2),
            Err(TextError::OffsetOverflow)
        );

        let original_start = FIXTURE_TECNICA_PREFIX.len() as u64;
        let partial_original = text
            .original()
            .span(original_start, original_start + 1)
            .expect("UTF-8 boundary but not normalization-unit boundary");
        assert_eq!(
            text.from_original_span(&partial_original),
            Err(TextError::SpanNotMappingBoundary)
        );
    }

    #[test]
    fn rejects_foreign_spans_even_when_bytes_match() {
        let first = NormalizedText::new(format!("{FIXTURE_TECNICA_PREFIX}A"))
            .expect("first technical fixture");
        let second = NormalizedText::new(format!("{FIXTURE_TECNICA_PREFIX}A"))
            .expect("second technical fixture");
        let normalized = first.span(0, first.len() as u64).expect("first span");
        assert_eq!(
            second.to_original_span(&normalized),
            Err(TextError::SpanSourceMismatch)
        );
        let original = first
            .original()
            .span(0, first.original().len() as u64)
            .expect("first original span");
        assert_eq!(
            second.from_original_span(&original),
            Err(TextError::SpanSourceMismatch)
        );
    }

    #[test]
    fn rejects_all_unicode_controls_and_explicit_zero_width_classes() {
        for scalar in 0..=0x9f {
            let Some(value) = char::from_u32(scalar) else {
                continue;
            };
            if value.is_control() {
                let fixture = format!("{FIXTURE_TECNICA_PREFIX}{value}");
                assert!(matches!(
                    NormalizedText::new(fixture),
                    Err(TextError::ControlCodePoint { .. })
                ));
            }
        }

        let zero_width_fixtures = [
            '\u{ad}',
            '\u{34f}',
            '\u{61c}',
            '\u{180e}',
            '\u{200b}',
            '\u{200c}',
            '\u{200d}',
            '\u{202e}',
            '\u{2060}',
            '\u{fe0f}',
            '\u{feff}',
            '\u{e0001}',
            '\u{e007f}',
            '\u{e0100}',
        ];
        for value in zero_width_fixtures {
            let fixture = format!("{FIXTURE_TECNICA_PREFIX}{value}");
            assert!(matches!(
                NormalizedText::new(fixture),
                Err(TextError::ZeroWidthCodePoint { .. })
            ));
        }
    }

    #[test]
    fn preserves_accents_compatibility_forms_and_cross_script_confusables() {
        let accent =
            NormalizedText::new(format!("{FIXTURE_TECNICA_PREFIX}\u{e1}")).expect("accent fixture");
        assert!(accent.as_str().ends_with('\u{e1}'));

        let compatibility = NormalizedText::new(format!("{FIXTURE_TECNICA_PREFIX}\u{2460}"))
            .expect("compatibility fixture");
        assert!(compatibility.as_str().ends_with('\u{2460}'));

        let latin =
            NormalizedText::new(format!("{FIXTURE_TECNICA_PREFIX}a")).expect("Latin fixture");
        let cyrillic = NormalizedText::new(format!("{FIXTURE_TECNICA_PREFIX}\u{430}"))
            .expect("Cyrillic fixture");
        let greek =
            NormalizedText::new(format!("{FIXTURE_TECNICA_PREFIX}o")).expect("Latin fixture");
        let omicron =
            NormalizedText::new(format!("{FIXTURE_TECNICA_PREFIX}\u{3bf}")).expect("Greek fixture");
        assert_ne!(latin.as_bytes(), cyrillic.as_bytes());
        assert_ne!(greek.as_bytes(), omicron.as_bytes());
    }

    #[test]
    fn enforces_byte_scalar_grapheme_and_token_boundaries() {
        // FIXTURE_TECNICA: U+10000 is selected only to exercise four-byte UTF-8.
        let exact_bytes = "\u{10000}".repeat(MAX_GRAPHEME_CLUSTERS);
        assert_eq!(exact_bytes.len(), MAX_REQUEST_BYTES);
        assert!(NormalizedText::new(exact_bytes.clone()).is_ok());
        assert_eq!(
            NormalizedText::new(format!("{exact_bytes}A")).expect_err("one byte over limit"),
            TextError::Core(CoreError::RequestTooLarge {
                limit: MAX_REQUEST_BYTES as u32
            })
        );

        let exact_scalars = "a\u{300}".repeat(MAX_GRAPHEME_CLUSTERS);
        let scalar_text = NormalizedText::new(exact_scalars.clone()).expect("exact scalar limit");
        assert_eq!(scalar_text.scalar_count(), MAX_UNICODE_SCALARS as u32);
        assert_eq!(
            NormalizedText::new(format!("{exact_scalars}\u{301}"))
                .expect_err("one scalar over limit"),
            TextError::ScalarLimit {
                limit: MAX_UNICODE_SCALARS as u32
            }
        );

        let exact_graphemes = "A".repeat(MAX_GRAPHEME_CLUSTERS);
        let grapheme_text =
            NormalizedText::new(exact_graphemes.clone()).expect("exact grapheme limit");
        assert_eq!(grapheme_text.grapheme_count(), MAX_GRAPHEME_CLUSTERS as u32);
        assert_eq!(
            NormalizedText::new(format!("{exact_graphemes}A"))
                .expect_err("one grapheme over limit"),
            TextError::GraphemeLimit {
                limit: MAX_GRAPHEME_CLUSTERS as u32
            }
        );

        let exact_tokens = "A ".repeat(MAX_TOKENS);
        let token_text = NormalizedText::new(exact_tokens.clone()).expect("exact token limit");
        assert_eq!(token_text.token_count(), MAX_TOKENS as u32);
        assert_eq!(
            NormalizedText::new(format!("{exact_tokens}A")).expect_err("one token over limit"),
            TextError::TokenLimit {
                limit: MAX_TOKENS as u32
            }
        );
    }

    #[test]
    fn accepts_empty_and_leading_combining_input_deterministically() {
        let empty = NormalizedText::new(String::new()).expect("empty technical fixture");
        assert!(empty.is_empty());
        assert_eq!(empty.scalar_count(), 0);
        assert_eq!(empty.grapheme_count(), 0);
        assert_eq!(empty.token_count(), 0);

        let leading = NormalizedText::new(format!("\u{301}{FIXTURE_TECNICA_PREFIX}"))
            .expect("leading combining technical fixture");
        assert!(leading.as_str().starts_with('\u{301}'));
    }
}
