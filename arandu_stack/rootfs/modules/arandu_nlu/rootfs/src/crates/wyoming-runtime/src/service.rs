use core::fmt;

use crate::wire::{
    RecognitionInput, RequestEvent, WireDecoder, encode_info, encode_not_recognized,
    encode_recognized,
};
use crate::{Result, RuntimeError};

pub const MAX_REQUESTS_PER_CONNECTION: usize = 32;
pub const CONNECTION_LIFETIME_MILLIS: u64 = 60_000;
pub const RECOGNITION_DEADLINE_MILLIS: u64 = 5_000;
pub const MAX_REVALIDATED_INTENTS: usize = 16;
pub const MAX_STATE_QUERY_NAME_BYTES: usize = 512;

#[derive(Clone, Copy, Debug, Eq, Ord, PartialEq, PartialOrd)]
pub struct MonotonicMillis(u64);

impl MonotonicMillis {
    #[must_use]
    pub const fn new(value: u64) -> Self {
        Self(value)
    }

    #[must_use]
    pub const fn get(self) -> u64 {
        self.0
    }

    fn checked_add(self, duration: u64) -> Result<Self> {
        self.0
            .checked_add(duration)
            .map(Self)
            .ok_or(RuntimeError::TimeOverflow)
    }
}

#[derive(Clone, Copy, Debug, Eq, Ord, PartialEq, PartialOrd)]
pub struct RecognitionRequestId(u64);

impl RecognitionRequestId {
    #[must_use]
    pub const fn get(self) -> u64 {
        self.0
    }
}

#[derive(Clone, Eq, PartialEq)]
pub struct RecognitionRequest {
    id: RecognitionRequestId,
    deadline: MonotonicMillis,
    input: RecognitionInput,
}

impl RecognitionRequest {
    #[must_use]
    pub const fn id(&self) -> RecognitionRequestId {
        self.id
    }

    #[must_use]
    pub const fn deadline(&self) -> MonotonicMillis {
        self.deadline
    }

    #[must_use]
    pub const fn input(&self) -> &RecognitionInput {
        &self.input
    }
}

impl fmt::Debug for RecognitionRequest {
    fn fmt(&self, formatter: &mut fmt::Formatter<'_>) -> fmt::Result {
        formatter
            .debug_struct("RecognitionRequest")
            .field("id", &self.id)
            .field("deadline", &self.deadline)
            .field("input", &self.input)
            .finish()
    }
}

#[derive(Clone, Eq, Ord, PartialEq, PartialOrd)]
pub struct RevalidatedStateQuery {
    exact_name: String,
}

impl RevalidatedStateQuery {
    pub fn from_revalidated_exact_name(exact_name: String) -> Result<Self> {
        if exact_name.is_empty()
            || exact_name.len() > MAX_STATE_QUERY_NAME_BYTES
            || exact_name.chars().all(char::is_whitespace)
            || exact_name.chars().any(char::is_control)
        {
            return Err(RuntimeError::InvalidStateQuery);
        }
        Ok(Self { exact_name })
    }

    #[must_use]
    pub fn exact_name(&self) -> &str {
        &self.exact_name
    }
}

impl fmt::Debug for RevalidatedStateQuery {
    fn fmt(&self, formatter: &mut fmt::Formatter<'_>) -> fmt::Result {
        formatter
            .debug_struct("RevalidatedStateQuery")
            .field("exact_name_bytes", &self.exact_name.len())
            .finish()
    }
}

#[derive(Clone, Eq, PartialEq)]
pub struct RevalidatedRecognition {
    request_id: RecognitionRequestId,
    queries: Vec<RevalidatedStateQuery>,
}

impl RevalidatedRecognition {
    pub fn single(request_id: RecognitionRequestId, query: RevalidatedStateQuery) -> Result<Self> {
        Self::independent(request_id, vec![query])
    }

    pub fn independent(
        request_id: RecognitionRequestId,
        mut queries: Vec<RevalidatedStateQuery>,
    ) -> Result<Self> {
        if queries.is_empty() {
            return Err(RuntimeError::InvalidStateQuery);
        }
        if queries.len() > MAX_REVALIDATED_INTENTS {
            return Err(RuntimeError::TooManyRecognizedIntents);
        }
        queries.sort();
        if queries.windows(2).any(|pair| pair[0] == pair[1]) {
            return Err(RuntimeError::InvalidStateQuery);
        }
        Ok(Self {
            request_id,
            queries,
        })
    }

