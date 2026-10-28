use std::collections::{BTreeMap, BTreeSet};

use crate::authorization::{EmissionMode, EmissionPolicy};
use crate::hash::{sha1_hex, sha256_hex};
use crate::json::{JsonValue, canonical_json, object};
use crate::path::{validate_identifier, validate_release_path};
use crate::{PackagerError, PackagerErrorCode, Result};

const SPDX_DOCUMENT_ID: &str = "SPDXRef-DOCUMENT";
const SPDX_PACKAGE_ID: &str = "SPDXRef-Package";
const SPDX_CREATOR: &str = "Tool: release-packager-0.1.0";
const NORMALIZED_CREATION_TIME: &str = "1970-01-01T00:00:00Z";

#[derive(Clone, Debug, Eq, PartialEq)]
pub struct SpdxFile {
    pub path: String,
    pub bytes: Vec<u8>,
    pub license_concluded: String,
    pub license_info_in_files: Vec<String>,
    pub copyright_text: String,
}

#[derive(Clone, Debug, Eq, PartialEq)]
pub struct SpdxPackageInput {
    pub name: String,
    pub version: String,
    pub download_location: String,
    pub license_concluded: String,
    pub license_declared: String,
    pub copyright_text: String,
    pub files: Vec<SpdxFile>,
}

#[derive(Clone, Debug, Eq, PartialEq)]
pub struct SpdxDocumentInput {
    pub name: String,
    pub namespace: String,
    pub package: SpdxPackageInput,
}

#[derive(Clone, Debug, Eq, PartialEq)]
pub struct EmittedSpdx {
    bytes: Vec<u8>,
    sha256: String,
    mode: EmissionMode,
}

impl EmittedSpdx {
    pub fn bytes(&self) -> &[u8] {
        &self.bytes
    }

    pub fn sha256(&self) -> &str {
        &self.sha256
    }

    pub const fn mode(&self) -> EmissionMode {
        self.mode
    }
}

