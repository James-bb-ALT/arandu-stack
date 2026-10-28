use std::{
    io::{Read, Write},
    net::{TcpListener, TcpStream},
    thread,
};

use local_nlu::server;

const V2_INVALID: &str = r#"{"status":"invalid_request","version":2}"#;

fn post_v2(body: &[u8]) -> String {
    post_raw(
        &format!(
            "POST /v2/resolve HTTP/1.1\r\nHost: localhost\r\nContent-Type: application/json\r\nContent-Length: {}\r\n\r\n",
            body.len()
        ),
        body,
    )
}

fn post_raw(headers: &str, body: &[u8]) -> String {
    let listener = TcpListener::bind("127.0.0.1:0").expect("bind");
    let address = listener.local_addr().expect("address");
    let server = thread::spawn(move || server::serve_one(&listener).expect("serve"));
    let mut stream = TcpStream::connect(address).expect("connect");
    stream.write_all(headers.as_bytes()).expect("headers");
    stream.write_all(body).expect("body");
    let mut response = String::new();
    stream.read_to_string(&mut response).expect("read");
    server.join().expect("join");
    response
}

fn catalog() -> serde_json::Value {
    serde_json::json!({
        "catalog_id": "test-catalog",
        "generation": "gen-001",
        "areas": [{"area_id": "sala", "names": ["sala"]}],
        "entities": [
            {
                "registry_id": "r1",
                "entity_id": "light.one",
                "domain": "light",
                "area_id": "sala",
                "display_name": "Abajur",
                "aliases": [],
                "capabilities": ["turn_on"]
            },
            {
                "registry_id": "r2",
                "entity_id": "light.two",
                "domain": "light",
                "area_id": "sala",
                "display_name": "Abajur",
                "aliases": [],
                "capabilities": ["turn_on"]
            }
        ]
    })
}

fn request(catalog: &serde_json::Value, mention: &str, constraints: &serde_json::Value) -> Vec<u8> {
    serde_json::to_vec(&serde_json::json!({
        "text": "Acenda.",
        "catalog": catalog,
        "generation": "gen-001",
        "mention": mention,
        "constraints": constraints,
    }))
    .expect("request")
}

#[test]
fn v2_resolves_unique_display_with_area() {
    let mut single = catalog();
    single["entities"]
        .as_array_mut()
        .expect("entities")
        .truncate(1);
    let response = post_v2(&request(
        &single,
        "abajur",
        &serde_json::json!({"area_id": "sala"}),
    ));
    assert!(response.starts_with("HTTP/1.1 200 OK\r\n"));
    assert!(response.ends_with(
        r#"{"outcome":"resolved","registry_id":"r1","evidence":"display_name_with_constraint"}"#
    ));
}

