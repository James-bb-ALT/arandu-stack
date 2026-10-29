use crate::{
    MAX_DECODED_STRING_BYTES, MAX_NESTING_DEPTH, MAX_NUMERIC_TOKEN_BYTES, MAX_STRUCTURAL_ITEMS,
    MAX_WIRE_BYTES, ProtocolError,
};

#[derive(Clone, Copy)]
pub(crate) struct Limits {
    wire_bytes: usize,
    decoded_string_bytes: usize,
    nesting_depth: usize,
    structural_items: usize,
    numeric_token_bytes: usize,
    count_object_members: bool,
    count_numeric_sign: bool,
}

impl Limits {
    pub(crate) const V1: Self = Self {
        wire_bytes: MAX_WIRE_BYTES,
        decoded_string_bytes: MAX_DECODED_STRING_BYTES,
        nesting_depth: MAX_NESTING_DEPTH,
        structural_items: MAX_STRUCTURAL_ITEMS,
        numeric_token_bytes: MAX_NUMERIC_TOKEN_BYTES,
        count_object_members: false,
        count_numeric_sign: false,
    };

    pub(crate) const fn strict(
        wire_bytes: usize,
        decoded_string_bytes: usize,
        nesting_depth: usize,
        structural_items: usize,
        numeric_token_bytes: usize,
    ) -> Self {
        Self {
            wire_bytes,
            decoded_string_bytes,
            nesting_depth,
            structural_items,
            numeric_token_bytes,
            count_object_members: true,
            count_numeric_sign: true,
        }
    }
}

#[derive(Clone, Copy)]
enum Container {
    Array { expecting_value: bool },
    Object,
}

pub(crate) fn inspect(bytes: &[u8]) -> Result<&str, ProtocolError> {
    inspect_with_limits(bytes, Limits::V1)
}

pub(crate) fn inspect_with_limits(bytes: &[u8], limits: Limits) -> Result<&str, ProtocolError> {
    if bytes.len() > limits.wire_bytes {
        return Err(ProtocolError::InputTooLarge);
    }
    let text = core::str::from_utf8(bytes).map_err(|_| ProtocolError::InvalidUtf8)?;
    let mut stack = Vec::with_capacity(limits.nesting_depth);
    let mut index = 0;
    let mut structural_items = 0;

    while index < bytes.len() {
        match bytes[index] {
            b'"' => {
                count_array_value(&mut stack, &mut structural_items, limits)?;
                index = scan_string(text, index + 1, limits)?;
            }
            b'[' => {
                count_array_value(&mut stack, &mut structural_items, limits)?;
                push_container(
                    &mut stack,
                    Container::Array {
                        expecting_value: true,
                    },
                    limits,
                )?;
                index += 1;
            }
            b'{' => {
                count_array_value(&mut stack, &mut structural_items, limits)?;
                push_container(&mut stack, Container::Object, limits)?;
                index += 1;
            }
            b']' | b'}' => {
                stack.pop();
                index += 1;
            }
            b',' => {
                if let Some(Container::Array { expecting_value }) = stack.last_mut() {
                    *expecting_value = true;
                }
                index += 1;
            }
            b':' if limits.count_object_members => {
                count_structural_item(&mut structural_items, limits)?;
                index += 1;
            }
            b'-' | b'0'..=b'9' => {
                count_array_value(&mut stack, &mut structural_items, limits)?;
                index = scan_number(bytes, index, limits)?;
            }
            b't' | b'f' | b'n' => {
                count_array_value(&mut stack, &mut structural_items, limits)?;
                index = scan_literal(bytes, index);
            }
            _ => index += 1,
        }
    }
    Ok(text)
}

fn push_container(
    stack: &mut Vec<Container>,
    container: Container,
    limits: Limits,
) -> Result<(), ProtocolError> {
    if stack.len() == limits.nesting_depth {
        return Err(ProtocolError::NestingTooDeep);
    }
    stack.push(container);
    Ok(())
}

fn count_array_value(
    stack: &mut [Container],
    structural_items: &mut usize,
    limits: Limits,
) -> Result<(), ProtocolError> {
    if let Some(Container::Array { expecting_value }) = stack.last_mut()
        && *expecting_value
    {
        count_structural_item(structural_items, limits)?;
        *expecting_value = false;
    }
    Ok(())
}

