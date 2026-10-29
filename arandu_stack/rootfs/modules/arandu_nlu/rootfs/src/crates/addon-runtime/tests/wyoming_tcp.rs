use std::io::{BufRead, BufReader, Read, Write};
use std::net::{Shutdown, TcpListener, TcpStream};
use std::thread;
use std::time::Duration;

use addon_runtime::{
    MonotonicClock, RecognitionProvider, WyomingTcpConfig, WyomingTcpError, drive_wyoming_tcp,
};
use serde_json::Value;
use wyoming_runtime::{
    CONNECTION_LIFETIME_MILLIS, MonotonicMillis, RecognitionCompletion, RecognitionRequest,
    RevalidatedRecognition, RevalidatedStateQuery, RuntimeError as WyomingRuntimeError,
};

const FIXTURE_TECNICA_DESCRIBE: &[u8] = b"{\"type\":\"describe\",\"version\":\"1.10.0\"}\n";

fn fixture_tecnica_transcript() -> Vec<u8> {
    let data = concat!(
        "{\"text\":\"FIXTURE_TECNICA_QUERY\",\"language\":\"pt-BR\",\"context\":{",
        "\"conversation_id\":\"FIXTURE_TECNICA_CONVERSATION\"}}"
    );
    let mut frame = format!(
        "{{\"type\":\"transcript\",\"version\":\"1.10.0\",\"data_length\":{}}}\n",
        data.len()
    )
    .into_bytes();
    frame.extend_from_slice(data.as_bytes());
    frame
}

fn loopback_pair() -> (TcpStream, TcpStream) {
    let listener =
        TcpListener::bind("127.0.0.1:0").expect("FIXTURE_TECNICA bind loopback listener");
    let address = listener
        .local_addr()
        .expect("FIXTURE_TECNICA loopback address");
    let client = TcpStream::connect(address).expect("FIXTURE_TECNICA connect loopback");
    let (server, peer) = listener.accept().expect("FIXTURE_TECNICA accept loopback");
    assert!(peer.ip().is_loopback());
    client
        .set_read_timeout(Some(Duration::from_secs(2)))
        .expect("FIXTURE_TECNICA client read timeout");
    client
        .set_write_timeout(Some(Duration::from_secs(2)))
        .expect("FIXTURE_TECNICA client write timeout");
    (client, server)
}

#[derive(Clone)]
struct FixedClock {
    now: MonotonicMillis,
}

impl FixedClock {
    fn new(now: u64) -> Self {
        Self {
            now: MonotonicMillis::new(now),
        }
    }
}

impl MonotonicClock for FixedClock {
    fn now(&mut self) -> MonotonicMillis {
        self.now
    }
}

struct SequenceClock {
    values: std::collections::VecDeque<MonotonicMillis>,
    last: MonotonicMillis,
}

impl SequenceClock {
    fn new(values: &[u64]) -> Self {
        let values = values.iter().copied().map(MonotonicMillis::new).collect();
        Self {
            values,
            last: MonotonicMillis::new(0),
        }
    }
}

impl MonotonicClock for SequenceClock {
    fn now(&mut self) -> MonotonicMillis {
        if let Some(value) = self.values.pop_front() {
            self.last = value;
        }
        self.last
    }
}

struct StateQueryProvider;

impl RecognitionProvider for StateQueryProvider {
    type Error = ();

    fn recognize(
        &mut self,
        request: &RecognitionRequest,
    ) -> Result<RecognitionCompletion, Self::Error> {
        let query =
            RevalidatedStateQuery::from_revalidated_exact_name("FIXTURE_TECNICA_ENTITY".to_owned())
                .map_err(|_| ())?;
        let result = RevalidatedRecognition::single(request.id(), query).map_err(|_| ())?;
        Ok(RecognitionCompletion::recognized(result))
    }
}

struct AbstainingProvider;

impl RecognitionProvider for AbstainingProvider {
    type Error = ();

    fn recognize(
        &mut self,
        request: &RecognitionRequest,
    ) -> Result<RecognitionCompletion, Self::Error> {
        Ok(RecognitionCompletion::abstain(request.id()))
    }
}

