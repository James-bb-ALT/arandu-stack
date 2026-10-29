use std::{
    io::{Read, Write},
    net::{TcpListener, TcpStream},
    thread,
    time::{Duration, Instant},
};

use local_nlu::server;

#[test]
fn health_endpoint_is_bounded_json() {
    let listener = TcpListener::bind("127.0.0.1:0").expect("bind");
    let address = listener.local_addr().expect("address");
    let server = thread::spawn(move || server::serve_one(&listener).expect("serve"));
    let mut stream = TcpStream::connect(address).expect("connect");
    stream
        .write_all(b"GET /health HTTP/1.1\r\nHost: localhost\r\n\r\n")
        .expect("write");
    let mut response = String::new();
    stream.read_to_string(&mut response).expect("read");
    server.join().expect("join");
    assert!(response.starts_with("HTTP/1.1 200 OK\r\n"));
    assert!(response.ends_with(r#"{"status":"ok","version":1}"#));
}

#[test]
fn malformed_json_fails_closed() {
    let listener = TcpListener::bind("127.0.0.1:0").expect("bind");
    let address = listener.local_addr().expect("address");
    let server = thread::spawn(move || server::serve_one(&listener).expect("serve"));
    let mut stream = TcpStream::connect(address).expect("connect");
    let body = b"{";
    write!(
        stream,
        "POST /v1/interpret HTTP/1.1\r\nHost: localhost\r\nContent-Type: application/json\r\nContent-Length: {}\r\n\r\n",
        body.len()
    )
    .expect("headers");
    stream.write_all(body).expect("body");
    let mut response = String::new();
    stream.read_to_string(&mut response).expect("read");
    server.join().expect("join");
    assert!(response.starts_with("HTTP/1.1 200 OK\r\n"));
    assert!(response.ends_with(r#"{"status":"invalid_request","version":1}"#));
}

#[test]
fn diagnostic_endpoint_identifies_passive_service_without_private_data() {
    let listener = TcpListener::bind("127.0.0.1:0").expect("bind");
    let address = listener.local_addr().expect("address");
    let handle = thread::spawn(move || server::serve_one(&listener).expect("serve"));
    let mut stream = TcpStream::connect(address).expect("connect");
    stream
        .write_all(b"GET /diagnostics HTTP/1.1\r\nHost: localhost\r\n\r\n")
        .expect("write");
    let mut response = String::new();
    stream.read_to_string(&mut response).expect("read");
    handle.join().expect("join");
    let value: serde_json::Value =
        serde_json::from_str(response.split("\r\n\r\n").nth(1).expect("body")).expect("json");
    assert_eq!(value["service_version"], env!("CARGO_PKG_VERSION"));
    assert_eq!(value["protocols"], serde_json::json!([1, 2, 3, 4]));
    assert_eq!(value["execution"], "passive");
    assert_eq!(value.as_object().expect("object").len(), 4);
}

#[test]
fn malformed_then_duplicate_content_length_is_rejected() {
    let listener = TcpListener::bind("127.0.0.1:0").expect("bind");
    let address = listener.local_addr().expect("address");
    let server = thread::spawn(move || server::serve_one(&listener).expect("serve"));
    let mut stream = TcpStream::connect(address).expect("connect");
    stream
        .write_all(
            b"POST /v1/interpret HTTP/1.1\r\nHost: localhost\r\nContent-Type: application/json\r\nContent-Length: malformed\r\nContent-Length: 2\r\n\r\n{}",
        )
        .expect("request");
    let mut response = String::new();
    stream.read_to_string(&mut response).expect("read");
    server.join().expect("join");
    assert!(response.starts_with("HTTP/1.1 400 Bad Request\r\n"));
    assert!(response.ends_with(r#"{"status":"invalid_request","version":1}"#));
}

#[test]
fn slow_client_does_not_starve_a_health_request() {
    let listener = TcpListener::bind("127.0.0.1:0").expect("bind");
    let address = listener.local_addr().expect("address");
    let server = thread::spawn(move || server::serve(&listener));

    let mut slow = TcpStream::connect(address).expect("slow connect");
    slow.write_all(b"POST /v1/interpret HTTP/1.1\r\n")
        .expect("partial request");

    let started = Instant::now();
    let mut health = TcpStream::connect(address).expect("health connect");
    health
        .set_read_timeout(Some(Duration::from_secs(1)))
        .expect("timeout");
    health
        .write_all(b"GET /health HTTP/1.1\r\nHost: localhost\r\n\r\n")
        .expect("health request");
    let mut response = String::new();
    health
        .read_to_string(&mut response)
        .expect("health response");

    assert!(started.elapsed() < Duration::from_secs(1));
    assert!(response.starts_with("HTTP/1.1 200 OK\r\n"));
    drop(slow);
    drop(server);
}