fn count_structural_item(
    structural_items: &mut usize,
    limits: Limits,
) -> Result<(), ProtocolError> {
    *structural_items += 1;
    if *structural_items > limits.structural_items {
        return Err(ProtocolError::StructuralLimitExceeded);
    }
    Ok(())
}

fn scan_string(text: &str, mut index: usize, limits: Limits) -> Result<usize, ProtocolError> {
    let bytes = text.as_bytes();
    let mut decoded_bytes = 0;
    while index < bytes.len() {
        match bytes[index] {
            b'"' => return Ok(index + 1),
            b'\\' => {
                index += 1;
                if index >= bytes.len() {
                    return Err(ProtocolError::MalformedJson);
                }
                match bytes[index] {
                    b'"' | b'\\' | b'/' | b'b' | b'f' | b'n' | b'r' | b't' => {
                        decoded_bytes += 1;
                        index += 1;
                    }
                    b'u' => {
                        let (scalar, next) = scan_unicode_escape(bytes, index + 1)?;
                        decoded_bytes += scalar.len_utf8();
                        index = next;
                    }
                    _ => return Err(ProtocolError::MalformedJson),
                }
            }
            byte if byte < 0x20 => return Err(ProtocolError::MalformedJson),
            byte if byte.is_ascii() => {
                decoded_bytes += 1;
                index += 1;
            }
            _ => {
                let character = text[index..]
                    .chars()
                    .next()
                    .ok_or(ProtocolError::MalformedJson)?;
                decoded_bytes += character.len_utf8();
                index += character.len_utf8();
            }
        }
        if decoded_bytes > limits.decoded_string_bytes {
            return Err(ProtocolError::StringTooLarge);
        }
    }
    Err(ProtocolError::MalformedJson)
}

fn scan_unicode_escape(bytes: &[u8], start: usize) -> Result<(char, usize), ProtocolError> {
    let first = parse_hex_quad(bytes, start)?;
    let next = start + 4;
    let scalar = if (0xd800..=0xdbff).contains(&first) {
        if bytes.get(next..next + 2) != Some(b"\\u") {
            return Err(ProtocolError::MalformedJson);
        }
        let second = parse_hex_quad(bytes, next + 2)?;
        if !(0xdc00..=0xdfff).contains(&second) {
            return Err(ProtocolError::MalformedJson);
        }
        let high = u32::from(first - 0xd800);
        let low = u32::from(second - 0xdc00);
        (high << 10) + low + 0x1_0000
    } else if (0xdc00..=0xdfff).contains(&first) {
        return Err(ProtocolError::MalformedJson);
    } else {
        u32::from(first)
    };
    let end = if scalar > 0xffff { next + 6 } else { next };
    char::from_u32(scalar)
        .map(|character| (character, end))
        .ok_or(ProtocolError::MalformedJson)
}

fn parse_hex_quad(bytes: &[u8], start: usize) -> Result<u16, ProtocolError> {
    let quad = bytes
        .get(start..start + 4)
        .ok_or(ProtocolError::MalformedJson)?;
    quad.iter().try_fold(0_u16, |value, byte| {
        let digit = match byte {
            b'0'..=b'9' => u16::from(*byte - b'0'),
            b'a'..=b'f' => u16::from(*byte - b'a' + 10),
            b'A'..=b'F' => u16::from(*byte - b'A' + 10),
            _ => return Err(ProtocolError::MalformedJson),
        };
        Ok((value << 4) | digit)
    })
}

fn scan_number(bytes: &[u8], start: usize, limits: Limits) -> Result<usize, ProtocolError> {
    let mut index = start;
    if bytes[index] == b'-' {
        index += 1;
    }
    let digits_start = index;
    while index < bytes.len() && !is_delimiter(bytes[index]) {
        if matches!(bytes[index], b'.' | b'e' | b'E') {
            return Err(ProtocolError::NonIntegerNumber);
        }
        index += 1;
    }
    let token_start = if limits.count_numeric_sign {
        start
    } else {
        digits_start
    };
    if index.saturating_sub(token_start) > limits.numeric_token_bytes {
        return Err(ProtocolError::NumericTokenTooLong);
    }
    Ok(index)
}

fn scan_literal(bytes: &[u8], start: usize) -> usize {
    let mut index = start;
    while index < bytes.len() && !is_delimiter(bytes[index]) {
        index += 1;
    }
    index
}

const fn is_delimiter(byte: u8) -> bool {
    matches!(
        byte,
        b' ' | b'\n' | b'\r' | b'\t' | b',' | b']' | b'}' | b':'
    )
}