struct FailingProvider;

impl RecognitionProvider for FailingProvider {
    type Error = ();

    fn recognize(
        &mut self,
        _request: &RecognitionRequest,
    ) -> Result<RecognitionCompletion, Self::Error> {
        Err(())
    }
}

fn read_event(reader: &mut BufReader<TcpStream>) -> (Value, Option<Value>) {
    let mut header_line = Vec::new();
    reader
        .read_until(b'\n', &mut header_line)
        .expect("FIXTURE_TECNICA read response header");
    assert_eq!(header_line.pop(), Some(b'\n'));
    let header: Value =
        serde_json::from_slice(&header_line).expect("FIXTURE_TECNICA response header JSON");
    let data = header
        .get("data_length")
        .and_then(Value::as_u64)
        .map(|length| {
            let mut bytes =
                vec![0_u8; usize::try_from(length).expect("FIXTURE_TECNICA response length")];
            reader
                .read_exact(&mut bytes)
                .expect("FIXTURE_TECNICA response data");
            serde_json::from_slice(&bytes).expect("FIXTURE_TECNICA response data JSON")
        });
    (header, data)
}

#[test]
fn loopback_discovery_and_typed_recognition_complete_before_eof() {
    let (mut client, server) = loopback_pair();
    let server_thread = thread::spawn(move || {
        let mut clock = FixedClock::new(100);
        let mut provider = StateQueryProvider;
        drive_wyoming_tcp(
            server,
            &mut clock,
            &mut provider,
            WyomingTcpConfig::default(),
        )
    });

    let mut requests = FIXTURE_TECNICA_DESCRIBE.to_vec();
    requests.extend_from_slice(&fixture_tecnica_transcript());
    client
        .write_all(&requests)
        .expect("FIXTURE_TECNICA write requests");

    let mut reader = BufReader::new(client.try_clone().expect("FIXTURE_TECNICA clone"));
    let (info_header, info_data) = read_event(&mut reader);
    assert_eq!(info_header["type"], "info");
    let info = info_data.expect("FIXTURE_TECNICA info data");
    assert_eq!(info["handle"], serde_json::json!([]));
    assert_eq!(info["intent"].as_array().map(Vec::len), Some(1));

    let (intent_header, intent_data) = read_event(&mut reader);
    assert_eq!(intent_header["type"], "intent");
    let intent = intent_data.expect("FIXTURE_TECNICA intent data");
    assert_eq!(intent["name"], "HassGetState");
    assert_eq!(
        intent["entities"][0]["value"],
        serde_json::json!("FIXTURE_TECNICA_ENTITY")
    );

    client
        .shutdown(Shutdown::Write)
        .expect("FIXTURE_TECNICA client EOF");
    let report = server_thread
        .join()
        .expect("FIXTURE_TECNICA server thread")
        .expect("FIXTURE_TECNICA TCP driver");
    assert_eq!(report.request_count(), 2);
    assert_eq!(report.bytes_read(), requests.len() as u64);
    assert!(report.bytes_written() > 0);
}

#[test]
fn abstention_provider_emits_not_recognized() {
    let (mut client, server) = loopback_pair();
    let server_thread = thread::spawn(move || {
        let mut clock = FixedClock::new(100);
        let mut provider = AbstainingProvider;
        drive_wyoming_tcp(
            server,
            &mut clock,
            &mut provider,
            WyomingTcpConfig::default(),
        )
    });

    client
        .write_all(&fixture_tecnica_transcript())
        .expect("FIXTURE_TECNICA transcript");
    let mut reader = BufReader::new(client.try_clone().expect("FIXTURE_TECNICA clone"));
    let (header, data) = read_event(&mut reader);
    assert_eq!(header["type"], "not-recognized");
    assert!(data.is_none());
    client
        .shutdown(Shutdown::Write)
        .expect("FIXTURE_TECNICA client EOF");
    server_thread
        .join()
        .expect("FIXTURE_TECNICA server thread")
        .expect("FIXTURE_TECNICA TCP driver");
}