pub fn emit_spdx_23_json(
    input: &SpdxDocumentInput,
    policy: &EmissionPolicy,
) -> Result<EmittedSpdx> {
    policy.validate()?;
    validate_document(input)?;
    let files = sorted_files(&input.package.files)?;

    let mut file_values = Vec::with_capacity(files.len());
    let mut relationships = vec![object([
        (
            "relatedSpdxElement",
            JsonValue::String(SPDX_PACKAGE_ID.to_owned()),
        ),
        (
            "relationshipType",
            JsonValue::String("DESCRIBES".to_owned()),
        ),
        (
            "spdxElementId",
            JsonValue::String(SPDX_DOCUMENT_ID.to_owned()),
        ),
    ])];
    let mut file_sha1s = Vec::with_capacity(files.len());
    let mut licenses_from_files = BTreeSet::new();

    for (index, file) in files.iter().enumerate() {
        let spdx_id = format!("SPDXRef-File-{:06}", index + 1);
        let sha1 = sha1_hex(&file.bytes)?;
        let sha256 = sha256_hex(&file.bytes)?;
        file_sha1s.push(sha1.clone());
        let mut licenses = file.license_info_in_files.clone();
        licenses.sort();
        licenses.dedup();
        licenses_from_files.extend(licenses.iter().cloned());
        file_values.push(object([
            ("SPDXID", JsonValue::String(spdx_id.clone())),
            (
                "checksums",
                JsonValue::Array(vec![
                    object([
                        ("algorithm", JsonValue::String("SHA1".to_owned())),
                        ("checksumValue", JsonValue::String(sha1)),
                    ]),
                    object([
                        ("algorithm", JsonValue::String("SHA256".to_owned())),
                        ("checksumValue", JsonValue::String(sha256)),
                    ]),
                ]),
            ),
            (
                "copyrightText",
                JsonValue::String(file.copyright_text.clone()),
            ),
            ("fileName", JsonValue::String(format!("./{}", file.path))),
            (
                "licenseConcluded",
                JsonValue::String(file.license_concluded.clone()),
            ),
            (
                "licenseInfoInFiles",
                JsonValue::Array(licenses.into_iter().map(JsonValue::String).collect()),
            ),
        ]));
        relationships.push(object([
            ("relatedSpdxElement", JsonValue::String(spdx_id)),
            ("relationshipType", JsonValue::String("CONTAINS".to_owned())),
            (
                "spdxElementId",
                JsonValue::String(SPDX_PACKAGE_ID.to_owned()),
            ),
        ]));
    }
    file_sha1s.sort();
    let verification_material = file_sha1s.concat();
    let verification_code = sha1_hex(verification_material.as_bytes())?;

    let package = object([
        ("SPDXID", JsonValue::String(SPDX_PACKAGE_ID.to_owned())),
        (
            "copyrightText",
            JsonValue::String(input.package.copyright_text.clone()),
        ),
        (
            "downloadLocation",
            JsonValue::String(input.package.download_location.clone()),
        ),
        ("filesAnalyzed", JsonValue::Bool(true)),
        (
            "licenseConcluded",
            JsonValue::String(input.package.license_concluded.clone()),
        ),
        (
            "licenseDeclared",
            JsonValue::String(input.package.license_declared.clone()),
        ),
        (
            "licenseInfoFromFiles",
            JsonValue::Array(
                licenses_from_files
                    .into_iter()
                    .map(JsonValue::String)
                    .collect(),
            ),
        ),
        ("name", JsonValue::String(input.package.name.clone())),
        (
            "packageVerificationCode",
            object([(
                "packageVerificationCodeValue",
                JsonValue::String(verification_code),
            )]),
        ),
        (
            "versionInfo",
            JsonValue::String(input.package.version.clone()),
        ),
    ]);

    let document = object([
        ("SPDXID", JsonValue::String(SPDX_DOCUMENT_ID.to_owned())),
        (
            "creationInfo",
            object([
                (
                    "created",
                    JsonValue::String(NORMALIZED_CREATION_TIME.to_owned()),
                ),
                (
                    "creators",
                    JsonValue::Array(vec![JsonValue::String(SPDX_CREATOR.to_owned())]),
                ),
            ]),
        ),
        ("dataLicense", JsonValue::String("CC0-1.0".to_owned())),
        (
            "documentNamespace",
            JsonValue::String(input.namespace.clone()),
        ),
        ("files", JsonValue::Array(file_values)),
        ("name", JsonValue::String(input.name.clone())),
        ("packages", JsonValue::Array(vec![package])),
        ("relationships", JsonValue::Array(relationships)),
        ("spdxVersion", JsonValue::String("SPDX-2.3".to_owned())),
    ]);
    let mut bytes = canonical_json(&document);
    bytes.push(b'\n');
    Ok(EmittedSpdx {
        sha256: sha256_hex(&bytes)?,
        bytes,
        mode: policy.mode(),
    })
}

fn validate_document(input: &SpdxDocumentInput) -> Result<()> {
    validate_identifier(&input.name, "SPDX document name")?;
    validate_identifier(&input.package.name, "SPDX package name")?;
    validate_identifier(&input.package.version, "SPDX package version")?;
    validate_identifier(
        &input.package.download_location,
        "SPDX package download location",
    )?;
    validate_download_location(&input.package.download_location)?;
    validate_license(
        &input.package.license_concluded,
        "SPDX package concluded license",
    )?;
    validate_license(
        &input.package.license_declared,
        "SPDX package declared license",
    )?;
    validate_identifier(&input.package.copyright_text, "SPDX package copyright")?;
    if !(input.namespace.starts_with("https://") || input.namespace.starts_with("urn:"))
        || input
            .namespace
            .bytes()
            .any(|byte| byte.is_ascii_whitespace())
    {
        return Err(PackagerError::new(
            PackagerErrorCode::InvalidSpdx,
            "SPDX document namespace must be a whitespace-free https or urn URI",
        ));
    }
    if input.package.files.is_empty() {
        return Err(PackagerError::new(
            PackagerErrorCode::InvalidSpdx,
            "SPDX package must contain at least one file",
        ));
    }
    for file in &input.package.files {
        validate_release_path(&file.path)?;
        validate_license(&file.license_concluded, "SPDX file concluded license")?;
        validate_identifier(&file.copyright_text, "SPDX file copyright")?;
        if file.license_info_in_files.is_empty() {
            return Err(PackagerError::new(
                PackagerErrorCode::InvalidSpdx,
                format!("SPDX file {:?} has no licenseInfoInFiles", file.path),
            ));
        }
        for license in &file.license_info_in_files {
            validate_license(license, "SPDX file license")?;
        }
    }
    Ok(())
}