    #[must_use]
    pub const fn request_id(&self) -> RecognitionRequestId {
        self.request_id
    }

    #[must_use]
    pub fn queries(&self) -> &[RevalidatedStateQuery] {
        &self.queries
    }
}

impl fmt::Debug for RevalidatedRecognition {
    fn fmt(&self, formatter: &mut fmt::Formatter<'_>) -> fmt::Result {
        formatter
            .debug_struct("RevalidatedRecognition")
            .field("request_id", &self.request_id)
            .field("query_count", &self.queries.len())
            .finish()
    }
}

#[derive(Clone, Eq, PartialEq)]
enum CompletionDisposition {
    Abstain,
    Recognized(RevalidatedRecognition),
}

#[derive(Clone, Eq, PartialEq)]
pub struct RecognitionCompletion {
    request_id: RecognitionRequestId,
    disposition: CompletionDisposition,
}

impl RecognitionCompletion {
    #[must_use]
    pub const fn abstain(request_id: RecognitionRequestId) -> Self {
        Self {
            request_id,
            disposition: CompletionDisposition::Abstain,
        }
    }

    #[must_use]
    pub fn recognized(recognition: RevalidatedRecognition) -> Self {
        Self {
            request_id: recognition.request_id,
            disposition: CompletionDisposition::Recognized(recognition),
        }
    }

    #[must_use]
    pub const fn request_id(&self) -> RecognitionRequestId {
        self.request_id
    }
}

impl fmt::Debug for RecognitionCompletion {
    fn fmt(&self, formatter: &mut fmt::Formatter<'_>) -> fmt::Result {
        formatter
            .debug_struct("RecognitionCompletion")
            .field("request_id", &self.request_id)
            .field(
                "recognized",
                &matches!(self.disposition, CompletionDisposition::Recognized(_)),
            )
            .finish()
    }
}

#[derive(Clone, Eq, PartialEq)]
pub struct Outbound {
    bytes: Vec<u8>,
}

impl Outbound {
    fn new(bytes: Vec<u8>) -> Self {
        Self { bytes }
    }

    #[must_use]
    pub fn as_bytes(&self) -> &[u8] {
        &self.bytes
    }

    #[must_use]
    pub fn into_bytes(self) -> Vec<u8> {
        self.bytes
    }
}

impl fmt::Debug for Outbound {
    fn fmt(&self, formatter: &mut fmt::Formatter<'_>) -> fmt::Result {
        formatter
            .debug_struct("Outbound")
            .field("bytes", &self.bytes.len())
            .finish()
    }
}

#[derive(Clone, Debug, Eq, PartialEq)]
pub enum ServiceAction {
    Write(Outbound),
    Recognize(RecognitionRequest),
}

#[derive(Clone, Copy)]
struct PendingRecognition {
    id: RecognitionRequestId,
    deadline: MonotonicMillis,
}

pub struct ServiceConnection {
    decoder: WireDecoder,
    connection_deadline: MonotonicMillis,
    last_now: MonotonicMillis,
    request_count: usize,
    next_request_id: u64,
    pending: Option<PendingRecognition>,
    closed: bool,
}

impl ServiceConnection {
    pub fn new(started_at: MonotonicMillis) -> Result<Self> {
        Ok(Self {
            decoder: WireDecoder::new(),
            connection_deadline: started_at.checked_add(CONNECTION_LIFETIME_MILLIS)?,
            last_now: started_at,
            request_count: 0,
            next_request_id: 1,
            pending: None,
            closed: false,
        })
    }

    #[must_use]
    pub const fn connection_deadline(&self) -> MonotonicMillis {
        self.connection_deadline
    }

    #[must_use]
    pub const fn request_count(&self) -> usize {
        self.request_count
    }

    #[must_use]
    pub const fn is_closed(&self) -> bool {
        self.closed
    }

    #[must_use]
    pub const fn has_pending_recognition(&self) -> bool {
        self.pending.is_some()
    }

