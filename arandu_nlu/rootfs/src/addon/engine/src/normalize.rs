use unicode_normalization::{UnicodeNormalization, char::is_combining_mark};

pub(crate) fn normalize(input: &str) -> String {
    let mut expanded = String::with_capacity(input.len());
    for character in input
        .nfd()
        .filter(|character| !is_combining_mark(*character))
    {
        if character == '%' {
            expanded.push_str(" por cento ");
        } else if character.is_alphanumeric() {
            for lower in character.to_lowercase() {
                expanded.push(lower);
            }
        } else {
            expanded.push(' ');
        }
    }
    expanded.split_whitespace().collect::<Vec<_>>().join(" ")
}

pub(crate) fn strip_article(value: &str) -> &str {
    for article in ["a ", "as ", "o ", "os ", "uma ", "um "] {
        if let Some(rest) = value.strip_prefix(article) {
            return rest;
        }
    }
    value
}

pub(crate) fn strip_preposition(value: &str) -> Option<&str> {
    for preposition in [
        "das ", "dos ", "nas ", "nos ", "da ", "de ", "do ", "na ", "no ",
    ] {
        if let Some(rest) = value.strip_prefix(preposition) {
            return Some(rest);
        }
    }
    None
}

pub(crate) const AREA_PREPOSITIONS: &[&str] = &[
    "das ", "dos ", "nas ", "nos ", "da ", "de ", "do ", "na ", "no ",
];

/// Normalize exactly like [`normalize`] and map every normalized byte back
/// to the source byte it came from, so parsed ranges translate to original
/// UTF-8 spans without inventing text.
pub(crate) fn normalize_with_spans(input: &str) -> (String, Vec<usize>) {
    let mut expanded: Vec<u8> = Vec::with_capacity(input.len());
    let mut sources: Vec<usize> = Vec::with_capacity(input.len());
    let mut push = |bytes: &[u8], source: usize| {
        expanded.extend_from_slice(bytes);
        sources.extend(std::iter::repeat_n(source, bytes.len()));
    };
    for (byte_index, character) in input.char_indices() {
        for decomposed in character.nfd() {
            if is_combining_mark(decomposed) {
                continue;
            }
            if decomposed == '%' {
                push(b" por cento ", byte_index);
            } else if decomposed.is_alphanumeric() {
                for lower in decomposed.to_lowercase() {
                    let mut encoded = [0_u8; 4];
                    let bytes = lower.encode_utf8(&mut encoded).as_bytes().to_vec();
                    push(&bytes, byte_index);
                }
            } else {
                push(b" ", byte_index);
            }
        }
    }
    let mut text: Vec<u8> = Vec::new();
    let mut map: Vec<usize> = Vec::new();
    let mut index = 0_usize;
    let mut first_word = true;
    while index < expanded.len() {
        let gap_start = index;
        while index < expanded.len() && expanded[index] == b' ' {
            index += 1;
        }
        let word_start = index;
        while index < expanded.len() && expanded[index] != b' ' {
            index += 1;
        }
        if word_start == index {
            break;
        }
        if !first_word {
            text.push(b' ');
            map.push(sources[gap_start]);
        }
        first_word = false;
        text.extend_from_slice(&expanded[word_start..index]);
        map.extend_from_slice(&sources[word_start..index]);
    }
    let text = String::from_utf8(text).expect("normalized pieces are valid UTF-8");
    debug_assert_eq!(text.len(), map.len());
    (text, map)
}

#[cfg(test)]
mod tests {
    use super::{normalize, normalize_with_spans};

    #[test]
    fn removes_case_diacritics_and_punctuation() {
        assert_eq!(
            normalize("  DESLIGUE o Ventilador em 25%!  "),
            "desligue o ventilador em 25 por cento"
        );
    }

    #[test]
    fn spanned_normalization_matches_plain_normalization() {
        let inputs = [
            "",
            "   ",
            "Acenda a luz da sala e do quarto.",
            "  DESLIGUE o Ventilador em 25%!  ",
            "Ligue a lâmpada da sala.",
            "Acenda a cafeteira ☕.",
            "eße Ångström 100% a  b   c",
            "日本語テスト ABC",
        ];
        for input in inputs {
            let (text, map) = normalize_with_spans(input);
            assert_eq!(text, normalize(input), "text for {input:?}");
            assert_eq!(text.len(), map.len(), "map for {input:?}");
            assert!(
                map.windows(2).all(|pair| pair[0] <= pair[1]),
                "monotone for {input:?}"
            );
            for source in &map {
                assert!(input.is_char_boundary(*source), "boundary for {input:?}");
            }
        }
    }

    #[test]
    fn spanned_ranges_round_trip_to_original_slices() {
        let input = "Acenda o abajur da cozinha ☕.";
        let (text, map) = normalize_with_spans(input);
        assert_eq!(text, "acenda o abajur da cozinha");
        let start = text.find("abajur").expect("needle");
        let end = start + "abajur".len();
        let original_start = map[start];
        let original_end = map.get(end).copied().unwrap_or(input.len());
        assert_eq!(&input[original_start..original_end], "abajur");
    }
}
