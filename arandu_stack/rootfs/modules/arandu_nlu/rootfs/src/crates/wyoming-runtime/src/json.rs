use std::collections::BTreeSet;

use serde::de::{DeserializeSeed, Error as _, MapAccess, SeqAccess, Visitor};
use serde_json::{Map, Number, Value};

use crate::{Result, RuntimeError};

const DUPLICATE_MARKER: &str = "WYOMING_DUPLICATE_KEY";

#[derive(Clone, Copy)]
pub(crate) struct JsonLimits {
    pub max_depth: usize,
    pub max_collection_items: usize,
    pub max_total_items: usize,
    pub max_string_bytes: usize,
}

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
        formatter.write_str("a bounded JSON value without duplicate keys")
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
        Err(E::custom("floating-point JSON values are prohibited"))
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
                return Err(A::Error::custom(DUPLICATE_MARKER));
            }
            values.insert(key, mapping.next_value_seed(StrictValue)?);
        }
        Ok(Value::Object(values))
    }
}

pub(crate) fn parse_strict_json(bytes: &[u8], limits: JsonLimits) -> Result<Value> {
    let mut deserializer = serde_json::Deserializer::from_slice(bytes);
    let value = StrictValue
        .deserialize(&mut deserializer)
        .map_err(|error| {
            if error.to_string().contains(DUPLICATE_MARKER) {
                RuntimeError::DuplicateJsonKey
            } else {
                RuntimeError::InvalidJson
            }
        })?;
    deserializer.end().map_err(|_| RuntimeError::InvalidJson)?;

    let mut total_items = 0_usize;
    validate_limits(&value, 1, &mut total_items, limits)?;
    Ok(value)
}

fn validate_limits(
    value: &Value,
    depth: usize,
    total_items: &mut usize,
    limits: JsonLimits,
) -> Result<()> {
    if depth > limits.max_depth {
        return Err(RuntimeError::JsonDepthLimit);
    }
    *total_items = total_items
        .checked_add(1)
        .ok_or(RuntimeError::JsonCollectionLimit)?;
    if *total_items > limits.max_total_items {
        return Err(RuntimeError::JsonCollectionLimit);
    }

    match value {
        Value::String(text) => {
            if text.len() > limits.max_string_bytes {
                return Err(RuntimeError::JsonStringLimit);
            }
        }
        Value::Array(values) => {
            if values.len() > limits.max_collection_items {
                return Err(RuntimeError::JsonCollectionLimit);
            }
            for child in values {
                validate_limits(child, depth + 1, total_items, limits)?;
            }
        }
        Value::Object(values) => {
            if values.len() > limits.max_collection_items {
                return Err(RuntimeError::JsonCollectionLimit);
            }
            for (key, child) in values {
                if key.len() > limits.max_string_bytes {
                    return Err(RuntimeError::JsonStringLimit);
                }
                validate_limits(child, depth + 1, total_items, limits)?;
            }
        }
        _ => {}
    }

    Ok(())
}
