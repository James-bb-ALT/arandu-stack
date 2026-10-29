//! `FIXTURE_TECNICA` catalog, project-authored internal conformance specification.
use local_nlu::contextual::{
    contract::{Area, AreaGroup, Catalog, ContextRequest, Entity},
    interpret, register_catalog, slots,
};
use serde_json::json;
use std::collections::BTreeMap;
use std::sync::{Mutex, MutexGuard};

// These tests intentionally share the production process-global eight-catalog cache.
// Keep each register/interpret fixture atomic relative to the other tests; otherwise
// a concurrent fixture can evict a generation while its assertions are in progress.
static CATALOG_TEST_LOCK: Mutex<()> = Mutex::new(());

fn catalog_test_guard() -> MutexGuard<'static, ()> {
    CATALOG_TEST_LOCK
        .lock()
        .unwrap_or_else(std::sync::PoisonError::into_inner)
}

#[test]
#[allow(clippy::too_many_lines)] // One bounded spatial acceptance matrix and provenance contract.
fn spatial_power_and_implicit_origin_contract() {
    let _guard = catalog_test_guard();
    let mut rows = vec![
        entity(
            "a",
            "light",
            "Bancada",
            "fixture_room",
            None,
            &["turn_on", "turn_off"],
        ),
        entity(
            "b",
            "fan",
            "Brisa",
            "fixture_room",
            None,
            &["turn_on", "turn_off"],
        ),
        entity(
            "c",
            "light",
            "Excluída",
            "fixture_room",
            None,
            &["turn_on", "turn_off"],
        ),
        entity(
            "d",
            "light",
            "Outro cômodo",
            "fixture_other",
            None,
            &["turn_on", "turn_off"],
        ),
        entity(
            "e",
            "fan",
            "Somente desliga",
            "fixture_room",
            None,
            &["turn_off"],
        ),
        entity(
            "scene",
            "scene",
            "Ambiente técnico",
            "fixture_room",
            None,
            &["activate"],
        ),
    ];
    rows[2]
        .attributes
        .insert("bulk_eligible".into(), json!(false));
    let catalog = Catalog {
        version: 4,
        generation: "8".repeat(64),
        areas: vec![
            Area {
                area_id: "fixture_room".into(),
                names: vec!["Ambiente técnico".into()],
            },
            Area {
                area_id: "fixture_other".into(),
                names: vec!["Outro".into()],
            },
        ],
        entities: rows,
        groups: vec![],
    };
    assert_eq!(register_catalog(catalog).status, "catalog_ready");
    let mut input = ContextRequest {
        version: 4,
        generation: "8".repeat(64),
        ..ContextRequest::default()
    };
    for (phrase, targets) in [
        ("liga o ambiente técnico", vec!["a", "b"]),
        (
            "liga todos os dispositivos do ambiente técnico",
            vec!["a", "b"],
        ),
        ("liga tudo no ambiente técnico", vec!["a", "b"]),
        ("desliga o ambiente técnico", vec!["a", "b", "e"]),
        ("liga todas as luzes do ambiente técnico", vec!["a"]),
        ("desliga todas as luzes do ambiente técnico", vec!["a"]),
    ] {
        input.text = phrase.into();
        let result = interpret(&input);
        assert_eq!(result.status, "plan", "{phrase}: {result:?}");
        assert_eq!(result.operations[0].targets, targets);
        assert_eq!(
            result.operations[0].parameters.scope.as_deref(),
            Some("bulk_area")
        );
    }
    input.text = "luz".into();
    input.origin_area = Some("fixture_room".into());
    assert_eq!(
        interpret(&input).reason.as_deref(),
        Some("missing_origin_area")
    );
    input.origin_area_source = Some("default".into());
    assert_eq!(interpret(&input).status, "clarification");
    for source in ["satellite", "device"] {
        input.origin_area_source = Some(source.into());
        for text in [
            "luz",
            "luzes",
            "iluminação",
            "lâmpadas",
            "ventilador",
            "ventiladores",
        ] {
            input.text = text.into();
            let result = interpret(&input);
            assert_eq!(result.status, "plan", "{text}: {result:?}");
            assert_eq!(result.operations[0].action, "turn_on");
            assert_eq!(
                result.operations[0].targets,
                if text.starts_with("ventil") {
                    vec!["b"]
                } else {
                    vec!["a"]
                }
            );
            assert_eq!(
                result.operations[0].parameters.area.as_deref(),
                Some("fixture_room")
            );
        }
    }
    for text in ["fechadura", "portão", "alarme", "qualquer"] {
        input.text = text.into();
        assert!(interpret(&input).operations.is_empty());
    }
    input.text = "luz".into();
    input.origin_area_source = Some("untrusted".into());
    assert_eq!(interpret(&input).status, "invalid_request");
}

