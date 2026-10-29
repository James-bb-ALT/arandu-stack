use super::{NormalizedText, TextError};
use unicode_normalization::UnicodeNormalization;
use unicode_segmentation::UnicodeSegmentation;

const NORMALIZATION_TESTS: &str =
    include_str!("../../../data/unicode/17.0.0/ucd/NormalizationTest.txt");
const GRAPHEME_TESTS: &str =
    include_str!("../../../data/unicode/17.0.0/ucd/auxiliary/GraphemeBreakTest.txt");
const WORD_TESTS: &str =
    include_str!("../../../data/unicode/17.0.0/ucd/auxiliary/WordBreakTest.txt");

#[test]
fn unicode_17_nfc_conformance_and_idempotence() {
    let mut rows = 0_usize;
    for (line_number, raw) in NORMALIZATION_TESTS.lines().enumerate() {
        let line = raw
            .split('#')
            .next()
            .expect("split always yields one field")
            .trim();
        if line.is_empty() || line.starts_with('@') {
            continue;
        }
        let fields: Vec<_> = line.split(';').map(str::trim).collect();
        assert!(fields.len() >= 5, "line {}", line_number + 1);
        let values: Vec<_> = fields[..5]
            .iter()
            .map(|field| decode_code_points(field, line_number + 1))
            .collect();
        let expected_nfc = &values[1];
        for source in &values[..3] {
            let actual: String = source.nfc().collect();
            assert_eq!(&actual, expected_nfc, "line {}", line_number + 1);
            assert_eq!(
                actual.nfc().collect::<String>(),
                actual,
                "idempotence line {}",
                line_number + 1
            );
        }
        for source in &values[3..] {
            assert_eq!(
                source.nfc().collect::<String>(),
                values[3],
                "compatibility-preserving line {}",
                line_number + 1
            );
        }
        rows += 1;
    }
    assert_eq!(rows, 20_034);
}

#[test]
fn unicode_17_extended_grapheme_boundary_conformance() {
    assert_boundary_conformance(GRAPHEME_TESTS, 766, |value| {
        let mut boundaries: Vec<_> = value.grapheme_indices(true).map(|(at, _)| at).collect();
        boundaries.push(value.len());
        boundaries
    });
}

#[test]
fn unicode_17_default_word_boundary_conformance() {
    assert_boundary_conformance(WORD_TESTS, 1_944, |value| {
        let mut boundaries: Vec<_> = value.split_word_bound_indices().map(|(at, _)| at).collect();
        boundaries.push(value.len());
        boundaries
    });
}

#[test]
fn accepted_normalization_rows_keep_reversible_full_spans() {
    let mut checked = 0_usize;
    for (line_number, raw) in NORMALIZATION_TESTS.lines().enumerate() {
        let line = raw
            .split('#')
            .next()
            .expect("split always yields one field")
            .trim();
        if line.is_empty() || line.starts_with('@') {
            continue;
        }
        let source = decode_code_points(
            line.split(';').next().expect("normalization source field"),
            line_number + 1,
        );
        if source.is_empty() || source.len() > nlu_core::MAX_REQUEST_BYTES {
            continue;
        }
        let text = match NormalizedText::new(source) {
            Ok(text) => text,
            Err(
                TextError::ControlCodePoint { .. }
                | TextError::ZeroWidthCodePoint { .. }
                | TextError::ScalarLimit { .. }
                | TextError::GraphemeLimit { .. }
                | TextError::TokenLimit { .. },
            ) => continue,
            Err(error) => panic!(
                "unexpected normalization rejection at line {}: {error}",
                line_number + 1
            ),
        };
        if text.is_empty() {
            continue;
        }
        let normalized = text
            .span(0, text.len() as u64)
            .expect("full normalized span");
        let original = text
            .to_original_span(&normalized)
            .expect("full original span");
        assert_eq!(
            text.from_original_span(&original)
                .expect("full normalized round trip"),
            normalized,
            "line {}",
            line_number + 1
        );
        checked += 1;
    }
    assert!(checked > 19_000, "checked {checked} admitted rows");
}

fn decode_code_points(field: &str, line_number: usize) -> String {
    field
        .split_whitespace()
        .map(|value| {
            let scalar = u32::from_str_radix(value, 16).expect("official hexadecimal scalar");
            char::from_u32(scalar).unwrap_or_else(|| panic!("invalid scalar at line {line_number}"))
        })
        .collect()
}

fn assert_boundary_conformance(
    source: &str,
    expected_rows: usize,
    implementation: impl Fn(&str) -> Vec<usize>,
) {
    let mut rows = 0_usize;
    for (line_number, raw) in source.lines().enumerate() {
        let line = raw
            .split('#')
            .next()
            .expect("split always yields one field")
            .trim();
        if line.is_empty() {
            continue;
        }
        let (value, expected) = decode_boundary_case(line, line_number + 1);
        assert_eq!(
            implementation(&value),
            expected,
            "boundary line {}",
            line_number + 1
        );
        rows += 1;
    }
    assert_eq!(rows, expected_rows);
}

fn decode_boundary_case(line: &str, line_number: usize) -> (String, Vec<usize>) {
    let mut value = String::new();
    let mut expected = Vec::new();
    for token in line.split_whitespace() {
        match token {
            "÷" => expected.push(value.len()),
            "×" => {}
            scalar => {
                let scalar = u32::from_str_radix(scalar, 16).expect("official hexadecimal scalar");
                value.push(
                    char::from_u32(scalar)
                        .unwrap_or_else(|| panic!("invalid scalar at line {line_number}")),
                );
            }
        }
    }
    (value, expected)
}
