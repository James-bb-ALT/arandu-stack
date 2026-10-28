use crate::{CoreError, MAX_REQUEST_BYTES};
use core::cmp::Ordering;
use core::fmt;
use std::sync::Arc;

#[derive(Clone)]
pub struct RequestText {
    source: Arc<str>,
}

impl RequestText {
    pub fn new(value: String) -> Result<Self, CoreError> {
        Self::validate_len(value.len())?;
        Ok(Self {
            source: Arc::from(value),
        })
    }

    pub fn from_utf8(bytes: Vec<u8>) -> Result<Self, CoreError> {
        Self::validate_len(bytes.len())?;
        let value = String::from_utf8(bytes).map_err(|_| CoreError::InvalidUtf8)?;
        Self::new(value)
    }

    fn validate_len(len: usize) -> Result<(), CoreError> {
        if len > MAX_REQUEST_BYTES {
            return Err(CoreError::RequestTooLarge {
                limit: MAX_REQUEST_BYTES as u32,
            });
        }
        Ok(())
    }

    #[must_use]
    pub fn as_str(&self) -> &str {
        &self.source
    }

    #[must_use]
    pub fn as_bytes(&self) -> &[u8] {
        self.source.as_bytes()
    }

    #[must_use]
    pub fn len(&self) -> usize {
        self.source.len()
    }

    #[must_use]
    pub fn is_empty(&self) -> bool {
        self.source.is_empty()
    }

    pub fn span(&self, start: u64, end: u64) -> Result<Utf8Span, CoreError> {
        if start > u64::from(u32::MAX) || end > u64::from(u32::MAX) {
            return Err(CoreError::OffsetOverflow);
        }
        if start > end {
            return Err(CoreError::SpanReversed);
        }
        if start == end {
            return Err(CoreError::EmptySpan);
        }

        let start = start as usize;
        let end = end as usize;
        if end > self.source.len() {
            return Err(CoreError::SpanOutOfRange);
        }
        if !self.source.is_char_boundary(start) || !self.source.is_char_boundary(end) {
            return Err(CoreError::SpanNotCharBoundary);
        }

        Ok(Utf8Span {
            source: Arc::clone(&self.source),
            start: start as u32,
            end: end as u32,
        })
    }
}

impl PartialEq for RequestText {
    fn eq(&self, other: &Self) -> bool {
        self.source == other.source
    }
}

impl Eq for RequestText {}

impl fmt::Debug for RequestText {
    fn fmt(&self, formatter: &mut fmt::Formatter<'_>) -> fmt::Result {
        formatter
            .debug_struct("RequestText")
            .field("bytes", &self.source.len())
            .finish()
    }
}

#[derive(Clone)]
pub struct Utf8Span {
    source: Arc<str>,
    start: u32,
    end: u32,
}

impl Utf8Span {
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
    pub fn belongs_to(&self, request: &RequestText) -> bool {
        Arc::ptr_eq(&self.source, &request.source)
    }

    pub fn slice<'a>(&self, request: &'a RequestText) -> Result<&'a str, CoreError> {
        if !self.belongs_to(request) {
            return Err(CoreError::SpanSourceMismatch);
        }
        Ok(&request.source[self.start as usize..self.end as usize])
    }
}

impl PartialEq for Utf8Span {
    fn eq(&self, other: &Self) -> bool {
        self.start == other.start && self.end == other.end && self.source == other.source
    }
}

impl Eq for Utf8Span {}

impl PartialOrd for Utf8Span {
    fn partial_cmp(&self, other: &Self) -> Option<Ordering> {
        Some(self.cmp(other))
    }
}

impl Ord for Utf8Span {
    fn cmp(&self, other: &Self) -> Ordering {
        (self.start, self.end, self.source.as_ref()).cmp(&(
            other.start,
            other.end,
            other.source.as_ref(),
        ))
    }
}

impl fmt::Debug for Utf8Span {
    fn fmt(&self, formatter: &mut fmt::Formatter<'_>) -> fmt::Result {
        formatter
            .debug_struct("Utf8Span")
            .field("start", &self.start)
            .field("end", &self.end)
            .field("source_bytes", &self.source.len())
            .finish()
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn preserves_original_utf8_bytes_and_half_open_coordinates() {
        let bytes = b"FIXTURE_TECNICA_\xc2\xa7_A".to_vec();
        let request = RequestText::from_utf8(bytes.clone()).expect("valid UTF-8");
        assert_eq!(request.as_bytes(), bytes);

        let marker_start = "FIXTURE_TECNICA_".len() as u64;
        let marker_end = marker_start + 2;
        let span = request
            .span(marker_start, marker_end)
            .expect("character boundary span");
        assert_eq!(span.slice(&request), Ok("\u{a7}"));
        assert_eq!(span.start(), marker_start as u32);
        assert_eq!(span.end(), marker_end as u32);
    }

    #[test]
    fn rejects_every_invalid_span_boundary() {
        let request = RequestText::new("FIXTURE_TECNICA_\u{a7}".into()).expect("valid request");
        let multibyte_start = "FIXTURE_TECNICA_".len() as u64;

        assert_eq!(request.span(2, 1), Err(CoreError::SpanReversed));
        assert_eq!(request.span(1, 1), Err(CoreError::EmptySpan));
        assert_eq!(
            request.span(0, request.len() as u64 + 1),
            Err(CoreError::SpanOutOfRange)
        );
        assert_eq!(
            request.span(multibyte_start + 1, request.len() as u64),
            Err(CoreError::SpanNotCharBoundary)
        );
        assert_eq!(
            request.span(u64::from(u32::MAX) + 1, u64::from(u32::MAX) + 2),
            Err(CoreError::OffsetOverflow)
        );
    }

    #[test]
    fn binds_spans_to_one_immutable_source_identity() {
        let first = RequestText::new("FIXTURE_TECNICA_A".into()).expect("valid request");
        let same_bytes = RequestText::new("FIXTURE_TECNICA_A".into()).expect("valid request");
        let clone = first.clone();
        let span = first.span(0, first.len() as u64).expect("full span");

        assert_eq!(span.slice(&clone), Ok("FIXTURE_TECNICA_A"));
        assert_eq!(span.slice(&same_bytes), Err(CoreError::SpanSourceMismatch));
    }

    #[test]
    fn rejects_invalid_or_oversized_request_bytes() {
        assert_eq!(
            RequestText::from_utf8(vec![0xff]),
            Err(CoreError::InvalidUtf8)
        );
        assert_eq!(
            RequestText::new("A".repeat(MAX_REQUEST_BYTES + 1)),
            Err(CoreError::RequestTooLarge {
                limit: MAX_REQUEST_BYTES as u32
            })
        );
    }

    #[test]
    fn debug_does_not_disclose_request_text() {
        let canary = "FIXTURE_TECNICA_PRIVATE_CANARY";
        let request = RequestText::new(canary.into()).expect("valid request");
        assert!(!format!("{request:?}").contains(canary));
        let span = request.span(0, request.len() as u64).expect("full span");
        assert!(!format!("{span:?}").contains(canary));
    }
}