#[test]
fn bulk_availability_and_missing_named_target_contract() {
    let _guard = catalog_test_guard();
    let mut cat = catalog();
    cat.generation = "ac".repeat(32);
    cat.entities = vec![
        entity(
            "online",
            "light",
            "Bancada",
            "sala",
            None,
            &["turn_on", "turn_off"],
        ),
        entity(
            "offline",
            "light",
            "Reserva",
            "sala",
            None,
            &["query", "turn_on", "turn_off"],
        ),
    ];
    // Availability wins even if a descriptor incorrectly retains effect capabilities.
    cat.entities[1]
        .attributes
        .insert("available".into(), json!(false));
    assert_eq!(register_catalog(cat.clone()).status, "catalog_ready");
    let mut input = ContextRequest {
        version: 4,
        generation: cat.generation.clone(),
        ..ContextRequest::default()
    };
    for phrase in [
        "liga a sala",
        "desliga a sala",
        "liga todas as luzes da sala",
    ] {
        input.text = phrase.into();
        let response = interpret(&input);
        assert_eq!(response.status, "plan", "{response:?}");
        assert_eq!(response.operations[0].targets, vec!["online"]);
    }
    input.text = "liga a luz do módulo inexistente".into();
    let response = interpret(&input);
    assert_eq!(response.reason.as_deref(), Some("target_not_found"));
    assert!(response.operations.is_empty());
    assert!(response.command.is_some());
    cat.entities[0]
        .attributes
        .insert("available".into(), json!(false));
    assert_eq!(register_catalog(cat).status, "catalog_ready");
    input.text = "desliga a sala".into();
    assert!(interpret(&input).operations.is_empty());
}

fn entity(
    id: &str,
    domain: &str,
    name: &str,
    area: &str,
    class: Option<&str>,
    actions: &[&str],
) -> Entity {
    Entity {
        registry_id: id.into(),
        entity_id: format!("{domain}.{id}"),
        domain: domain.into(),
        name: name.into(),
        aliases: vec![],
        device_id: Some(format!("device_{id}")),
        device_name: None,
        area_id: Some(area.into()),
        device_class: class.map(str::to_owned),
        actions: actions.iter().map(|action| (*action).into()).collect(),
        attributes: BTreeMap::new(),
        preferred: false,
    }
}

fn catalog() -> Catalog {
    Catalog {
        version: 4,
        generation: "a".repeat(64),
        groups: vec![],
        areas: vec![
            Area {
                area_id: "quarto".into(),
                names: vec!["Quarto".into()],
            },
            Area {
                area_id: "sala".into(),
                names: vec!["Sala".into()],
            },
            Area {
                area_id: "escritorio".into(),
                names: vec!["Escritório de Jaime".into()],
            },
        ],
        entities: vec![
            entity(
                "ar_quarto",
                "climate",
                "Ar-condicionado",
                "quarto",
                None,
                &["turn_on", "turn_off", "temperature", "query"],
            ),
            entity(
                "ar_sala",
                "climate",
                "Ar-condicionado",
                "sala",
                None,
                &["turn_on", "turn_off", "temperature", "query"],
            ),
            entity(
                "ar_escritorio",
                "climate",
                "Ar-condicionado",
                "escritorio",
                None,
                &["turn_on", "turn_off", "temperature", "query"],
            ),
            entity(
                "fan",
                "fan",
                "Ventilador",
                "quarto",
                None,
                &["turn_on", "turn_off", "percentage", "query"],
            ),
            entity(
                "light",
                "light",
                "Luz",
                "quarto",
                None,
                &["turn_on", "turn_off", "brightness", "color", "query"],
            ),
            entity(
                "sensor",
                "sensor",
                "Termômetro",
                "quarto",
                Some("temperature"),
                &["query"],
            ),
            entity(
                "timer",
                "timer",
                "Timer",
                "quarto",
                None,
                &[
                    "timer_start",
                    "timer_pause",
                    "timer_resume",
                    "timer_cancel",
                    "query",
                ],
            ),
            entity(
                "lock",
                "lock",
                "Porta",
                "quarto",
                None,
                &["lock", "unlock", "query"],
            ),
            entity(
                "calendar",
                "calendar",
                "Calendário",
                "quarto",
                None,
                &["calendar", "query"],
            ),
        ],
    }
}

