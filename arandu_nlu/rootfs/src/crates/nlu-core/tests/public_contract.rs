use nlu_core::{
    AbstentionReason, CatalogGeneration, Confidence, CoreError, EntityId, EntityRef, IntentId,
    RequestText, SemanticOutcome, SlotId, SlotValue,
};

#[test]
fn public_types_preserve_foundation_invariants() {
    let request = RequestText::from_utf8(b"FIXTURE_TECNICA_A".to_vec()).expect("request");
    let span = request.span(0, request.len() as u64).expect("span");
    assert_eq!(span.slice(&request), Ok("FIXTURE_TECNICA_A"));

    let generation = CatalogGeneration::new(1).expect("generation");
    let entity = EntityRef::new(
        EntityId::new("fixture_tecnica:entity").expect("entity ID"),
        generation,
    );
    let value = SlotValue::Entity(entity);
    assert!(matches!(value, SlotValue::Entity(_)));

    let slot = SlotId::new("fixture_tecnica:slot").expect("slot ID");
    let intent = IntentId::new("fixture_tecnica:intent").expect("intent ID");
    assert_ne!(slot.as_str(), intent.as_str());
    assert_eq!(
        Confidence::from_basis_points(10_001),
        Err(CoreError::ScoreOutOfRange)
    );
    assert_eq!(
        SemanticOutcome::Abstention(AbstentionReason::Unsupported),
        SemanticOutcome::Abstention(AbstentionReason::Unsupported)
    );
}
