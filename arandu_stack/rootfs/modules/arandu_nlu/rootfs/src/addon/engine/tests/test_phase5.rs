//! Apache-2.0, project-authored `FIXTURE_TECNICA`; not linguistic gold.
use local_nlu::contextual::{
    contract::{Antecedent, Area, Catalog, ContextRequest, Entity, Parameters},
    interpret, register_catalog,
};
use serde_json::json;
use std::collections::BTreeMap;

fn entity(id: &str, domain: &str, name: &str, area: &str, actions: &[&str]) -> Entity {
    Entity {
        registry_id: id.into(),
        entity_id: format!("{domain}.{id}"),
        domain: domain.into(),
        area_id: Some(area.into()),
        device_id: None,
        name: name.into(),
        device_name: None,
        aliases: vec![],
        device_class: None,
        actions: actions.iter().map(|a| (*a).into()).collect(),
        attributes: BTreeMap::new(),
        preferred: false,
    }
}

#[test]
#[allow(clippy::too_many_lines)] // One immutable catalog and bounded acceptance matrix.
fn deterministic_phase5_matrix() {
    let generation = "e".repeat(64);
    let mut sensor = entity(
        "humidity",
        "sensor",
        "Umidade",
        "sala",
        &["query", "history_query"],
    );
    sensor.device_class = Some("humidity".into());
    let mut meter = entity(
        "meter",
        "sensor",
        "Medidor",
        "sala",
        &["query", "history_query"],
    );
    meter.device_class = Some("energy".into());
    let mut power = entity("power", "sensor", "Aparelho", "sala", &["query"]);
    power.device_class = Some("power".into());
    let mut temperature = entity(
        "temperature",
        "sensor",
        "Ambiente",
        "sala",
        &["query", "history_query"],
    );
    temperature.device_class = Some("temperature".into());
    temperature
        .attributes
        .insert("measurement_kind".into(), json!("ambient"));
    let rows = vec![
        entity(
            "irrigation",
            "automation",
            "Irrigação",
            "sala",
            &[
                "query",
                "automation_enable",
                "automation_disable",
                "automation_trigger",
            ],
        ),
        entity(
            "homonym",
            "light",
            "Irrigação",
            "sala",
            &["query", "turn_on"],
        ),
        entity(
            "shopping",
            "todo",
            "Compras",
            "sala",
            &[
                "todo_add",
                "todo_list",
                "todo_complete",
                "todo_remove",
                "todo_update",
                "todo_remove_completed",
            ],
        ),
        entity("cinema", "scene", "Cinema", "sala", &["activate"]),
        entity(
            "cinema_auto",
            "automation",
            "Cinema",
            "sala",
            &["automation_enable", "automation_trigger"],
        ),
        entity(
            "living",
            "light",
            "Luz",
            "sala",
            &["turn_on", "turn_off", "brightness"],
        ),
        entity(
            "kitchen",
            "light",
            "Luz",
            "cozinha",
            &["turn_on", "turn_off", "brightness"],
        ),
        entity(
            "tv",
            "media_player",
            "TV",
            "sala",
            &["history_query", "query"],
        ),
        sensor,
        meter,
        power,
        temperature,
    ];
    assert_eq!(
        register_catalog(Catalog {
            version: 4,
            generation: generation.clone(),
            areas: vec![
                Area {
                    area_id: "sala".into(),
                    names: vec!["sala".into()]
                },
                Area {
                    area_id: "cozinha".into(),
                    names: vec!["cozinha".into()]
                }
            ],
            entities: rows,
            groups: vec![]
        })
        .status,
        "catalog_ready"
    );
    let request = |text: &str| ContextRequest {
        version: 4,
        generation: generation.clone(),
        text: text.into(),
        ..ContextRequest::default()
    };
    for (text, action, intent, target) in [
        (
            "Ativa a automação irrigação",
            "automation_enable",
            "automation.enable",
            "irrigation",
        ),
        (
            "Desliga a automação irrigação",
            "automation_disable",
            "automation.disable",
            "irrigation",
        ),
        (
            "Executa a automação irrigação agora",
            "automation_trigger",
            "automation.run",
            "irrigation",
        ),
        (
            "Roda a automação irrigação",
            "automation_trigger",
            "automation.run",
            "irrigation",
        ),
        (
            "A automação irrigação está ligada?",
            "query",
            "automation.status",
            "irrigation",
        ),
        (
            "Adiciona CAFÉ à lista de compras",
            "todo_add",
            "todo.add",
            "shopping",
        ),
        (
            "Marca café como comprado",
            "todo_complete",
            "todo.complete",
            "shopping",
        ),
        (
            "Tira leite da lista de compras",
            "todo_remove",
            "todo.remove",
            "shopping",
        ),
        (
            "Troca leite por leite sem lactose na lista",
            "todo_update",
            "todo.update",
            "shopping",
        ),
        (
            "Limpa os itens concluídos da lista",
            "todo_remove_completed",
            "todo.clear_completed",
            "shopping",
        ),
        ("O que falta comprar?", "todo_list", "todo.list", "shopping"),
        (
            "Quanto consumiu Medidor esta semana",
            "history_query",
            "query.energy_period",
            "meter",
        ),
        ("Modo cinema", "activate", "scene.invoke_exact", "cinema"),
        (
            "A umidade caiu na última hora?",
            "history_query",
            "query.metric_trend",
            "humidity",
        ),
        (
            "Qual foi o pico de temperatura da sala ontem",
            "history_query",
            "query.metric_peak",
            "temperature",
        ),
        (
            "Quanto tempo a TV ficou ligada hoje?",
            "history_query",
            "query.state_duration",
            "tv",
        ),
        (
            "Qual cômodo está mais quente?",
            "query",
            "query.temperature_extremes",
            "temperature",
        ),
    ] {
        let result = interpret(&request(text));
        assert_eq!(result.status, "plan", "{text}: {result:?}");
        assert_eq!(result.operations.len(), 1, "{text}");
        assert_eq!(result.operations[0].action, action, "{text}");
        assert_eq!(result.operations[0].intent, intent, "{text}");
        assert_eq!(result.operations[0].targets, vec![target], "{text}");
    }
    let data = interpret(&request(
        "Adiciona desligue a luz e abra a porta na lista de compras",
    ));
    assert_ne!(
        interpret(&request("Qual foi o pico de umidade na garagem ontem")).status,
        "plan"
    );
    assert_ne!(
        interpret(&request("Qual aparelho da garagem gasta mais")).status,
        "plan"
    );
    assert_eq!(data.operations.len(), 1);
    assert_eq!(data.operations[0].action, "todo_add");
    assert_eq!(
        data.operations[0].parameters.value,
        Some(json!("desligue a luz e abra a porta"))
    );
    let unicode = interpret(&request("Adiciona CAFÉ à lista de compras"));
    assert_eq!(unicode.operations[0].parameters.value, Some(json!("CAFÉ")));
    for text in [
        "Boa noite",
        "Modo desconhecido",
        "Se a temperatura passar de 28 graus liga a luz",
        "Cria uma rotina para ligar a luz",
        "Tira leite da lista de inexistente",
        "Agora a da cozinha",
        "O outro também",
        "Desfaz",
    ] {
        assert!(interpret(&request(text)).operations.is_empty(), "{text}");
    }
    let mut follow = request("Agora a da cozinha");
    follow.antecedent = Some(Antecedent {
        generation: generation.clone(),
        action: "brightness".into(),
        targets: vec!["living".into()],
        parameters: Parameters {
            value: Some(json!(40)),
            ..Parameters::default()
        },
    });
    let result = interpret(&follow);
    assert_eq!(result.operations[0].targets, vec!["kitchen"]);
    assert_eq!(result.operations[0].parameters.value, Some(json!(40)));
    follow.text = "Desfaz a última mudança".into();
    assert_eq!(interpret(&follow).operations[0].action, "contextual_undo");
    follow.antecedent.as_mut().unwrap().action = "unlock".into();
    assert_eq!(interpret(&follow).status, "invalid_request");
}