#[test]
fn compositional_sets_compare_real_room_and_name_without_overfitting() {
    let _guard = catalog_test_guard();
    for (owner, room) in [("Ana", "Estúdio de Ana"), ("João", "Laboratório de João")] {
        let mut cat = catalog();
        cat.generation = "10".repeat(32);
        cat.areas[2].names = vec![room.into()];
        cat.entities = vec![
            entity(
                "a",
                "fan",
                &format!("Ventilador de {owner}"),
                "escritorio",
                None,
                &["turn_off"],
            ),
            entity(
                "b",
                "fan",
                &format!("Ventilador da mesa de {owner}"),
                "escritorio",
                None,
                &["turn_off"],
            ),
            entity(
                "c",
                "fan",
                "Ventilador do teto",
                "escritorio",
                None,
                &["turn_off"],
            ),
        ];
        assert_eq!(register_catalog(cat.clone()).status, "catalog_ready");
        let mut request = ContextRequest {
            version: 4,
            generation: cat.generation.clone(),
            text: format!("desliga os ventiladores de {owner}"),
            ..ContextRequest::default()
        };
        let result = interpret(&request);
        assert_eq!(result.status, "clarification", "{owner}: {result:?}");
        assert_eq!(result.reason.as_deref(), Some("name_area_sets_differ"));
        assert!(result.operations.is_empty());
        assert_eq!(result.options[0].targets, vec!["a", "b"]);
        assert_eq!(result.options[1].targets, vec!["a", "b", "c"]);
        request.text = format!("desliga os ventiladores do {room}");
        let result = interpret(&request);
        assert_eq!(result.status, "plan", "{result:?}");
        assert_eq!(result.operations[0].targets, vec!["a", "b", "c"]);
        cat.entities.pop();
        let _ = register_catalog(cat);
        request.text = format!("desliga os ventiladores de {owner}");
        assert_eq!(interpret(&request).operations[0].targets, vec!["a", "b"]);
        request.text = "desliga os ventiladores de Pessoa desconhecida".into();
        assert!(interpret(&request).operations.is_empty());
    }
}

#[test]
fn compositional_tokens_do_not_match_substrings_or_override_aliases() {
    let _guard = catalog_test_guard();
    let mut cat = catalog();
    cat.generation = "11".repeat(32);
    cat.entities = vec![
        entity("cabinet", "switch", "Armário", "sala", None, &["turn_off"]),
        entity(
            "fan",
            "fan",
            "Ventilador da mesa de Ana",
            "sala",
            None,
            &["turn_off"],
        ),
    ];
    cat.entities[1].aliases = vec!["Brisa pessoal".into()];
    let _ = register_catalog(cat);
    let mut request = ContextRequest {
        version: 4,
        generation: "11".repeat(32),
        text: "desliga ar".into(),
        ..ContextRequest::default()
    };
    assert!(interpret(&request).operations.is_empty());
    request.text = "desliga Brisa pessoal".into();
    assert_eq!(interpret(&request).operations[0].targets, vec!["fan"]);
    request.text = "desliga ventilador de Ana".into();
    let result = interpret(&request);
    assert_eq!(result.status, "plan", "{result:?}");
    assert_eq!(result.operations[0].targets, vec!["fan"]);
}

fn request(text: &str) -> ContextRequest {
    ContextRequest {
        version: 4,
        generation: "a".repeat(64),
        text: text.into(),
        origin_area: Some("quarto".into()),
        ..ContextRequest::default()
    }
}