fn sorted_files(files: &[SpdxFile]) -> Result<Vec<&SpdxFile>> {
    let mut by_path = BTreeMap::new();
    for file in files {
        if by_path.insert(file.path.as_str(), file).is_some() {
            return Err(PackagerError::new(
                PackagerErrorCode::DuplicatePath,
                format!("duplicate SPDX file path {:?}", file.path),
            ));
        }
    }
    Ok(by_path.into_values().collect())
}

fn validate_license(value: &str, context: &str) -> Result<()> {
    validate_identifier(value, context)?;
    if matches!(value, "NONE" | "NOASSERTION") {
        return Ok(());
    }
    let tokens = tokenize_license_expression(value, context)?;
    let mut cursor = 0_usize;
    parse_or_expression(&tokens, &mut cursor, context)?;
    if cursor != tokens.len() {
        return Err(PackagerError::new(
            PackagerErrorCode::InvalidSpdx,
            format!("{context} has trailing SPDX license tokens"),
        ));
    }
    Ok(())
}

#[derive(Clone, Copy, Debug, Eq, PartialEq)]
enum LicenseToken<'a> {
    LeftParenthesis,
    RightParenthesis,
    Word(&'a str),
}

fn tokenize_license_expression<'a>(value: &'a str, context: &str) -> Result<Vec<LicenseToken<'a>>> {
    if !value.is_ascii() {
        return Err(invalid_spdx(format!(
            "{context} must use ASCII SPDX license syntax"
        )));
    }
    let bytes = value.as_bytes();
    let mut tokens = Vec::new();
    let mut cursor = 0_usize;
    while cursor < bytes.len() {
        match bytes[cursor] {
            b' ' | b'\t' => cursor += 1,
            b'(' => {
                tokens.push(LicenseToken::LeftParenthesis);
                cursor += 1;
            }
            b')' => {
                tokens.push(LicenseToken::RightParenthesis);
                cursor += 1;
            }
            byte if is_license_word_byte(byte) => {
                let start = cursor;
                while cursor < bytes.len() && is_license_word_byte(bytes[cursor]) {
                    cursor += 1;
                }
                tokens.push(LicenseToken::Word(&value[start..cursor]));
            }
            _ => {
                return Err(invalid_spdx(format!(
                    "{context} contains an invalid SPDX license byte"
                )));
            }
        }
    }
    if tokens.is_empty() {
        return Err(invalid_spdx(format!(
            "{context} has an empty SPDX license expression"
        )));
    }
    Ok(tokens)
}

