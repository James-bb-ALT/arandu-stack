//! Deterministic, passive PT-BR interpretation for the Local NLU MLP.

pub mod contextual;
pub mod extraction;
mod model;
mod normalize;
mod parser;
pub mod resolution;
pub mod server;
pub mod v2;
pub mod v3;

pub use extraction::{
    ConstraintEvidence, ConstraintKind, ExtractionConstraint, Inheritance, InheritanceVia, Mention,
    MentionExtraction, OperationSegment, UnlinkedKind, UnlinkedRef, extract,
};
pub use model::{
    Action, Area, Catalog, CatalogEntity, InterpretRequest, InterpretResponse, Operation,
};
pub use parser::interpret;
pub use resolution::{
    ResolutionArea, ResolutionCatalog, ResolutionConstraints, ResolutionEntity, ResolutionEvidence,
    ResolutionOutcome, ResolutionRequest, resolve_entity,
};
pub use v2::{InterpretRequestV2, InterpretResponseV2, interpret_v2};
pub use v3::{InterpretRequestV3, InterpretResponseV3, interpret_v3};