#[test]
fn all_twenty_frozen_user_examples() {
    let _guard = catalog_test_guard();
    let mut catalog = catalog();
    catalog.generation = "d".repeat(64);
    catalog.areas.extend([
        Area {
            area_id: "corredor".into(),
            names: vec!["Corredor".into()],
        },
        Area {
            area_id: "cozinha".into(),
            names: vec!["Cozinha".into()],
        },
    ]);
    catalog.entities.extend([
        entity(
            "hall_light",
            "light",
            "Luz",
            "corredor",
            None,
            &["turn_on", "turn_off", "query"],
        ),
        entity(
            "living_light",
            "light",
            "Luz",
            "sala",
            None,
            &["turn_on", "turn_off", "query"],
        ),
        entity("person", "person", "Bruna", "sala", None, &["query"]),
        entity(
            "presence",
            "binary_sensor",
            "Presença",
            "cozinha",
            Some("occupancy"),
            &["query"],
        ),
        entity(
            "tv",
            "media_player",
            "TV",
            "sala",
            Some("tv"),
            &["remote_key", "channel", "query"],
        ),
        entity(
            "scene",
            "scene",
            "Cinema",
            "sala",
            None,
            &["activate", "query"],
        ),
    ]);
    assert_eq!(register_catalog(catalog).status, "catalog_ready");
    let spec: serde_json::Value = serde_json::from_str(include_str!(
        "../../../data/contextual/conformance-spec.json"
    ))
    .unwrap();
    for case in spec["cases"].as_array().unwrap() {
        let text = case[0].as_str().unwrap();
        let expected = case[1].as_str().unwrap();
        let mut input = request(text);
        input.generation = "d".repeat(64);
        let response = interpret(&input);
        if expected == "cancel" {
            assert_eq!(response.status, "cancel", "{text}: {response:?}");
        } else {
            assert_eq!(response.status, "plan", "{text}: {response:?}");
            if expected == "compound" {
                assert_eq!(response.operations.len(), 2, "{text}");
            } else {
                assert_eq!(response.operations[0].action, expected, "{text}");
            }
        }
    }
}

#[test]
fn floor_groups_are_explicit_and_categories_remain_distinct() {
    let _guard = catalog_test_guard();
    let mut catalog = catalog();
    catalog.generation = "e".repeat(64);
    catalog.groups = vec![AreaGroup {
        group_id: "floor".into(),
        names: vec!["Andar superior".into()],
        area_ids: vec!["quarto".into(), "sala".into()],
    }];
    catalog.entities.push(entity(
        "fan2",
        "fan",
        "Ventilador",
        "sala",
        None,
        &["turn_on", "turn_off", "query"],
    ));
    assert_eq!(register_catalog(catalog).status, "catalog_ready");
    let mut input = request("liga as luzes do andar superior");
    input.generation = "e".repeat(64);
    let plan = interpret(&input);
    assert_eq!(plan.status, "plan", "{plan:?}");
    assert_eq!(plan.operations[0].targets, ["light"]);
    assert_eq!(
        plan.operations[0].parameters.scope.as_deref(),
        Some("bulk_floor_group")
    );
    input.text = "liga as ventiladores do andar superior".into();
    let plan = interpret(&input);
    assert_eq!(plan.status, "plan", "{plan:?}");
    assert_eq!(plan.operations[0].targets, ["fan", "fan2"]);
}

#[test]
fn corpus_resolution_regressions() {
    let _guard = catalog_test_guard();
    let mut catalog = catalog();
    catalog.generation = "c".repeat(64);
    catalog.entities.extend([
        entity(
            "washer",
            "sensor",
            "Máquina de lavar",
            "sala",
            None,
            &["query"],
        ),
        entity(
            "window",
            "binary_sensor",
            "Janela",
            "sala",
            Some("window"),
            &["query"],
        ),
        entity(
            "plug",
            "switch",
            "Tomada da TV",
            "sala",
            Some("outlet"),
            &["turn_on", "query"],
        ),
        entity(
            "vacuum",
            "vacuum",
            "Robô aspirador",
            "sala",
            None,
            &["vacuum_start", "vacuum_area", "query"],
        ),
        entity(
            "mower",
            "lawn_mower",
            "Cortador de grama",
            "sala",
            None,
            &["mower_start", "query"],
        ),
    ]);
    catalog.entities[1]
        .actions
        .extend(["fan_mode".into(), "swing_mode".into()]);
    assert_eq!(register_catalog(catalog).status, "catalog_ready");
    for (text, action, target) in [
        ("como está a máquina de lavar", "query", "washer"),
        ("a janela da sala está aberta", "query", "window"),
        ("liga a tomada da TV", "turn_on", "plug"),
        ("liga o robô aspirador", "vacuum_start", "vacuum"),
        ("liga o cortador de grama", "mower_start", "mower"),
        (
            "manda o robô aspirador limpar o escritório de Jaime",
            "vacuum_area",
            "vacuum",
        ),
        (
            "coloca o ventilador do ar-condicionado da sala em alta",
            "fan_mode",
            "ar_sala",
        ),
        (
            "deixa a aleta do ar-condicionado da sala para cima",
            "swing_mode",
            "ar_sala",
        ),
    ] {
        let mut input = request(text);
        input.generation = "c".repeat(64);
        input.origin_area = Some("sala".into());
        let result = interpret(&input);
        assert_eq!(result.status, "plan", "{text}: {result:?}");
        assert_eq!(result.operations[0].action, action, "{text}");
        assert_eq!(result.operations[0].targets, [target], "{text}");
    }
}

