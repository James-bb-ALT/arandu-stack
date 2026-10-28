use std::collections::BTreeMap;

use lang_ptbr::{NormalizedText, Token, TokenClass};
use nlu_core::{IntentId, RequestText, SlotId, Utf8Span};
use nlu_data::{
    DataErrorCode, decode_intent_package,
    intent::{
        DecodedIntentPackage, INTENT_COMPILER_ID, IntentDefinition, IntentPackageIdentity,
        IntentSchema, IntentSlotSchema, IntentValueKind, SlotExtractor,
    },
    sha256_hex,
};

use crate::{
    IntentClarification, IntentEngineError, IntentMatch, IntentSlotValue,
    RecognitionAbstentionReason, RecognitionOutcome, Result, SlotBinding, invalid_artifact,
    invalid_configuration, invalid_text, resource_limit,
};

pub const INTENT_SCHEMA_ID: &str = "p09-intent-schema-v1";
pub const INTENT_ALGORITHM_ID: &str = "p09-weighted-marker-slot-extraction-v1";
pub const INTENT_CONFIGURATION_ID: &str = "p09-intent-config-v1";
pub const INTENT_PACKAGE_SHA256: &str =
    "56140cfa9d93377145aac132a174ed79262e9f454b6e991118477e3ad54bddda";
pub const INTENT_MANIFEST_SHA256: &str =
    "cdef4653c0edc5d819fc52ec0c03976f6df66d0036dff9828b410f699f44ffc3";

const BUNDLED_PACKAGE: &[u8] = include_bytes!("../../../data/intents/p09/package.bin");
const BUNDLED_MANIFEST: &[u8] = include_bytes!("../../../data/intents/p09/package-manifest.json");
const SOURCE_ID: &str = "project-authored-synthetic-ptbr-v1";
const CORPUS_VERSION: &str = "1.0.0";
const GENERATOR_ID: &str = "p02-generator-v1";
const SOURCE_LICENSE: &str = "Apache-2.0";
const LOCALE: &str = "pt-BR";
const CLAIM_SCOPE: &str = "internal_conformance_only";
const LINGUISTIC_INPUT: &str = "train_only";
const SOURCE_MANIFEST_SHA256: &str =
    "d9e1ca32ec0f92aa6b5fc6c1d4f232f93389e0bf8031267d23daddf7287006c5";
const SPECIFICATION_SHA256: &str =
    "f72451f03d1a5e2e4955b5d2857bd7d33b3b012912d920e53a3d85719f14859d";
const GENERATOR_SHA256: &str = "ff8817afc2c2f13d539ab7d10720cab407d769ca3a6189dcc55d43ea052689d1";
const PHYSICAL_TRAIN_SHA256: &str =
    "23d2bc8c8fcde80d1a9560d42219484bc34e9198c791ccadf5d4b56413a81b64";
const PHYSICAL_TRAIN_RECORDS: u64 = 960;

pub struct IntentEngine {
    schema: IntentSchema,
}

impl IntentEngine {
    pub fn bundled() -> Result<Self> {
        Self::from_artifact(BUNDLED_PACKAGE, BUNDLED_MANIFEST)
    }

    pub fn from_artifact(package: &[u8], manifest: &[u8]) -> Result<Self> {
        if sha256_hex(package).map_err(|_| invalid_artifact("package hash"))?
            != INTENT_PACKAGE_SHA256
            || sha256_hex(manifest).map_err(|_| invalid_artifact("manifest hash"))?
                != INTENT_MANIFEST_SHA256
        {
            return Err(invalid_artifact("artifact substitution"));
        }
        let decoded = decode_intent_package(package, manifest, &package_identity())
            .map_err(map_artifact_error)?;
        Self::from_decoded(decoded)
    }

    fn from_decoded(decoded: DecodedIntentPackage) -> Result<Self> {
        validate_artifact_identity(&decoded)?;
        validate_runtime_schema(decoded.schema())?;
        Ok(Self {
            schema: decoded.schema().clone(),
        })
    }