fn parse_or_expression(
    tokens: &[LicenseToken<'_>],
    cursor: &mut usize,
    context: &str,
) -> Result<()> {
    parse_and_expression(tokens, cursor, context)?;
    while token_is_word(tokens.get(*cursor), "OR") {
        *cursor += 1;
        parse_and_expression(tokens, cursor, context)?;
    }
    Ok(())
}

fn parse_and_expression(
    tokens: &[LicenseToken<'_>],
    cursor: &mut usize,
    context: &str,
) -> Result<()> {
    parse_with_expression(tokens, cursor, context)?;
    while token_is_word(tokens.get(*cursor), "AND") {
        *cursor += 1;
        parse_with_expression(tokens, cursor, context)?;
    }
    Ok(())
}

fn parse_with_expression(
    tokens: &[LicenseToken<'_>],
    cursor: &mut usize,
    context: &str,
) -> Result<()> {
    parse_license_primary(tokens, cursor, context)?;
    if token_is_word(tokens.get(*cursor), "WITH") {
        *cursor += 1;
        let Some(LicenseToken::Word(exception)) = tokens.get(*cursor) else {
            return Err(invalid_spdx(format!(
                "{context} has WITH without an SPDX exception identifier"
            )));
        };
        validate_license_word(exception, context)?;
        *cursor += 1;
    }
    Ok(())
}

fn parse_license_primary(
    tokens: &[LicenseToken<'_>],
    cursor: &mut usize,
    context: &str,
) -> Result<()> {
    match tokens.get(*cursor) {
        Some(LicenseToken::Word(word)) => {
            validate_license_word(word, context)?;
            *cursor += 1;
            Ok(())
        }
        Some(LicenseToken::LeftParenthesis) => {
            *cursor += 1;
            parse_or_expression(tokens, cursor, context)?;
            if tokens.get(*cursor) != Some(&LicenseToken::RightParenthesis) {
                return Err(invalid_spdx(format!(
                    "{context} has an unclosed SPDX license parenthesis"
                )));
            }
            *cursor += 1;
            Ok(())
        }
        _ => Err(invalid_spdx(format!(
            "{context} expected an SPDX license identifier"
        ))),
    }
}

fn validate_license_word(word: &str, context: &str) -> Result<()> {
    if matches!(word, "AND" | "OR" | "WITH" | "NONE" | "NOASSERTION")
        || !word.bytes().any(|byte| byte.is_ascii_alphanumeric())
        || !word.bytes().all(is_license_word_byte)
    {
        return Err(invalid_spdx(format!(
            "{context} contains an invalid SPDX license identifier"
        )));
    }
    Ok(())
}

fn token_is_word(token: Option<&LicenseToken<'_>>, expected: &str) -> bool {
    matches!(token, Some(LicenseToken::Word(word)) if *word == expected)
}

fn is_license_word_byte(byte: u8) -> bool {
    byte.is_ascii_alphanumeric() || matches!(byte, b'.' | b'-' | b'+' | b':')
}

fn validate_download_location(value: &str) -> Result<()> {
    if matches!(value, "NONE" | "NOASSERTION") {
        return Ok(());
    }
    let Some((scheme, location)) = value.split_once(':') else {
        return Err(invalid_spdx(
            "SPDX download location is not a URI or sentinel",
        ));
    };
    if scheme.is_empty()
        || location.is_empty()
        || !scheme.bytes().enumerate().all(|(index, byte)| {
            if index == 0 {
                byte.is_ascii_alphabetic()
            } else {
                byte.is_ascii_alphanumeric() || matches!(byte, b'+' | b'-' | b'.')
            }
        })
        || value.bytes().any(|byte| byte.is_ascii_whitespace())
    {
        return Err(invalid_spdx("SPDX download location is not a valid URI"));
    }
    Ok(())
}

fn invalid_spdx(message: impl Into<String>) -> PackagerError {
    PackagerError::new(PackagerErrorCode::InvalidSpdx, message)
}

#[cfg(test)]
mod tests {
    use super::{SpdxDocumentInput, SpdxFile, SpdxPackageInput, emit_spdx_23_json};
    use crate::{Architecture, EmissionMode, EmissionPolicy, ReleaseAuthorization};

    #[test]
    fn fixture_tecnica_spdx_is_canonical_and_order_independent() {
        let first = input();
        let mut second = input();
        second.package.files.reverse();
        let first = emit_spdx_23_json(&first, &policy()).expect("first SPDX");
        let second = emit_spdx_23_json(&second, &policy()).expect("second SPDX");
        assert_eq!(first, second);
        assert!(
            first
                .bytes()
                .starts_with(b"{\"SPDXID\":\"SPDXRef-DOCUMENT\"")
        );
        assert!(first.bytes().ends_with(b"\n"));
    }

    #[test]
    fn fixture_tecnica_spdx_rejects_incomplete_license_expression() {
        let mut input = input();
        input.package.license_declared = "Apache-2.0 AND".to_owned();
        assert!(emit_spdx_23_json(&input, &policy()).is_err());
    }

    fn input() -> SpdxDocumentInput {
        SpdxDocumentInput {
            name: "FIXTURE_TECNICA-document".to_owned(),
            namespace: "urn:FIXTURE_TECNICA:spdx".to_owned(),
            package: SpdxPackageInput {
                name: "FIXTURE_TECNICA-package".to_owned(),
                version: "1.0.0".to_owned(),
                download_location: "NOASSERTION".to_owned(),
                license_concluded: "Apache-2.0".to_owned(),
                license_declared: "Apache-2.0".to_owned(),
                copyright_text: "NOASSERTION".to_owned(),
                files: vec![
                    file("z/FIXTURE_TECNICA", b"z"),
                    file("a/FIXTURE_TECNICA", b"a"),
                ],
            },
        }
    }

    fn file(path: &str, bytes: &[u8]) -> SpdxFile {
        SpdxFile {
            path: path.to_owned(),
            bytes: bytes.to_vec(),
            license_concluded: "Apache-2.0".to_owned(),
            license_info_in_files: vec!["Apache-2.0".to_owned()],
            copyright_text: "NOASSERTION".to_owned(),
        }
    }

    fn policy() -> EmissionPolicy {
        EmissionPolicy::new(
            EmissionMode::DryRun,
            vec![Architecture::Amd64],
            ReleaseAuthorization::default(),
        )
    }
}
