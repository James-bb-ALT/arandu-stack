use std::{
    io::{Read, Write},
    net::{TcpListener, TcpStream},
    thread,
};

use local_nlu::server;

const V2_INVALID: &str = r#"{"status":"invalid_request","version":2}"#;

fn post_v2interpret(body: &[u8]) -> String {
    let listener = TcpListener::bind("127.0.0.1:0").expect("bind");
    let address = listener.local_addr().expect("address");
    let server = thread::spawn(move || server::serve_one(&listener).expect("serve"));
    let mut stream = TcpStream::connect(address).expect("connect");
    write!(
        stream,
        "POST /v2/interpret HTTP/1.1\r\nHost: localhost\r\nContent-Type: application/json\r\nContent-Length: {}\r\n\r\n",
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
        "catalog_id": "test-v2",
        "generation": "gen-001",
        "areas": [{"area_id": "sala", "names": ["sala"]}],
        "entities": [{
            "registry_id": "r1",
            "entity_id": "light.one",
            "domain": "light",
            "area_id": "sala",
            "display_name": "Abajur",
            "aliases": ["abajur da sala"],
            "capabilities": ["turn_on", "turn_off"]
        }]
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
fn v2_interpret_returns_plans_over_http() {
    let response = post_v2interpret(&request("Apague o abajur da sala."));
    assert!(response.starts_with("HTTP/1.1 200 OK\r\n"));
    assert!(response.ends_with(
        r#"{"status":"plan","operations":[{"action":"turn_off","targets":["r1"]}],"version":2}"#
    ));
}

#[test]
fn v2_interpret_rejects_malformed_bodies_with_400() {
    for body in [
        b"{".to_vec(),
        br#"{"text":"x","snapshot":{},"generation":"g","bogus":1}"#.to_vec(),
    ] {
        let response = post_v2interpret(&body);
        assert!(
            response.starts_with("HTTP/1.1 400 Bad Request\r\n"),
            "{response}"
        );
        assert!(response.ends_with(V2_INVALID), "{response}");
    }
}

#[test]
fn v2_interpret_abstains_on_unlinked_mentions() {
    let response = post_v2interpret(&request("Apague o abajur da copa."));
    assert!(response.starts_with("HTTP/1.1 200 OK\r\n"));
    assert!(response.ends_with(r#"{"status":"no_match","version":2}"#));
}