#[test]
fn read_timeout_and_connection_lifetime_fail_closed() {
    let (client, server) = loopback_pair();
    let timeout_thread = thread::spawn(move || {
        let mut clock = FixedClock::new(100);
        let mut provider = AbstainingProvider;
        drive_wyoming_tcp(
            server,
            &mut clock,
            &mut provider,
            WyomingTcpConfig::new(Duration::from_millis(20), Duration::from_secs(1))
                .expect("FIXTURE_TECNICA timeout config"),
        )
    });
    assert_eq!(
        timeout_thread
            .join()
            .expect("FIXTURE_TECNICA timeout thread")
            .expect_err("FIXTURE_TECNICA read timeout"),
        WyomingTcpError::ReadTimeout
    );
    let mut closed = [0_u8; 1];
    assert_eq!(
        (&client)
            .read(&mut closed)
            .expect("FIXTURE_TECNICA fail-closed EOF"),
        0
    );

    let (_client, server) = loopback_pair();
    let mut clock = SequenceClock::new(&[0, CONNECTION_LIFETIME_MILLIS]);
    let mut provider = AbstainingProvider;
    assert_eq!(
        drive_wyoming_tcp(
            server,
            &mut clock,
            &mut provider,
            WyomingTcpConfig::default(),
        )
        .expect_err("FIXTURE_TECNICA connection lifetime"),
        WyomingTcpError::Protocol(WyomingRuntimeError::ConnectionDeadline)
    );
}

#[test]
fn clean_and_truncated_eof_are_distinguished() {
    let (client, server) = loopback_pair();
    client
        .shutdown(Shutdown::Write)
        .expect("FIXTURE_TECNICA clean EOF");
    let mut clock = FixedClock::new(100);
    let mut provider = AbstainingProvider;
    let report = drive_wyoming_tcp(
        server,
        &mut clock,
        &mut provider,
        WyomingTcpConfig::default(),
    )
    .expect("FIXTURE_TECNICA clean EOF accepted");
    assert_eq!(report.request_count(), 0);

    let (mut client, server) = loopback_pair();
    client
        .write_all(b"{\"type\":\"FIXTURE_TECNICA")
        .expect("FIXTURE_TECNICA truncated request");
    client
        .shutdown(Shutdown::Write)
        .expect("FIXTURE_TECNICA truncated EOF");
    let mut clock = FixedClock::new(100);
    assert_eq!(
        drive_wyoming_tcp(
            server,
            &mut clock,
            &mut provider,
            WyomingTcpConfig::default(),
        )
        .expect_err("FIXTURE_TECNICA truncated EOF rejected"),
        WyomingTcpError::Protocol(WyomingRuntimeError::TruncatedFrame)
    );
}

#[test]
fn provider_failure_closes_without_output_and_timeout_config_is_bounded() {
    assert_eq!(
        WyomingTcpConfig::new(Duration::ZERO, Duration::from_secs(1))
            .expect_err("FIXTURE_TECNICA zero timeout"),
        WyomingTcpError::InvalidTimeout
    );
    assert_eq!(
        WyomingTcpConfig::new(Duration::from_secs(1), Duration::from_secs(6))
            .expect_err("FIXTURE_TECNICA oversized timeout"),
        WyomingTcpError::InvalidTimeout
    );

    let (mut client, server) = loopback_pair();
    let server_thread = thread::spawn(move || {
        let mut clock = FixedClock::new(100);
        let mut provider = FailingProvider;
        drive_wyoming_tcp(
            server,
            &mut clock,
            &mut provider,
            WyomingTcpConfig::default(),
        )
    });
    client
        .write_all(&fixture_tecnica_transcript())
        .expect("FIXTURE_TECNICA transcript");
    assert_eq!(
        server_thread
            .join()
            .expect("FIXTURE_TECNICA provider thread")
            .expect_err("FIXTURE_TECNICA provider failure"),
        WyomingTcpError::RecognitionProvider
    );
    let mut output = [0_u8; 1];
    assert_eq!(
        client
            .read(&mut output)
            .expect("FIXTURE_TECNICA provider fail-closed EOF"),
        0
    );
}