    pub fn recognize(&self, source: &RequestText) -> Result<RecognitionOutcome> {
        let normalized =
            NormalizedText::from_request(source.clone()).map_err(|_| invalid_text("normalize"))?;
        let tokenization = normalized
            .tokenize()
            .map_err(|_| invalid_text("tokenize"))?;
        let tokens = tokenization.tokens();
        let mut candidates = Vec::new();

        for definition in self.schema.intents() {
            if let Some(candidate) = build_candidate(definition, tokens, &normalized, source)? {
                let key = candidate.semantic_key(source)?;
                candidates.push(RankedCandidate {
                    intent_match: candidate,
                    semantic_key: key,
                });
            }
        }
        if candidates.len() > usize::from(self.schema.ranking().maximum_candidates()) {
            return Err(resource_limit("candidate count"));
        }
        candidates.sort_by(|left, right| {
            right
                .intent_match
                .score()
                .cmp(&left.intent_match.score())
                .then_with(|| left.semantic_key.cmp(&right.semantic_key))
        });
        if candidates
            .windows(2)
            .any(|pair| pair[0].semantic_key == pair[1].semantic_key)
        {
            return Err(invalid_configuration("duplicate semantic candidate"));
        }

        let Some(best) = candidates
            .first()
            .map(|candidate| candidate.intent_match.score())
        else {
            return Ok(RecognitionOutcome::Abstention(
                RecognitionAbstentionReason::InsufficientEvidence,
            ));
        };
        let margin = self.schema.ranking().ambiguity_margin();
        let alternatives = candidates
            .into_iter()
            .take_while(|candidate| best - candidate.intent_match.score() <= margin)
            .map(|candidate| candidate.intent_match)
            .collect::<Vec<_>>();
        match alternatives.len() {
            0 => Err(invalid_configuration("empty ranked candidate set")),
            1 => Ok(RecognitionOutcome::Match(
                alternatives
                    .into_iter()
                    .next()
                    .ok_or_else(|| invalid_configuration("missing ranked candidate"))?,
            )),
            _ => Ok(RecognitionOutcome::Clarification(IntentClarification::new(
                alternatives,
            )?)),
        }
    }
}

impl core::fmt::Debug for IntentEngine {
    fn fmt(&self, formatter: &mut core::fmt::Formatter<'_>) -> core::fmt::Result {
        formatter
            .debug_struct("IntentEngine")
            .field("intent_count", &self.schema.intents().len())
            .finish()
    }
}

struct RankedCandidate {
    intent_match: IntentMatch,
    semantic_key: Vec<u8>,
}

#[derive(Clone)]
struct Capture {
    end_token: usize,
    span: Utf8Span,
}

fn build_candidate(
    definition: &IntentDefinition,
    tokens: &[Token],
    normalized: &NormalizedText,
    source: &RequestText,
) -> Result<Option<IntentMatch>> {
    let mut score = 0_u16;
    let mut evidence = Vec::new();
    for marker in definition.markers() {
        let Some(token) = tokens.iter().find(|token| {
            token
                .normalized_slice(normalized)
                .is_ok_and(|value| value == marker.text())
        }) else {
            continue;
        };
        score = score
            .checked_add(marker.weight())
            .ok_or_else(|| resource_limit("candidate score"))?;
        evidence.push(token.original_span().clone());
    }
    if score < definition.evidence_threshold() {
        return Ok(None);
    }

    let mut captures = BTreeMap::<(Box<str>, u16), Capture>::new();
    let mut bindings = BTreeMap::<(Box<str>, u16), SlotBinding>::new();
    for slot in definition.slots() {
        if matches!(slot.extractor(), SlotExtractor::NumberAfterCapture { .. }) {
            continue;
        }
        let Some(capture) = extract_capture(slot.extractor(), tokens, normalized, source)? else {
            return Ok(None);
        };
        let value = match slot.value_kind() {
            IntentValueKind::Mention => IntentSlotValue::Mention(capture.span.clone()),
            IntentValueKind::Text => IntentSlotValue::Text(capture.span.clone()),
            IntentValueKind::Integer => {
                return Err(invalid_configuration("integer capture extractor"));
            }
        };
        let key = (Box::<str>::from(slot.role()), slot.occurrence());
        let binding = make_binding(slot, source, value, capture.span.clone())?;
        if captures.insert(key.clone(), capture).is_some()
            || bindings.insert(key, binding).is_some()
        {
            return Err(invalid_configuration("duplicate capture identity"));
        }
    }

    for slot in definition.slots() {
        let SlotExtractor::NumberAfterCapture {
            after_role,
            after_occurrence,
            scale,
        } = slot.extractor()
        else {
            continue;
        };
        let Some(capture) =
            captures.get(&(Box::<str>::from(after_role.as_str()), *after_occurrence))
        else {
            return Err(invalid_configuration("missing capture reference"));
        };
        let Some((value, evidence_span)) =
            extract_integer(tokens, normalized, capture.end_token, *scale)?
        else {
            return Ok(None);
        };
        if !slot
            .minimum()
            .zip(slot.maximum())
            .is_some_and(|(minimum, maximum)| (minimum..=maximum).contains(&value))
        {
            return Ok(None);
        }
        let key = (Box::<str>::from(slot.role()), slot.occurrence());
        let binding = make_binding(slot, source, IntentSlotValue::Integer(value), evidence_span)?;
        if bindings.insert(key, binding).is_some() {
            return Err(invalid_configuration("duplicate binding identity"));
        }
    }

    let mut ordered = Vec::with_capacity(definition.slots().len());
    for slot in definition.slots() {
        let key = (Box::<str>::from(slot.role()), slot.occurrence());
        let Some(binding) = bindings.remove(&key) else {
            return Ok(None);
        };
        ordered.push(binding);
    }
    if !bindings.is_empty() {
        return Err(invalid_configuration("unconsumed slot binding"));
    }
    let intent =
        IntentId::new(definition.intent_id()).map_err(|_| invalid_configuration("intent ID"))?;
    IntentMatch::new(source, intent, score, evidence, ordered).map(Some)
}

