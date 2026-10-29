use std::{
    io::{Read, Write},
    net::{TcpListener, TcpStream},
    thread,
};

use local_nlu::server;

fn post_v3(body: &[u8]) -> String {
    let listener = TcpListener::bind("127.0.0.1:0").expect("bind");
    let address = listener.local_addr().expect("address");
    let server = thread::spawn(move || server::serve_one(&listener).expect("serve"));
    let mut stream = TcpStream::connect(address).expect("connect");
    write!(
        stream,
        "POST /v3/interpret HTTP/1.1\r\nHost: localhost\r\nContent-Type: application/json\r\nContent-Length: {}\r\n\r\n",
        body.len()
    )
    .expect("headers");
    stream.write_all(body).expect("body");
    let mut response = String::new();
    stream.read_to_string(&mut response).expect("read");
    server.join().expect("join");
    response
}

fn snapshot() -> serde_json::Value {
    serde_json::json!({
        "catalog_id": "test-v3",
        "generation": "gen-001",
        "areas": [
            {"area_id": "area_jaime", "names": ["Jaime"]},
            {"area_id": "area_sala", "names": ["sala"]},
            {"area_id": "area_quarto", "names": ["quarto"]}
        ],
        "entities": [
            {"registry_id": "light_jaime", "entity_id": "light.jaime", "domain": "light", "area_id": "area_jaime", "display_name": "luz", "aliases": ["luz de Jaime"], "capabilities": ["turn_on", "turn_off"]},
            {"registry_id": "fan_jaime", "entity_id": "fan.jaime", "domain": "fan", "area_id": "area_jaime", "display_name": "ventilador", "aliases": ["ventilador de Jaime"], "capabilities": ["turn_on", "turn_off", "set_fan_percentage"]},
            {"registry_id": "lamp_sala", "entity_id": "light.abajur_sala", "domain": "light", "area_id": "area_sala", "display_name": "abajur", "aliases": ["abajur da sala"], "capabilities": ["turn_on", "turn_off"]},
            {"registry_id": "lamp_quarto", "entity_id": "light.abajur_quarto", "domain": "light", "area_id": "area_quarto", "display_name": "abajur", "aliases": ["abajur do quarto"], "capabilities": ["turn_on", "turn_off"]},
            {"registry_id": "coffee", "entity_id": "switch.cafeteira", "domain": "switch", "area_id": "area_sala", "display_name": "cafeteira", "aliases": [], "capabilities": ["turn_on", "turn_off"]}
        ]
    })
}

fn request(text: &str) -> Vec<u8> {
    serde_json::to_vec(&serde_json::json!({
        "text": text,
        "snapshot": snapshot(),
        "generation": "gen-001",
    }))
    .expect("request")
}

#[test]
fn v3_interprets_heterogeneous_device_clauses() {
    let response = post_v3(&request("desliga a luz de Jaime e o ventilador de Jaime"));
    assert!(response.starts_with("HTTP/1.1 200 OK\r\n"));
    assert!(response.ends_with(
        r#"{"status":"plan","operations":[{"action":"turn_off","targets":["light_jaime"]},{"action":"turn_off","targets":["fan_jaime"]}],"version":3}"#
    ), "{response}");
}

#[test]
fn v3_produces_target_clarification_for_unknown_and_ambiguous_targets() {
    let missing = post_v3(&request("desliga a luz de inexistente"));
    assert!(missing.ends_with(
        r#"{"status":"missing_slot","missing_slot":"target","action":"turn_off","domain":"light","version":3}"#
    ), "{missing}");

    let ambiguous = post_v3(&request("desliga o abajur"));
    assert!(ambiguous.ends_with(
        r#"{"status":"missing_slot","missing_slot":"target","action":"turn_off","domain":"light","candidates":["lamp_quarto","lamp_sala"],"version":3}"#
    ), "{ambiguous}");
}

#[test]
fn v3_interprets_music_provider_area_and_missing_query() {
    let play = post_v3(&request("toca Queen no Spotify na sala"));
    assert!(play.ends_with(
        r#"{"status":"plan","music":{"action":"play","media_query":"Queen","provider":"spotify","player_area":"area_sala"},"version":3}"#
    ), "{play}");

    let missing = post_v3(&request("toca Deezer"));
    assert!(missing.ends_with(
        r#"{"status":"missing_slot","missing_slot":"media_query","provider":"deezer","version":3}"#
    ), "{missing}");
}