#[test]
fn v2_reports_full_ambiguity_without_truncation() {
    let response = post_v2(&request(
        &catalog(),
        "abajur",
        &serde_json::json!({"domain": "light"}),
    ));
    assert!(response.starts_with("HTTP/1.1 200 OK\r\n"));
    assert!(response.ends_with(r#"{"outcome":"ambiguous","candidates":["r1","r2"]}"#));
}

#[test]
fn v2_single_display_without_constraint_is_no_match() {
    let mut single = catalog();
    single["entities"]
        .as_array_mut()
        .expect("entities")
        .truncate(1);
    let response = post_v2(&request(&single, "abajur", &serde_json::json!({})));
    assert!(response.starts_with("HTTP/1.1 200 OK\r\n"));
    assert!(response.ends_with(r#"{"outcome":"no_match"}"#));
}

#[test]
fn v2_stale_generation_is_no_match() {
    let mut body = request(
        &catalog(),
        "abajur",
        &serde_json::json!({"domain": "light"}),
    );
    let mut parsed: serde_json::Value = serde_json::from_slice(&body).expect("parse");
    parsed["generation"] = serde_json::Value::String("gen-000".to_owned());
    body = serde_json::to_vec(&parsed).expect("rewrite");
    let response = post_v2(&body);
    assert!(response.starts_with("HTTP/1.1 200 OK\r\n"));
    assert!(response.ends_with(r#"{"outcome":"no_match"}"#));
}

#[test]
fn v2_malformed_bodies_are_400_invalid_request() {
    let good = request(&catalog(), "abajur", &serde_json::json!({}));
    let mut unknown = serde_json::from_slice::<serde_json::Value>(&good).expect("parse");
    unknown["bogus"] = serde_json::Value::Bool(true);
    let cases: Vec<Vec<u8>> = vec![
        b"{".to_vec(),
        serde_json::to_vec(&unknown).expect("unknown"),
        br#"{"text":1}"#.to_vec(),
    ];
    for body in cases {
        let response = post_v2(&body);
        assert!(
            response.starts_with("HTTP/1.1 400 Bad Request\r\n"),
            "{response}"
        );
        assert!(response.ends_with(V2_INVALID), "{response}");
    }
}

#[test]
fn v2_oversized_body_is_rejected_before_parsing() {
    // Headers alone decide the rejection; the body is never consumed, so the
    // client reads the response without sending the declared payload.
    let listener = TcpListener::bind("127.0.0.1:0").expect("bind");
    let address = listener.local_addr().expect("address");
    let server = thread::spawn(move || server::serve_one(&listener).expect("serve"));
    let mut stream = TcpStream::connect(address).expect("connect");
    stream
        .write_all(
            b"POST /v2/resolve HTTP/1.1\r\nHost: localhost\r\nContent-Type: application/json\r\nContent-Length: 70000\r\n\r\n",
        )
        .expect("headers");
    let mut response = String::new();
    stream.read_to_string(&mut response).expect("read");
    server.join().expect("join");
    assert!(response.starts_with("HTTP/1.1 400 Bad Request\r\n"));
    assert!(response.ends_with(V2_INVALID));
}

#[test]
fn v2_wrong_method_keeps_existing_404_behavior() {
    let listener = TcpListener::bind("127.0.0.1:0").expect("bind");
    let address = listener.local_addr().expect("address");
    let server = thread::spawn(move || server::serve_one(&listener).expect("serve"));
    let mut stream = TcpStream::connect(address).expect("connect");
    stream
        .write_all(b"GET /v2/resolve HTTP/1.1\r\nHost: localhost\r\n\r\n")
        .expect("write");
    let mut response = String::new();
    stream.read_to_string(&mut response).expect("read");
    server.join().expect("join");
    assert!(response.starts_with("HTTP/1.1 404 Not Found\r\n"));
    assert!(response.ends_with(r#"{"status":"invalid_request","version":1}"#));
}

#[test]
fn v2_large_ambiguity_returns_every_candidate() {
    // Largest ambiguity reachable through the 64 KB request bound: the full
    // candidate list must arrive, never a truncation. (The 500-error branch
    // in the server is unreachable over HTTP for the same reason and exists
    // only as defense-in-depth if transport bounds ever change.)
    let mut entities = Vec::new();
    for index in 0..500 {
        entities.push(serde_json::json!({
            "registry_id": format!("r{index}"),
            "entity_id": format!("light.e{index}"),
            "domain": "light",
            "display_name": "d",
            "aliases": [],
            "capabilities": ["turn_on"]
        }));
    }
    let catalog = serde_json::json!({
        "catalog_id": "big",
        "generation": "gen-001",
        "areas": [],
        "entities": entities
    });
    let body = request(&catalog, "d", &serde_json::json!({}));
    assert!(body.len() <= 65_536);
    let response = post_v2(&body);
    let mut expected: Vec<String> = (0..500).map(|index| format!("r{index}")).collect();
    expected.sort();
    let expected_body = format!(
        "{{\"outcome\":\"ambiguous\",\"candidates\":[{}]}}",
        expected
            .iter()
            .map(|candidate| format!("{candidate:?}"))
            .collect::<Vec<_>>()
            .join(",")
    );
    assert!(response.starts_with("HTTP/1.1 200 OK\r\n"));
    assert!(response.ends_with(&expected_body));
}