fn make_binding(
    slot: &IntentSlotSchema,
    source: &RequestText,
    value: IntentSlotValue,
    evidence: Utf8Span,
) -> Result<SlotBinding> {
    let id = SlotId::new(slot.slot_id()).map_err(|_| invalid_configuration("slot ID"))?;
    SlotBinding::new(
        source,
        id,
        Box::from(slot.role()),
        slot.occurrence(),
        value,
        evidence,
    )
}

fn extract_capture(
    extractor: &SlotExtractor,
    tokens: &[Token],
    normalized: &NormalizedText,
    source: &RequestText,
) -> Result<Option<Capture>> {
    let range = match extractor {
        SlotExtractor::AnchoredTokens {
            anchor,
            anchor_occurrence,
            token_count,
        } => {
            let Some(start) = anchor_index(tokens, normalized, anchor, *anchor_occurrence) else {
                return Ok(None);
            };
            token_range(start, usize::from(*token_count), tokens.len())
        }
        SlotExtractor::TokensBeforeAnchor {
            anchor,
            anchor_occurrence,
            skip_tokens,
            token_count,
        } => {
            let Some(anchor) = anchor_index(tokens, normalized, anchor, *anchor_occurrence) else {
                return Ok(None);
            };
            let Some(end) = anchor.checked_sub(usize::from(*skip_tokens)) else {
                return Ok(None);
            };
            let Some(start) = end.checked_sub(usize::from(*token_count)) else {
                return Ok(None);
            };
            Some((start, end))
        }
        SlotExtractor::LastToken => token_range(tokens.len().saturating_sub(1), 1, tokens.len()),
        SlotExtractor::LastTokens { token_count } => {
            let count = usize::from(*token_count);
            tokens
                .len()
                .checked_sub(count)
                .map(|start| (start, tokens.len()))
        }
        SlotExtractor::NumberAfterCapture { .. } => {
            return Err(invalid_configuration("nested number capture"));
        }
    };
    let Some((start, end)) = range else {
        return Ok(None);
    };
    if start >= end || end > tokens.len() {
        return Ok(None);
    }
    let first = &tokens[start];
    let last = &tokens[end - 1];
    let span = source
        .span(
            u64::from(first.original_span().start()),
            u64::from(last.original_span().end()),
        )
        .map_err(|_| invalid_text("capture span"))?;
    Ok(Some(Capture {
        end_token: end,
        span,
    }))
}

fn token_range(start: usize, count: usize, limit: usize) -> Option<(usize, usize)> {
    let end = start.checked_add(count)?;
    (count > 0 && end <= limit).then_some((start, end))
}

