use core::fmt;

#[derive(Clone, Copy, Debug, Eq, PartialEq)]
pub enum ServerErrorCode {
    InvalidConfiguration,
    UnsanitizedEnvironment,
    InvalidSocketPath,
    SocketPathOccupied,
    SocketBind,
    SocketAccept,
    SocketConfiguration,
    PeerCredentials,
    RequestQueueFull,
    RequestTimeout,
    FrameIo,
    EmptyFrame,
    FrameTooLarge,
    ResponseTooLarge,
    SnapshotGeneration,
    RuntimeConfiguration,
    RuntimeState,
    RequestProcessing,
    LockPoisoned,
    WorkerPanic,
}

impl ServerErrorCode {
    #[must_use]
    pub const fn code(self) -> &'static str {
        match self {
            Self::InvalidConfiguration => "server_invalid_configuration",
            Self::UnsanitizedEnvironment => "server_unsanitized_environment",
            Self::InvalidSocketPath => "server_invalid_socket_path",
            Self::SocketPathOccupied => "server_socket_path_occupied",
            Self::SocketBind => "server_socket_bind",
            Self::SocketAccept => "server_socket_accept",
            Self::SocketConfiguration => "server_socket_configuration",
            Self::PeerCredentials => "server_peer_credentials",
            Self::RequestQueueFull => "server_request_queue_full",
            Self::RequestTimeout => "server_request_timeout",
            Self::FrameIo => "server_frame_io",
            Self::EmptyFrame => "server_empty_frame",
            Self::FrameTooLarge => "server_frame_too_large",
            Self::ResponseTooLarge => "server_response_too_large",
            Self::SnapshotGeneration => "server_snapshot_generation",
            Self::RuntimeConfiguration => "server_runtime_configuration",
            Self::RuntimeState => "server_runtime_state",
            Self::RequestProcessing => "server_request_processing",
            Self::LockPoisoned => "server_lock_poisoned",
            Self::WorkerPanic => "server_worker_panic",
        }
    }
}

#[derive(Clone, Copy, Debug, Eq, PartialEq)]
pub struct ServerError {
    code: ServerErrorCode,
}

impl ServerError {
    pub(crate) const fn new(code: ServerErrorCode) -> Self {
        Self { code }
    }

    #[must_use]
    pub const fn code(self) -> ServerErrorCode {
        self.code
    }
}

impl fmt::Display for ServerError {
    fn fmt(&self, formatter: &mut fmt::Formatter<'_>) -> fmt::Result {
        formatter.write_str(self.code.code())
    }
}

impl std::error::Error for ServerError {}

pub type Result<T> = core::result::Result<T, ServerError>;
