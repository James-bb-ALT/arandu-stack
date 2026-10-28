//! Reproducible synthetic catalog benchmark, no private residential data.
use local_nlu::contextual::{
    contract::{Area, Catalog, ContextRequest, Entity},
    interpret, register_catalog,
};
use serde_json::json;
use std::{collections::BTreeMap, time::Instant};

fn percentile(values: &mut [f64], percent: usize) -> f64 {
    values.sort_by(f64::total_cmp);
    values[((values.len() - 1) * percent).div_ceil(100)]
}

fn main() {
    let mut reports = Vec::new();
    for size in [64, 512, 4096] {
        let generation = format!("{size:064x}");
        let catalog = Catalog {
            version: 4,
            generation: generation.clone(),
            groups: vec![],
            areas: vec![Area {
                area_id: "sala".into(),
                names: vec!["Sala".into()],
            }],
            entities: (0..size)
                .map(|i| Entity {
                    registry_id: format!("fixture_{i}"),
                    entity_id: format!("light.fixture_{i}"),
                    domain: "light".into(),
                    area_id: Some("sala".into()),
                    device_id: Some(format!("fixture_device_{i}")),
                    name: if i == 0 {
                        "Luz principal".into()
                    } else {
                        format!("Luz fixture {i}")
                    },
                    device_name: None,
                    aliases: vec![format!("Fixture alias {i}")],
                    device_class: None,
                    actions: vec![
                        "turn_on".into(),
                        "turn_off".into(),
                        "brightness".into(),
                        "color".into(),
                        "query".into(),
                    ],
                    attributes: BTreeMap::new(),
                    preferred: i == 0,
                })
                .collect(),
        };
        let bytes = serde_json::to_vec(&catalog).unwrap().len();
        let mut cold = Vec::new();
        for _ in 0..10 {
            let start = Instant::now();
            assert_eq!(register_catalog(catalog.clone()).status, "catalog_ready");
            cold.push(start.elapsed().as_secs_f64() * 1_000.0);
        }
        let texts = [
            "Liga a luz",
            "Coloca a luz em cinquenta por cento",
            "Deixa a luz mais fraca",
            "Coloca a iluminação azul",
            "Qual é o estado da luz principal?",
        ];
        let mut phases: BTreeMap<String, Vec<f64>> = BTreeMap::new();
        let mut statuses = BTreeMap::new();
        for n in 0..1100 {
            let request = ContextRequest {
                version: 4,
                generation: generation.clone(),
                text: texts[n % texts.len()].into(),
                origin_area: Some("sala".into()),
                ..ContextRequest::default()
            };
            let response = interpret(&request);
            assert_eq!(response.status, "plan", "{response:?}");
            if n >= 100 {
                *statuses.entry(response.status).or_insert(0) += 1;
                for (key, value) in response.timings {
                    phases.entry(key).or_default().push(value);
                }
            }
        }
        let times: BTreeMap<_, _> = phases.into_iter().map(|(key, mut values)| (key, json!({"p50_ms": percentile(&mut values, 50), "p95_ms": percentile(&mut values, 95), "p99_ms": percentile(&mut values, 99)}))).collect();
        let rss = std::fs::read_to_string("/proc/self/status")
            .ok()
            .and_then(|text| {
                text.lines()
                    .find(|line| line.starts_with("VmRSS:"))
                    .map(str::to_owned)
            });
        reports.push(json!({"entities": size, "snapshot_bytes": bytes, "iterations": 1000, "warmup": 100, "catalog_compile_p50_ms": percentile(&mut cold, 50), "catalog_compile_p95_ms": percentile(&mut cold, 95), "phases": times, "statuses": statuses, "process_rss": rss}));
    }
    println!("{}", serde_json::to_string_pretty(&json!({"kind": "Rust release in-process synthetic catalog benchmark; excludes HTTP and HA execution", "catalogs": reports})).unwrap());
}