#[cfg(test)]
mod tests {
    use super::*;

    const STRICT_LIMITS: Limits = Limits::strict(
        crate::v2::MAX_WIRE_BYTES,
        crate::v2::MAX_DECODED_STRING_BYTES,
        crate::v2::MAX_NESTING_DEPTH,
        crate::v2::MAX_STRUCTURAL_ITEMS,
        crate::v2::MAX_NUMERIC_TOKEN_BYTES,
    );

    #[test]
    fn accepts_exact_depth_and_rejects_the_next_level() {
        let exact = format!(
            "{}0{}",
            "[".repeat(MAX_NESTING_DEPTH),
            "]".repeat(MAX_NESTING_DEPTH)
        );
        assert!(inspect(exact.as_bytes()).is_ok());

        let too_deep = format!(
            "{}0{}",
            "[".repeat(MAX_NESTING_DEPTH + 1),
            "]".repeat(MAX_NESTING_DEPTH + 1)
        );
        assert_eq!(
            inspect(too_deep.as_bytes()),
            Err(ProtocolError::NestingTooDeep)
        );
    }

    #[test]
    fn counts_decoded_string_bytes_including_escapes() {
        let exact = format!("\"{}\"", "A".repeat(MAX_DECODED_STRING_BYTES));
        assert!(inspect(exact.as_bytes()).is_ok());
        assert!(inspect(format!("\"{}\"", "\\u0041".repeat(100)).as_bytes()).is_ok());
        let too_large = format!("\"{}\"", "A".repeat(MAX_DECODED_STRING_BYTES + 1));
        assert_eq!(
            inspect(too_large.as_bytes()),
            Err(ProtocolError::StringTooLarge)
        );
    }

    #[test]
    fn handles_surrogate_pairs_and_rejects_invalid_escapes() {
        assert_eq!(inspect(br#""\ud83d\udea7""#), Ok(r#""\ud83d\udea7""#));
        assert_eq!(inspect(br#""\ud83d""#), Err(ProtocolError::MalformedJson));
        assert_eq!(inspect(br#""\udea7""#), Err(ProtocolError::MalformedJson));
        assert_eq!(inspect(br#""\x20""#), Err(ProtocolError::MalformedJson));
    }

    #[test]
    fn rejects_floats_long_numbers_and_excess_array_items() {
        assert_eq!(inspect(b"1.0"), Err(ProtocolError::NonIntegerNumber));
        assert_eq!(
            inspect(b"123456789012345678901"),
            Err(ProtocolError::NumericTokenTooLong)
        );
        let array = format!("[{}]", vec!["0"; MAX_STRUCTURAL_ITEMS + 1].join(","));
        assert_eq!(
            inspect(array.as_bytes()),
            Err(ProtocolError::StructuralLimitExceeded)
        );
    }

    #[test]
    fn strict_profile_counts_object_members_at_the_exact_boundary() {
        let exact = format!(
            "{{{}}}",
            (0..crate::v2::MAX_STRUCTURAL_ITEMS)
                .map(|index| format!(r#""k{index}":0"#))
                .collect::<Vec<_>>()
                .join(",")
        );
        assert!(inspect_with_limits(exact.as_bytes(), STRICT_LIMITS).is_ok());

        let too_many = exact.replacen(
            "}",
            &format!(r#","overflow":{}}}"#, crate::v2::MAX_STRUCTURAL_ITEMS),
            1,
        );
        assert_eq!(
            inspect_with_limits(too_many.as_bytes(), STRICT_LIMITS),
            Err(ProtocolError::StructuralLimitExceeded)
        );
    }

    #[test]
    fn strict_profile_counts_the_sign_in_twenty_byte_integer_tokens() {
        assert!(
            inspect_with_limits(b"-9223372036854775808", STRICT_LIMITS).is_ok(),
            "i64::MIN is exactly twenty bytes"
        );
        assert!(
            inspect_with_limits(b"18446744073709551615", STRICT_LIMITS).is_ok(),
            "u64::MAX is exactly twenty bytes"
        );
        assert_eq!(
            inspect_with_limits(b"-10000000000000000000", STRICT_LIMITS),
            Err(ProtocolError::NumericTokenTooLong)
        );
        assert_eq!(
            inspect_with_limits(b"100000000000000000000", STRICT_LIMITS),
            Err(ProtocolError::NumericTokenTooLong)
        );
    }
}
