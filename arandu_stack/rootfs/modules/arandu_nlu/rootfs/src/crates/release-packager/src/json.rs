use std::collections::BTreeMap;

#[derive(Clone, Debug, Eq, PartialEq)]
pub(crate) enum JsonValue {
    Null,
    Bool(bool),
    Number(u64),
    String(String),
    Array(Vec<JsonValue>),
    Object(BTreeMap<String, JsonValue>),
}

pub(crate) fn object(entries: impl IntoIterator<Item = (&'static str, JsonValue)>) -> JsonValue {
    JsonValue::Object(
        entries
            .into_iter()
            .map(|(key, value)| (key.to_owned(), value))
            .collect(),
    )
}

pub(crate) fn canonical_json(value: &JsonValue) -> Vec<u8> {
    let mut output = Vec::new();
    encode(value, &mut output);
    output
}

fn encode(value: &JsonValue, output: &mut Vec<u8>) {
    match value {
        JsonValue::Null => output.extend_from_slice(b"null"),
        JsonValue::Bool(value) => output.extend_from_slice(if *value { b"true" } else { b"false" }),
        JsonValue::Number(value) => output.extend_from_slice(value.to_string().as_bytes()),
        JsonValue::String(value) => encode_string(value, output),
        JsonValue::Array(values) => {
            output.push(b'[');
            for (index, value) in values.iter().enumerate() {
                if index != 0 {
                    output.push(b',');
                }
                encode(value, output);
            }
            output.push(b']');
        }
        JsonValue::Object(values) => {
            output.push(b'{');
            for (index, (key, value)) in values.iter().enumerate() {
                if index != 0 {
                    output.push(b',');
                }
                encode_string(key, output);
                output.push(b':');
                encode(value, output);
            }
            output.push(b'}');
        }
    }
}

fn encode_string(value: &str, output: &mut Vec<u8>) {
    output.push(b'"');
    for character in value.chars() {
        match character {
            '"' => output.extend_from_slice(br#"\""#),
            '\\' => output.extend_from_slice(br#"\\"#),
            '\u{08}' => output.extend_from_slice(br#"\b"#),
            '\u{0c}' => output.extend_from_slice(br#"\f"#),
            '\n' => output.extend_from_slice(br#"\n"#),
            '\r' => output.extend_from_slice(br#"\r"#),
            '\t' => output.extend_from_slice(br#"\t"#),
            '\u{00}'..='\u{1f}' => {
                let escaped = format!("\\u{:04x}", u32::from(character));
                output.extend_from_slice(escaped.as_bytes());
            }
            _ => {
                let mut encoded = [0_u8; 4];
                output.extend_from_slice(character.encode_utf8(&mut encoded).as_bytes());
            }
        }
    }
    output.push(b'"');
}

#[cfg(test)]
mod tests {
    use super::{JsonValue, canonical_json, object};

    #[test]
    fn fixture_tecnica_json_is_sorted_and_escaped() {
        let value = object([
            ("z", JsonValue::String("ação\n".to_owned())),
            ("a", JsonValue::Number(1)),
        ]);
        assert_eq!(
            canonical_json(&value),
            "{\"a\":1,\"z\":\"ação\\n\"}".as_bytes()
        );
    }
}