    pub fn receive(&mut self, bytes: &[u8], now: MonotonicMillis) -> Result<Vec<ServiceAction>> {
        self.check_time(now)?;
        if self.pending.is_some() && !bytes.is_empty() {
            return self.fail(RuntimeError::RequestInFlight);
        }

        let events = match self.decoder.push(bytes) {
            Ok(events) => events,
            Err(error) => return self.fail(error),
        };
        let mut actions = Vec::new();

        for event in events {
            if self.request_count >= MAX_REQUESTS_PER_CONNECTION {
                return self.fail(RuntimeError::RequestLimit);
            }
            self.request_count += 1;

            match event {
                RequestEvent::Describe => {
                    if self.pending.is_some() {
                        return self.fail(RuntimeError::RequestInFlight);
                    }
                    let response = match encode_info() {
                        Ok(response) => response,
                        Err(error) => return self.fail(error),
                    };
                    actions.push(ServiceAction::Write(Outbound::new(response)));
                }
                RequestEvent::Transcript(input) => {
                    if self.pending.is_some() {
                        return self.fail(RuntimeError::RequestInFlight);
                    }
                    let id = RecognitionRequestId(self.next_request_id);
                    self.next_request_id = self
                        .next_request_id
                        .checked_add(1)
                        .ok_or(RuntimeError::RequestLimit)?;
                    let request_deadline = now
                        .checked_add(RECOGNITION_DEADLINE_MILLIS)?
                        .min(self.connection_deadline);
                    self.pending = Some(PendingRecognition {
                        id,
                        deadline: request_deadline,
                    });
                    actions.push(ServiceAction::Recognize(RecognitionRequest {
                        id,
                        deadline: request_deadline,
                        input,
                    }));
                }
            }
        }

        if self.pending.is_some() && !self.decoder.is_idle() {
            return self.fail(RuntimeError::PayloadNotAllowed);
        }
        Ok(actions)
    }

    pub fn poll(&mut self, now: MonotonicMillis) -> Result<Vec<ServiceAction>> {
        self.check_time(now)?;
        if self.pending.is_some_and(|pending| now >= pending.deadline) {
            self.pending = None;
            let response = match encode_not_recognized() {
                Ok(response) => response,
                Err(error) => return self.fail(error),
            };
            return Ok(vec![ServiceAction::Write(Outbound::new(response))]);
        }
        Ok(Vec::new())
    }

    pub fn complete(
        &mut self,
        completion: RecognitionCompletion,
        now: MonotonicMillis,
    ) -> Result<ServiceAction> {
        self.check_time(now)?;
        let pending = self.pending.ok_or(RuntimeError::NoPendingRecognition)?;
        if completion.request_id != pending.id {
            return self.fail(RuntimeError::WrongRecognitionRequest);
        }
        self.pending = None;

        let response = if now >= pending.deadline {
            encode_not_recognized()
        } else {
            match completion.disposition {
                CompletionDisposition::Abstain => encode_not_recognized(),
                CompletionDisposition::Recognized(recognition) => {
                    let names = recognition
                        .queries
                        .into_iter()
                        .map(|query| query.exact_name)
                        .collect::<Vec<_>>();
                    encode_recognized(&names)
                }
            }
        };
        match response {
            Ok(response) => Ok(ServiceAction::Write(Outbound::new(response))),
            Err(error) => self.fail(error),
        }
    }

    pub fn finish(&mut self, now: MonotonicMillis) -> Result<()> {
        self.check_time(now)?;
        if let Err(error) = self.decoder.finish() {
            return self.fail(error);
        }
        self.pending = None;
        self.closed = true;
        Ok(())
    }

    fn check_time(&mut self, now: MonotonicMillis) -> Result<()> {
        if self.closed {
            return Err(RuntimeError::ConnectionClosed);
        }
        if now < self.last_now {
            return self.fail(RuntimeError::TimeRegressed);
        }
        self.last_now = now;
        if now >= self.connection_deadline {
            return self.fail(RuntimeError::ConnectionDeadline);
        }
        Ok(())
    }

    fn fail<T>(&mut self, error: RuntimeError) -> Result<T> {
        self.pending = None;
        self.closed = true;
        Err(error)
    }
}