#[test]
fn required_semantic_context_and_parameters() {
    let _guard = catalog_test_guard();
    assert_eq!(register_catalog(catalog()).status, "catalog_ready");
    for (text, action, target) in [
        ("Liga o ar.", "turn_on", "ar_quarto"),
        (
            "Liga o ar do escritório do Jaime",
            "turn_on",
            "ar_escritorio",
        ),
        ("Qual é a temperatura aqui?", "query", "sensor"),
        (
            "Coloca o ar em vinte e três graus",
            "temperature",
            "ar_quarto",
        ),
        ("Coloca o ar em 22,5 graus", "temperature", "ar_quarto"),
        ("Deixa a luz mais fraca", "brightness", "light"),
        ("Aumenta um pouco o ventilador", "percentage", "fan"),
        ("Coloca a iluminação azul", "color", "light"),
        ("Coloca um timer de dez minutos", "timer_start", "timer"),
        (
            "O que eu tenho no calendário amanhã?",
            "calendar",
            "calendar",
        ),
        ("Destranca a porta", "unlock", "lock"),
        ("Pode ligar o ventilador?", "turn_on", "fan"),
    ] {
        let result = interpret(&request(text));
        assert_eq!(result.status, "plan", "{text}: {result:?}");
        assert_eq!(result.operations[0].action, action, "{text}");
        assert_eq!(result.operations[0].targets, vec![target], "{text}");
    }
    assert_eq!(
        interpret(&request("Coloca o ar em 22,5 graus")).operations[0]
            .parameters
            .value,
        Some(json!(22.5))
    );
    assert_eq!(
        interpret(&request("Coloca um timer de dez minutos")).operations[0]
            .parameters
            .value,
        Some(json!(600.0))
    );
}

#[test]
fn ambiguity_negation_and_unknown_explicit_area_fail_closed() {
    let _guard = catalog_test_guard();
    let _ = register_catalog(catalog());
    let mut no_origin = request("Liga o ar");
    no_origin.origin_area = None;
    assert_eq!(interpret(&no_origin).status, "clarification");
    for text in [
        "Não liga o ventilador",
        "Não quero o ventilador ligado",
        "Nunca destranca a porta",
    ] {
        let result = interpret(&request(text));
        assert_eq!(result.status, "cancel");
        assert!(result.operations.is_empty());
    }
    let result = interpret(&request("Liga o ar do quarto inexistente"));
    assert!(result.operations.is_empty());
    let mut duplicates = catalog();
    duplicates.generation = "b".repeat(64);
    duplicates.entities.push(entity(
        "second",
        "climate",
        "Ar-condicionado",
        "quarto",
        None,
        &["turn_on"],
    ));
    let _ = register_catalog(duplicates);
    let mut input = request("Liga o ar");
    input.generation = "b".repeat(64);
    assert_eq!(interpret(&input).status, "clarification");
}

#[test]
fn compound_relative_and_context() {
    let _guard = catalog_test_guard();
    let _ = register_catalog(catalog());
    let result = interpret(&request("Liga o ar e apaga a luz"));
    assert_eq!(result.status, "plan", "{result:?}");
    assert_eq!(result.operations.len(), 2);
    let contradictory = interpret(&request("Liga o ar e desliga o ar"));
    assert!(contradictory.operations.is_empty());
    let mut continuation = request("Esfria mais dois graus");
    continuation.last_targets = vec!["ar_quarto".into()];
    let result = interpret(&continuation);
    assert_eq!(result.status, "plan", "{result:?}");
    assert_eq!(result.operations[0].targets, vec!["ar_quarto"]);
}

#[test]
fn numbers_duration_and_limits() {
    let _guard = catalog_test_guard();
    assert_eq!(slots::number("vinte e tres"), Some(23.0));
    assert_eq!(slots::number("um dois"), None);
    assert_eq!(slots::duration("uma hora e dez minutos"), Some(4200.0));
    assert_eq!(slots::duration("dez minutos banana"), None);
    assert_eq!(slots::duration("uma hora e"), None);
    assert_eq!(slots::original_slot("!!!", ""), None);
    assert_eq!(
        slots::original_slot("Adiciona Reunião às sete", "reuniao"),
        Some("Reunião".into())
    );
    assert_eq!(slots::number("NaN"), None);
    let mut request = request("liga o ar");
    request.text = "a".repeat(2049);
    assert_eq!(interpret(&request).status, "invalid_request");
}