fn anchor_index(
    tokens: &[Token],
    normalized: &NormalizedText,
    anchor: &str,
    occurrence: u16,
) -> Option<usize> {
    tokens
        .iter()
        .enumerate()
        .filter(|(_, token)| {
            token
                .normalized_slice(normalized)
                .is_ok_and(|value| value == anchor)
        })
        .nth(usize::from(occurrence))
        .map(|(index, _)| index)
}

fn extract_integer(
    tokens: &[Token],
    normalized: &NormalizedText,
    start: usize,
    scale: i64,
) -> Result<Option<(i64, Utf8Span)>> {
    for token in tokens.iter().skip(start) {
        if token.class() != TokenClass::Number {
            continue;
        }
        let text = token
            .normalized_slice(normalized)
            .map_err(|_| invalid_text("integer token"))?;
        if !text.bytes().all(|byte| byte.is_ascii_digit()) {
            return Ok(None);
        }
        let Ok(value) = text.parse::<i64>() else {
            return Ok(None);
        };
        let Some(scaled) = value.checked_mul(scale) else {
            return Err(resource_limit("integer scale"));
        };
        return Ok(Some((scaled, token.original_span().clone())));
    }
    Ok(None)
}

fn validate_runtime_schema(schema: &IntentSchema) -> Result<()> {
    for definition in schema.intents() {
        for marker in definition.markers() {
            let normalized = NormalizedText::new(marker.text().to_owned())
                .map_err(|_| invalid_configuration("marker normalization"))?;
            let tokens = normalized
                .tokenize()
                .map_err(|_| invalid_configuration("marker tokenization"))?;
            if tokens.len() != 1
                || tokens.tokens()[0]
                    .normalized_slice(&normalized)
                    .map_or(true, |value| value != marker.text())
            {
                return Err(invalid_configuration("marker is not one exact token"));
            }
        }
    }
    Ok(())
}

fn validate_artifact_identity(decoded: &DecodedIntentPackage) -> Result<()> {
    let source = decoded.manifest().source();
    if decoded.manifest().compiler_id() != INTENT_COMPILER_ID
        || decoded.manifest().package_sha256() != INTENT_PACKAGE_SHA256
        || source.source_id() != SOURCE_ID
        || source.corpus_version() != CORPUS_VERSION
        || source.generator_id() != GENERATOR_ID
        || source.license() != SOURCE_LICENSE
        || source.locale() != LOCALE
        || source.claim_scope() != CLAIM_SCOPE
        || source.linguistic_input() != LINGUISTIC_INPUT
        || source.source_manifest_sha256() != SOURCE_MANIFEST_SHA256
        || source.specification_sha256() != SPECIFICATION_SHA256
        || source.generator_sha256() != GENERATOR_SHA256
        || source.physical_train_sha256() != PHYSICAL_TRAIN_SHA256
        || source.physical_train_records() != PHYSICAL_TRAIN_RECORDS
    {
        return Err(invalid_artifact("artifact identity"));
    }
    Ok(())
}

fn package_identity() -> IntentPackageIdentity {
    IntentPackageIdentity::new(
        INTENT_SCHEMA_ID,
        INTENT_ALGORITHM_ID,
        INTENT_COMPILER_ID,
        INTENT_CONFIGURATION_ID,
    )
}

fn map_artifact_error(error: nlu_data::DataError) -> IntentEngineError {
    if error.code() == DataErrorCode::ResourceLimit {
        resource_limit("artifact limits")
    } else {
        invalid_artifact("artifact decode")
    }
}

#[cfg(test)]
mod tests {
    use nlu_data::{compile_intent_package, parse_strict_json};
    use serde_json::Value;

    use super::*;

