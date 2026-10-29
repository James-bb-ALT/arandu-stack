use std::collections::BTreeSet;

use serde::de::{DeserializeSeed, Error as _, MapAccess, SeqAccess, Visitor};
use serde_json::{Map, Number, Value};

use crate::{DataError, DataErrorCode, Result};

pub(crate) const MAX_JSON_DEPTH: usize = 64;
pub(crate) const MAX_JSON_ITEMS: usize = 100_000;
pub(crate) const MAX_JSON_STRING_BYTES: usize = 64 * 1024;

struct StrictValue;

impl<'de> DeserializeSeed<'de> for StrictValue {
    type Value = Value;

    fn deserialize<D>(self, deserializer: D) -> core::result::Result<Self::Value, D::Error>
    where
        D: serde::Deserializer<'de>,
    {
        deserializer.deserialize_any(StrictValueVisitor)
    }
}

struct StrictValueVisitor;

impl<'de> Visitor<'de> for StrictValueVisitor {
    type Value = Value;

    fn expecting(&self, formatter: &mut core::fmt::Formatter<'_>) -> core::fmt::Result {
        formatter.write_str("a JSON value without duplicate keys or floating-point numbers")
    }

    fn visit_bool<E>(self, value: bool) -> core::result::Result<Self::Value, E> {
        Ok(Value::Bool(value))
    }

    fn visit_i64<E>(self, value: i64) -> core::result::Result<Self::Value, E> {
        Ok(Value::Number(Number::from(value)))
    }

    fn visit_u64<E>(self, value: u64) -> core::result::Result<Self::Value, E> {
        Ok(Value::Number(Number::from(value)))
    }

    fn visit_f64<E>(self, _value: f64) -> core::result::Result<Self::Value, E>
    where
        E: serde::de::Error,
    {
        Err(E::custom("floating-point values are prohibited"))
    }

    fn visit_str<E>(self, value: &str) -> core::result::Result<Self::Value, E>
    where
        E: serde::de::Error,
    {
        Ok(Value::String(value.to_owned()))
    }

    fn visit_string<E>(self, value: String) -> core::result::Result<Self::Value, E> {
        Ok(Value::String(value))
    }

    fn visit_none<E>(self) -> core::result::Result<Self::Value, E> {
        Ok(Value::Null)
    }

    fn visit_unit<E>(self) -> core::result::Result<Self::Value, E> {
        Ok(Value::Null)
    }

    fn visit_seq<A>(self, mut sequence: A) -> core::result::Result<Self::Value, A::Error>
    where
        A: SeqAccess<'de>,
    {
        let mut values = Vec::new();
        while let Some(value) = sequence.next_element_seed(StrictValue)? {
            values.push(value);
        }
        Ok(Value::Array(values))
    }

    fn visit_map<A>(self, mut mapping: A) -> core::result::Result<Self::Value, A::Error>
    where
        A: MapAccess<'de>,
    {
        let mut keys = BTreeSet::new();
        let mut values = Map::new();
        while let Some(key) = mapping.next_key::<String>()? {
            if !keys.insert(key.clone()) {
                return Err(A::Error::custom(format!("duplicate JSON key: {key}")));
            }
            values.insert(key, mapping.next_value_seed(StrictValue)?);
        }
        Ok(Value::Object(values))
    }
}

pub fn parse_strict_json(bytes: &[u8], context: &str) -> Result<Value> {
    let mut deserializer = serde_json::Deserializer::from_slice(bytes);
    let value = StrictValue
        .deserialize(&mut deserializer)
        .map_err(|error| {
            let code = if error.to_string().contains("duplicate JSON key") {
                DataErrorCode::DuplicateJsonKey
            } else {
                DataErrorCode::InvalidJson
            };
            DataError::new(code, format!("{context}: {error}"))
        })?;
    deserializer.end().map_err(|error| {
        DataError::new(
            DataErrorCode::InvalidJson,
            format!("{context}: trailing input: {error}"),
        )
    })?;
    let mut items = 0_usize;
    validate_limits(&value, 1, &mut items, context)?;
    Ok(value)
}

fn validate_limits(value: &Value, depth: usize, items: &mut usize, context: &str) -> Result<()> {
    if depth > MAX_JSON_DEPTH {
        return Err(DataError::new(
            DataErrorCode::ResourceLimit,
            format!("{context}: JSON nesting"),
        ));
    }
    *items = items.checked_add(1).ok_or_else(|| {
        DataError::new(
            DataErrorCode::ResourceLimit,
            format!("{context}: JSON item count"),
        )
    })?;
    if *items > MAX_JSON_ITEMS {
        return Err(DataError::new(
            DataErrorCode::ResourceLimit,
            format!("{context}: JSON item count"),
        ));
    }
    match value {
        Value::String(text) if text.len() > MAX_JSON_STRING_BYTES => Err(DataError::new(
            DataErrorCode::ResourceLimit,
            format!("{context}: JSON string bytes"),
        )),
        Value::Array(values) => {
            for child in values {
                validate_limits(child, depth + 1, items, context)?;
            }
            Ok(())
        }
        Value::Object(values) => {
            for (key, child) in values {
                if key.len() > MAX_JSON_STRING_BYTES {
                    return Err(DataError::new(
                        DataErrorCode::ResourceLimit,
                        format!("{context}: JSON key bytes"),
                    ));
                }
                validate_limits(child, depth + 1, items, context)?;
            }
            Ok(())
        }
        _ => Ok(()),
    }
}

pub fn canonical_json(value: &Value, context: &str) -> Result<Vec<u8>> {
    serde_json::to_vec(value).map_err(|error| {
        DataError::new(
            DataErrorCode::InvalidJson,
            format!("{context}: canonical encoding: {error}"),
        )
    })
}

#[cfg(test)]
mod tests {
    use super::{canonical_json, parse_strict_json};
    use crate::DataErrorCode;

    #[test]
    fn rejects_duplicate_keys() {
        let error = parse_strict_json(br#"{"a":1,"a":2}"#, "fixture").expect_err("duplicate");
        assert_eq!(error.code(), DataErrorCode::DuplicateJsonKey);
    }

    #[test]
    fn canonicalizes_object_keys_without_changing_strings() {
        let value = parse_strict_json(br#"{"z":{"b":2,"a":"a\u00e7\u00e3o"},"a":1}"#, "fixture")
            .expect("parse");
        assert_eq!(
            canonical_json(&value, "fixture").expect("encode"),
            r#"{"a":1,"z":{"a":"ação","b":2}}"#.as_bytes()
        );
    }

    #[test]
    fn rejects_floating_point_values() {
        assert!(parse_strict_json(br#"{"value":1.5}"#, "fixture").is_err());
    }
}
