mod support;

use ha_adapter::{
    AdapterError, CompanionMode, GraphTopology, NodeOrdinal, RouteAbstention, RouteNode, RoutePlan,
    TargetScope, TransportRoute, TypedOperation, WyomingSafetyAssessment, WyomingSafetyProof,
    select_transport,
};
use support::target;

fn proof() -> WyomingSafetyProof {
    WyomingSafetyProof::new(WyomingSafetyAssessment::reviewed())
        .expect("FIXTURE_TECNICA reviewed proof")
}

fn read_node(ordinal: u16, target_id: u8) -> RouteNode {
    RouteNode::new(
        NodeOrdinal::new(ordinal).expect("FIXTURE_TECNICA ordinal"),
        TypedOperation::read_entity_state(target(target_id)).expect("FIXTURE_TECNICA read"),
        TargetScope::ExactEntity,
        Some(proof()),
        false,
        false,
    )
}

fn route(
    nodes: Vec<RouteNode>,
    topology: GraphTopology,
    partial_safe: bool,
    clarification: bool,
    continuation: bool,
    companion: bool,
) -> TransportRoute {
    let plan = RoutePlan::new(
        nodes,
        topology,
        partial_safe,
        clarification,
        continuation,
        companion,
        None,
    )
    .expect("FIXTURE_TECNICA route plan");
    select_transport(&plan)
}

#[test]
fn wyoming_accepts_only_reviewed_exact_single_target_read_nodes() {
    assert_eq!(
        route(
            vec![read_node(1, 1)],
            GraphTopology::Single,
            true,
            false,
            false,
            true,
        ),
        TransportRoute::WyomingSafeRead { node_count: 1 }
    );
    assert_eq!(
        route(
            vec![read_node(1, 1), read_node(2, 2)],
            GraphTopology::UnorderedIndependent,
            true,
            false,
            false,
            true,
        ),
        TransportRoute::WyomingSafeRead { node_count: 2 }
    );

    let expanding = RouteNode::new(
        NodeOrdinal::new(1).expect("ordinal"),
        TypedOperation::read_entity_state(target(1)).expect("read"),
        TargetScope::Expanding,
        Some(proof()),
        false,
        false,
    );
    assert_eq!(
        route(
            vec![expanding],
            GraphTopology::Single,
            true,
            false,
            false,
            true,
        ),
        TransportRoute::Companion(CompanionMode::Sequential)
    );

    let caller_bound = RouteNode::new(
        NodeOrdinal::new(1).expect("ordinal"),
        TypedOperation::read_entity_state(target(1)).expect("read"),
        TargetScope::ExactEntity,
        Some(proof()),
        true,
        false,
    );
    assert_eq!(
        route(
            vec![caller_bound],
            GraphTopology::Single,
            true,
            false,
            false,
            true,
        ),
        TransportRoute::Companion(CompanionMode::Sequential)
    );

    let effect = RouteNode::new(
        NodeOrdinal::new(1).expect("ordinal"),
        TypedOperation::turn_on(target(1)).expect("turn on"),
        TargetScope::ExactEntity,
        Some(proof()),
        false,
        false,
    );
    assert_eq!(
        route(
            vec![effect],
            GraphTopology::Single,
            true,
            false,
            false,
            true,
        ),
        TransportRoute::Companion(CompanionMode::Sequential)
    );
}

#[test]
fn every_wyoming_proof_property_is_mandatory() {
    let mutations = [
        WyomingSafetyAssessment::new(false, true, true, true, true),
        WyomingSafetyAssessment::new(true, false, true, true, true),
        WyomingSafetyAssessment::new(true, true, false, true, true),
        WyomingSafetyAssessment::new(true, true, true, false, true),
        WyomingSafetyAssessment::new(true, true, true, true, false),
    ];
    for assessment in mutations {
        assert_eq!(
            WyomingSafetyProof::new(assessment),
            Err(AdapterError::InvalidSafetyProof)
        );
    }
}

