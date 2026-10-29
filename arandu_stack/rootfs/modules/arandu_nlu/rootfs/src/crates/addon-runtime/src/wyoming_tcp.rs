use core::fmt;
use std::io::{self, Read, Write};
use std::net::{Shutdown, TcpStream};
use std::time::Duration;

use wyoming_runtime::{
    CONNECTION_LIFETIME_MILLIS, MonotonicMillis, RecognitionCompletion, RecognitionRequest,
    RuntimeError as WyomingRuntimeError, ServiceAction, ServiceConnection,
};

pub const TCP_READ_BUFFER_BYTES: usize = 4_096;
pub const MAX_TCP_IO_TIMEOUT: Duration = Duration::from_secs(5);
pub const DEFAULT_TCP_READ_TIMEOUT: Duration = Duration::from_secs(2);
pub const DEFAULT_TCP_WRITE_TIMEOUT: Duration = Duration::from_secs(2);

pub type WyomingTcpResult<T> = core::result::Result<T, WyomingTcpError>;

#[derive(Clone, Copy, Debug, Eq, PartialEq)]
pub enum WyomingTcpError {
    InvalidTimeout,
    SocketConfiguration,
    ReadTimeout,
    ReadIo,
    WriteTimeout,
    WriteIo,
    RecognitionProvider,
    UnexpectedServiceAction,
    CounterOverflow,
    Protocol(WyomingRuntimeError),
}

impl fmt::Display for WyomingTcpError {
    fn fmt(&self, formatter: &mut fmt::Formatter<'_>) -> fmt::Result {
        formatter.write_str(match self {
            Self::InvalidTimeout => "wyoming TCP timeout configuration is invalid",
            Self::SocketConfiguration => "wyoming TCP socket configuration failed",
            Self::ReadTimeout => "wyoming TCP read timed out",
            Self::ReadIo => "wyoming TCP read failed",
            Self::WriteTimeout => "wyoming TCP write timed out",
            Self::WriteIo => "wyoming TCP write failed",
            Self::RecognitionProvider => "wyoming recognition provider failed",
            Self::UnexpectedServiceAction => "wyoming service emitted an unexpected action",
            Self::CounterOverflow => "wyoming TCP byte counter overflowed",
            Self::Protocol(_) => "wyoming protocol rejected the connection",
        })
    }
}

impl std::error::Error for WyomingTcpError {}

impl From<WyomingRuntimeError> for WyomingTcpError {
    fn from(error: WyomingRuntimeError) -> Self {
        Self::Protocol(error)
    }
}

#[derive(Clone, Copy, Debug, Eq, PartialEq)]
pub struct WyomingTcpConfig {
    read_timeout: Duration,
    write_timeout: Duration,
}

impl WyomingTcpConfig {
    pub fn new(read_timeout: Duration, write_timeout: Duration) -> WyomingTcpResult<Self> {
        if !valid_timeout(read_timeout) || !valid_timeout(write_timeout) {
            return Err(WyomingTcpError::InvalidTimeout);
        }
        Ok(Self {
            read_timeout,
            write_timeout,
        })
    }

    #[must_use]
    pub const fn read_timeout(self) -> Duration {
        self.read_timeout
    }

    #[must_use]
    pub const fn write_timeout(self) -> Duration {
        self.write_timeout
    }
}

impl Default for WyomingTcpConfig {
    fn default() -> Self {
        Self {
            read_timeout: DEFAULT_TCP_READ_TIMEOUT,
            write_timeout: DEFAULT_TCP_WRITE_TIMEOUT,
        }
    }
}

#[derive(Clone, Copy, Debug, Eq, PartialEq)]
pub struct WyomingTcpReport {
    request_count: usize,
    bytes_read: u64,
    bytes_written: u64,
}

impl WyomingTcpReport {
    #[must_use]
    pub const fn request_count(self) -> usize {
        self.request_count
    }

    #[must_use]
    pub const fn bytes_read(self) -> u64 {
        self.bytes_read
    }

    #[must_use]
    pub const fn bytes_written(self) -> u64 {
        self.bytes_written
    }
}

pub trait MonotonicClock {
    fn now(&mut self) -> MonotonicMillis;
}

impl<F> MonotonicClock for F
where
    F: FnMut() -> MonotonicMillis,
{
    fn now(&mut self) -> MonotonicMillis {
        self()
    }
}

pub trait RecognitionProvider {
    type Error;

    fn recognize(
        &mut self,
        request: &RecognitionRequest,
    ) -> core::result::Result<RecognitionCompletion, Self::Error>;
}

pub fn drive_wyoming_tcp<C, P>(
    mut stream: TcpStream,
    clock: &mut C,
    provider: &mut P,
    config: WyomingTcpConfig,
) -> WyomingTcpResult<WyomingTcpReport>
where
    C: MonotonicClock,
    P: RecognitionProvider,
{
    let result = drive_connection(&mut stream, clock, provider, config);
    if result.is_err() {
        let _ = stream.shutdown(Shutdown::Both);
    }
    result
}

