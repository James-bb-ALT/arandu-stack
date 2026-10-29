use core::fmt;

pub type Result<T> = core::result::Result<T, RuntimeError>;

#[derive(Clone, Copy, Debug, Eq, PartialEq)]
pub enum RuntimeError {
    EmptyGraph,
    TooManyNodes,
    InvalidNodeId,
    DuplicateNodeId,
    InvalidDependency,
    InvalidTimeout,
    GraphBindingMismatch,
    SequenceMismatch,
    DuplicateNodeAttempt,
    TargetBindingMismatch,
    UnsupportedOperation,
    InvalidExecutionClass,
    PlanDigestMismatch,
    WireEncoding,
    WireTooLarge,
    ChannelIo,
    ChannelOfferRejected,
    ChannelHandshakeRejected,
    ChannelFrameRejected,
    IpcIo,
    InvalidSubmission,
    InvalidHelperReply,
    InvalidPairingMaterial,
    InvalidProcessConfiguration,
    PairingProtocol,
    PairingRejected,
    RuntimeDirectory,
    RuntimeDirectorySecurity,
    ServerSpawn,
    CatalogFrame,
    CatalogSnapshot,
    HelperStartup,
    ServerStartup,
    ServerExchange,
    ProtocolExchange,
    InterpretationRejected,
    PlanMapping,
}

impl RuntimeError {
    #[must_use]
    pub const fn code(self) -> &'static str {
        match self {
            Self::EmptyGraph => "empty_graph",
            Self::TooManyNodes => "too_many_nodes",
            Self::InvalidNodeId => "invalid_node_id",
            Self::DuplicateNodeId => "duplicate_node_id",
            Self::InvalidDependency => "invalid_dependency",
            Self::InvalidTimeout => "invalid_timeout",
            Self::GraphBindingMismatch => "graph_binding_mismatch",
            Self::SequenceMismatch => "sequence_mismatch",
            Self::DuplicateNodeAttempt => "duplicate_node_attempt",
            Self::TargetBindingMismatch => "target_binding_mismatch",
            Self::UnsupportedOperation => "unsupported_operation",
            Self::InvalidExecutionClass => "invalid_execution_class",
            Self::PlanDigestMismatch => "plan_digest_mismatch",
            Self::WireEncoding => "wire_encoding",
            Self::WireTooLarge => "wire_too_large",
            Self::ChannelIo => "channel_io",
            Self::ChannelOfferRejected => "channel_offer_rejected",
            Self::ChannelHandshakeRejected => "channel_handshake_rejected",
            Self::ChannelFrameRejected => "channel_frame_rejected",
            Self::IpcIo => "ipc_io",
            Self::InvalidSubmission => "invalid_submission",
            Self::InvalidHelperReply => "invalid_helper_reply",
            Self::InvalidPairingMaterial => "invalid_pairing_material",
            Self::InvalidProcessConfiguration => "invalid_process_configuration",
            Self::PairingProtocol => "pairing_protocol",
            Self::PairingRejected => "pairing_rejected",
            Self::RuntimeDirectory => "runtime_directory",
            Self::RuntimeDirectorySecurity => "runtime_directory_security",
            Self::ServerSpawn => "server_spawn",
            Self::CatalogFrame => "catalog_frame",
            Self::CatalogSnapshot => "catalog_snapshot",
            Self::HelperStartup => "helper_startup",
            Self::ServerStartup => "server_startup",
            Self::ServerExchange => "server_exchange",
            Self::ProtocolExchange => "protocol_exchange",
            Self::InterpretationRejected => "interpretation_rejected",
            Self::PlanMapping => "plan_mapping",
        }
    }
}

impl fmt::Display for RuntimeError {
    fn fmt(&self, formatter: &mut fmt::Formatter<'_>) -> fmt::Result {
        formatter.write_str(self.code())
    }
}

impl std::error::Error for RuntimeError {}