#[test]
fn ordering_clarification_continuation_and_missing_companion_fail_closed() {
    assert_eq!(
        route(
            vec![read_node(1, 1), read_node(2, 2)],
            GraphTopology::Ordered,
            true,
            false,
            false,
            true,
        ),
        TransportRoute::Companion(CompanionMode::Sequential)
    );
    assert_eq!(
        route(
            vec![read_node(1, 1)],
            GraphTopology::Single,
            true,
            true,
            false,
            true,
        ),
        TransportRoute::Companion(CompanionMode::Sequential)
    );
    assert_eq!(
        route(
            vec![read_node(1, 1)],
            GraphTopology::Single,
            true,
            false,
            true,
            true,
        ),
        TransportRoute::Companion(CompanionMode::Sequential)
    );

    let no_proof = RouteNode::new(
        NodeOrdinal::new(1).expect("ordinal"),
        TypedOperation::read_entity_state(target(1)).expect("read"),
        TargetScope::ExactEntity,
        None,
        false,
        false,
    );
    assert_eq!(
        route(
            vec![no_proof],
            GraphTopology::Single,
            true,
            false,
            false,
            false,
        ),
        TransportRoute::Abstain(RouteAbstention::CompanionUnavailable)
    );

    assert_eq!(
        route(
            vec![read_node(1, 1), read_node(2, 2)],
            GraphTopology::Ordered,
            false,
            false,
            false,
            true,
        ),
        TransportRoute::Abstain(RouteAbstention::UnsafePartialCompletion)
    );
}

#[test]
fn conflicting_and_contradictory_graphs_always_abstain() {
    assert_eq!(
        route(
            vec![read_node(1, 1)],
            GraphTopology::Conflicting,
            true,
            false,
            false,
            true,
        ),
        TransportRoute::Abstain(RouteAbstention::ConflictingGraph)
    );
    assert_eq!(
        route(
            vec![read_node(1, 1)],
            GraphTopology::Contradictory,
            true,
            false,
            false,
            true,
        ),
        TransportRoute::Abstain(RouteAbstention::ContradictoryGraph)
    );
}

#[test]
fn atomic_route_accepts_only_an_exact_read_only_bundle_mapping() {
    let nodes = vec![read_node(1, 1), read_node(2, 2)];
    let targets =
        ha_adapter::TargetSet::new(vec![target(2), target(1)]).expect("FIXTURE_TECNICA targets");
    let bundle = TypedOperation::read_bundled_snapshot(targets).expect("FIXTURE_TECNICA bundle");
    let plan = RoutePlan::new(
        nodes.clone(),
        GraphTopology::AtomicRequired,
        false,
        false,
        false,
        true,
        Some(bundle),
    )
    .expect("atomic plan");
    assert_eq!(
        select_transport(&plan),
        TransportRoute::Companion(CompanionMode::AtomicReadBundle)
    );

    let unavailable = RoutePlan::new(
        nodes.clone(),
        GraphTopology::AtomicRequired,
        false,
        false,
        false,
        true,
        None,
    )
    .expect("unavailable atomic plan");
    assert_eq!(
        select_transport(&unavailable),
        TransportRoute::Abstain(RouteAbstention::AtomicCapabilityUnavailable)
    );

    let wrong_targets = ha_adapter::TargetSet::new(vec![target(1), target(3)])
        .expect("FIXTURE_TECNICA wrong targets");
    assert_eq!(
        RoutePlan::new(
            nodes,
            GraphTopology::AtomicRequired,
            false,
            false,
            false,
            true,
            Some(
                TypedOperation::read_bundled_snapshot(wrong_targets)
                    .expect("FIXTURE_TECNICA wrong bundle")
            ),
        ),
        Err(AdapterError::InvalidRoute)
    );

    let extra_mapping = TypedOperation::read_bundled_snapshot(
        ha_adapter::TargetSet::new(vec![target(1), target(2)]).expect("FIXTURE_TECNICA targets"),
    )
    .expect("FIXTURE_TECNICA bundle");
    assert_eq!(
        RoutePlan::new(
            vec![read_node(1, 1), read_node(2, 2)],
            GraphTopology::UnorderedIndependent,
            true,
            false,
            false,
            true,
            Some(extra_mapping),
        ),
        Err(AdapterError::InvalidRoute)
    );
}