    const FIXTURE_SCHEMA: &str = r#"{
      "schema_version":1,
      "schema_id":"p09-intent-schema-v1",
      "algorithm_id":"p09-weighted-marker-slot-extraction-v1",
      "configuration_id":"p09-intent-config-v1",
      "source":{
        "source_id":"project-authored-synthetic-ptbr-v1",
        "corpus_version":"1.0.0",
        "generator_id":"p02-generator-v1",
        "license":"Apache-2.0",
        "locale":"pt-BR",
        "claim_scope":"internal_conformance_only",
        "source_manifest_sha256":"d9e1ca32ec0f92aa6b5fc6c1d4f232f93389e0bf8031267d23daddf7287006c5",
        "specification_sha256":"f72451f03d1a5e2e4955b5d2857bd7d33b3b012912d920e53a3d85719f14859d",
        "generator_sha256":"ff8817afc2c2f13d539ab7d10720cab407d769ca3a6189dcc55d43ea052689d1",
        "physical_train_sha256":"23d2bc8c8fcde80d1a9560d42219484bc34e9198c791ccadf5d4b56413a81b64",
        "physical_train_records":960,
        "linguistic_input":"train_only"
      },
      "ranking":{
        "score_maximum":10000,
        "ambiguity_margin":500,
        "maximum_candidates":32,
        "maximum_slots_per_candidate":8
      },
      "intents":[
        {
          "external_intent":"FixtureA",
          "intent_id":"fixture_tecnica:intent_a",
          "evidence_threshold":5000,
          "markers":[{"text":"FIXTURE","weight":5000}],
          "slots":[{
            "slot_id":"fixture_tecnica:slot",
            "role":"target",
            "occurrence":0,
            "value_kind":"text",
            "extractor":{"kind":"last_token"}
          }]
        },
        {
          "external_intent":"FixtureB",
          "intent_id":"fixture_tecnica:intent_b",
          "evidence_threshold":4500,
          "markers":[{"text":"FIXTURE","weight":4500}],
          "slots":[{
            "slot_id":"fixture_tecnica:slot",
            "role":"target",
            "occurrence":0,
            "value_kind":"text",
            "extractor":{"kind":"last_token"}
          }]
        }
      ]
    }"#;

    fn fixture_engine(schema: &[u8]) -> IntentEngine {
        let compiled = compile_intent_package(schema).expect("compile fixture");
        let decoded = decode_intent_package(
            compiled.package_bytes(),
            compiled.manifest_bytes(),
            &package_identity(),
        )
        .expect("decode fixture");
        validate_runtime_schema(decoded.schema()).expect("runtime schema");
        IntentEngine {
            schema: decoded.schema().clone(),
        }
    }

    #[test]
    fn inclusive_margin_clarifies_in_canonical_order() {
        let engine = fixture_engine(FIXTURE_SCHEMA.as_bytes());
        let source = RequestText::new("FIXTURE TECNICA".into()).expect("source");
        let RecognitionOutcome::Clarification(value) =
            engine.recognize(&source).expect("recognize")
        else {
            panic!("expected clarification");
        };
        assert_eq!(value.alternatives().len(), 2);
        assert_eq!(
            value.alternatives()[0].intent().as_str(),
            "fixture_tecnica:intent_a"
        );
        assert_eq!(
            value.alternatives()[1].intent().as_str(),
            "fixture_tecnica:intent_b"
        );
    }

    #[test]
    fn schema_permutation_preserves_canonical_output_bytes() {
        let original = fixture_engine(FIXTURE_SCHEMA.as_bytes());
        let mut value = parse_strict_json(FIXTURE_SCHEMA.as_bytes(), "fixture").expect("schema");
        let intents = value["intents"].as_array_mut().expect("intents");
        intents.reverse();
        for intent in intents {
            intent["markers"].as_array_mut().expect("markers").reverse();
            intent["slots"].as_array_mut().expect("slots").reverse();
        }
        let permuted_bytes = serde_json::to_vec(&value).expect("permuted schema");
        let permuted = fixture_engine(&permuted_bytes);
        let source = RequestText::new("FIXTURE TECNICA".into()).expect("source");

        let original_output = original.recognize(&source).expect("original");
        let permuted_output = permuted.recognize(&source).expect("permuted");
        assert_eq!(original_output, permuted_output);
        assert_eq!(
            original_output
                .canonical_bytes(&source)
                .expect("original bytes"),
            permuted_output
                .canonical_bytes(&source)
                .expect("permuted bytes")
        );
    }

    #[test]
    fn candidate_outside_inclusive_margin_does_not_clarify() {
        let mut value = parse_strict_json(FIXTURE_SCHEMA.as_bytes(), "fixture").expect("schema");
        let second = &mut value["intents"].as_array_mut().expect("intents")[1];
        second["markers"][0]["weight"] = Value::from(4_499);
        second["evidence_threshold"] = Value::from(4_499);
        let changed = serde_json::to_vec(&value).expect("changed schema");
        let engine = fixture_engine(&changed);
        let source = RequestText::new("FIXTURE TECNICA".into()).expect("source");

        let RecognitionOutcome::Match(value) = engine.recognize(&source).expect("recognize") else {
            panic!("expected unique leading match");
        };
        assert_eq!(value.intent().as_str(), "fixture_tecnica:intent_a");
    }

    #[test]
    fn runtime_schema_rejects_a_marker_that_is_not_one_token() {
        let mut value = parse_strict_json(FIXTURE_SCHEMA.as_bytes(), "fixture").expect("schema");
        for intent in value["intents"].as_array_mut().expect("intents") {
            intent["markers"][0]["text"] = Value::from("FIXTURE\u{2014}TECNICA");
        }
        let changed = serde_json::to_vec(&value).expect("changed schema");
        let compiled = compile_intent_package(&changed).expect("compile fixture");
        let decoded = decode_intent_package(
            compiled.package_bytes(),
            compiled.manifest_bytes(),
            &package_identity(),
        )
        .expect("decode fixture");
        assert_eq!(
            validate_runtime_schema(decoded.schema())
                .expect_err("multi-token marker")
                .code(),
            crate::IntentEngineErrorCode::InvalidConfiguration
        );
    }

    #[test]
    fn unknown_input_abstains_without_payload() {
        let engine = fixture_engine(FIXTURE_SCHEMA.as_bytes());
        let source = RequestText::new("SEM_MARCADOR_TECNICO".into()).expect("source");
        assert_eq!(
            engine.recognize(&source).expect("recognize"),
            RecognitionOutcome::Abstention(RecognitionAbstentionReason::InsufficientEvidence)
        );
    }

    #[test]
    fn bundled_hyphen_marker_is_one_preserved_token() {
        let engine = IntentEngine::bundled().expect("bundled engine");
        let source = RequestText::new(
            "transmita confirmacao 1 do setor sala em alto-falante 1 do setor sala".into(),
        )
        .expect("source");
        let RecognitionOutcome::Match(value) = engine.recognize(&source).expect("recognize") else {
            panic!("expected broadcast match");
        };
        assert_eq!(value.intent().as_str(), "ha:hass_broadcast");
        assert_eq!(value.slots().len(), 2);
    }

    #[test]
    fn bundled_artifact_rejects_substitution_before_recognition() {
        let mut package = BUNDLED_PACKAGE.to_vec();
        package[0] ^= 1;
        assert_eq!(
            IntentEngine::from_artifact(&package, BUNDLED_MANIFEST)
                .expect_err("substituted package")
                .code(),
            crate::IntentEngineErrorCode::InvalidArtifact
        );
    }

    #[test]
    fn multibyte_original_spans_remain_checked() {
        let engine = IntentEngine::bundled().expect("bundled engine");
        let source =
            RequestText::new("consulte a temperatura em termo\u{302}metro 1 do setor sala".into())
                .expect("source");
        let RecognitionOutcome::Match(value) = engine.recognize(&source).expect("recognize") else {
            panic!("expected climate match");
        };
        let slot = &value.slots()[0];
        assert_eq!(
            slot.evidence().slice(&source).expect("slot evidence"),
            "termo\u{302}metro 1 do setor sala"
        );
        assert!(slot.evidence().belongs_to(&source));
    }

    #[test]
    fn canonical_output_rejects_a_foreign_source() {
        let engine = fixture_engine(FIXTURE_SCHEMA.as_bytes());
        let source = RequestText::new("FIXTURE TECNICA".into()).expect("source");
        let foreign = RequestText::new("FIXTURE TECNICA".into()).expect("foreign source");
        let output = engine.recognize(&source).expect("recognize");
        assert_eq!(
            output
                .canonical_bytes(&foreign)
                .expect_err("foreign source")
                .code(),
            crate::IntentEngineErrorCode::SpanSourceMismatch
        );
    }
}