fn drive_connection<C, P>(
    stream: &mut TcpStream,
    clock: &mut C,
    provider: &mut P,
    config: WyomingTcpConfig,
) -> WyomingTcpResult<WyomingTcpReport>
where
    C: MonotonicClock,
    P: RecognitionProvider,
{
    validate_config(config)?;
    stream
        .set_nodelay(true)
        .and_then(|()| stream.set_read_timeout(Some(config.read_timeout)))
        .and_then(|()| stream.set_write_timeout(Some(config.write_timeout)))
        .map_err(|_| WyomingTcpError::SocketConfiguration)?;

    let started_at = clock.now();
    let mut service = ServiceConnection::new(started_at)?;
    let mut bytes_read = 0_u64;
    let mut bytes_written = 0_u64;
    let mut buffer = [0_u8; TCP_READ_BUFFER_BYTES];

    loop {
        let now = clock.now();
        let polled_actions = service.poll(now)?;
        write_actions(
            stream,
            clock,
            provider,
            &mut service,
            polled_actions,
            config,
            &mut bytes_written,
        )?;
        set_bounded_timeout(
            stream,
            TimeoutDirection::Read,
            config.read_timeout,
            now,
            service.connection_deadline(),
        )?;

        match stream.read(&mut buffer) {
            Ok(0) => {
                service.finish(clock.now())?;
                let _ = stream.shutdown(Shutdown::Both);
                return Ok(WyomingTcpReport {
                    request_count: service.request_count(),
                    bytes_read,
                    bytes_written,
                });
            }
            Ok(length) => {
                bytes_read = bytes_read
                    .checked_add(
                        u64::try_from(length).map_err(|_| WyomingTcpError::CounterOverflow)?,
                    )
                    .ok_or(WyomingTcpError::CounterOverflow)?;
                let actions = service.receive(&buffer[..length], clock.now())?;
                write_actions(
                    stream,
                    clock,
                    provider,
                    &mut service,
                    actions,
                    config,
                    &mut bytes_written,
                )?;
            }
            Err(error) if error.kind() == io::ErrorKind::Interrupted => {}
            Err(error) if is_timeout(&error) => {
                let now = clock.now();
                if now >= service.connection_deadline() {
                    service.poll(now)?;
                }
                return Err(WyomingTcpError::ReadTimeout);
            }
            Err(_) => return Err(WyomingTcpError::ReadIo),
        }
    }
}

#[allow(clippy::too_many_arguments)]
fn write_actions<C, P>(
    stream: &mut TcpStream,
    clock: &mut C,
    provider: &mut P,
    service: &mut ServiceConnection,
    actions: Vec<ServiceAction>,
    config: WyomingTcpConfig,
    bytes_written: &mut u64,
) -> WyomingTcpResult<()>
where
    C: MonotonicClock,
    P: RecognitionProvider,
{
    for action in actions {
        match action {
            ServiceAction::Write(outbound) => {
                write_outbound(
                    stream,
                    clock,
                    service,
                    outbound.as_bytes(),
                    config,
                    bytes_written,
                )?;
            }
            ServiceAction::Recognize(request) => {
                let completion = provider
                    .recognize(&request)
                    .map_err(|_| WyomingTcpError::RecognitionProvider)?;
                let completed = service.complete(completion, clock.now())?;
                let ServiceAction::Write(outbound) = completed else {
                    return Err(WyomingTcpError::UnexpectedServiceAction);
                };
                write_outbound(
                    stream,
                    clock,
                    service,
                    outbound.as_bytes(),
                    config,
                    bytes_written,
                )?;
            }
        }
    }
    Ok(())
}

fn write_outbound<C>(
    stream: &mut TcpStream,
    clock: &mut C,
    service: &ServiceConnection,
    bytes: &[u8],
    config: WyomingTcpConfig,
    bytes_written: &mut u64,
) -> WyomingTcpResult<()>
where
    C: MonotonicClock,
{
    let now = clock.now();
    set_bounded_timeout(
        stream,
        TimeoutDirection::Write,
        config.write_timeout,
        now,
        service.connection_deadline(),
    )?;
    stream.write_all(bytes).map_err(|error| {
        if is_timeout(&error) {
            WyomingTcpError::WriteTimeout
        } else {
            WyomingTcpError::WriteIo
        }
    })?;
    stream.flush().map_err(|error| {
        if is_timeout(&error) {
            WyomingTcpError::WriteTimeout
        } else {
            WyomingTcpError::WriteIo
        }
    })?;
    *bytes_written = bytes_written
        .checked_add(u64::try_from(bytes.len()).map_err(|_| WyomingTcpError::CounterOverflow)?)
        .ok_or(WyomingTcpError::CounterOverflow)?;
    Ok(())
}

#[derive(Clone, Copy)]
enum TimeoutDirection {
    Read,
    Write,
}

fn set_bounded_timeout(
    stream: &TcpStream,
    direction: TimeoutDirection,
    configured: Duration,
    now: MonotonicMillis,
    deadline: MonotonicMillis,
) -> WyomingTcpResult<()> {
    let remaining_millis =
        deadline
            .get()
            .checked_sub(now.get())
            .ok_or(WyomingTcpError::Protocol(
                WyomingRuntimeError::ConnectionDeadline,
            ))?;
    if remaining_millis == 0 {
        return Err(WyomingTcpError::Protocol(
            WyomingRuntimeError::ConnectionDeadline,
        ));
    }
    let timeout = configured.min(Duration::from_millis(remaining_millis));
    let result = match direction {
        TimeoutDirection::Read => stream.set_read_timeout(Some(timeout)),
        TimeoutDirection::Write => stream.set_write_timeout(Some(timeout)),
    };
    result.map_err(|_| WyomingTcpError::SocketConfiguration)
}

fn validate_config(config: WyomingTcpConfig) -> WyomingTcpResult<()> {
    if !valid_timeout(config.read_timeout) || !valid_timeout(config.write_timeout) {
        return Err(WyomingTcpError::InvalidTimeout);
    }
    Ok(())
}

fn valid_timeout(timeout: Duration) -> bool {
    !timeout.is_zero()
        && timeout <= MAX_TCP_IO_TIMEOUT
        && timeout <= Duration::from_millis(CONNECTION_LIFETIME_MILLIS)
}

fn is_timeout(error: &io::Error) -> bool {
    matches!(
        error.kind(),
        io::ErrorKind::TimedOut | io::ErrorKind::WouldBlock
    )
}
