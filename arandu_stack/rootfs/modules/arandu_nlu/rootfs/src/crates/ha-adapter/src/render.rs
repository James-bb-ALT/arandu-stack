use core::fmt;

use crate::ExecutionFailure;

#[derive(Clone, Copy, Debug, Eq, PartialEq)]
pub enum InterpretationReason {
    Ambiguous,
    Unsupported,
    StaleCatalog,
    InsufficientEvidence,
}

#[derive(Clone, Copy, Debug, Eq, PartialEq)]
pub enum InterpretationOutcome {
    Recognized { node_count: u16 },
    ClarificationRequired { option_count: u16 },
    Abstained(InterpretationReason),
}

#[derive(Clone, Copy, Debug, Eq, PartialEq)]
pub enum ExecutionRenderOutcome {
    NotAttempted,
    InProgress {
        completed_nodes: u16,
    },
    Completed {
        node_count: u16,
    },
    Stopped {
        completed_nodes: u16,
        failure: ExecutionFailure,
    },
    Indeterminate {
        completed_nodes: u16,
    },
}

#[derive(Clone, Eq, PartialEq)]
pub struct RenderedResponse(Box<str>);

impl RenderedResponse {
    fn fixed(value: &'static str) -> Self {
        Self(value.into())
    }

    #[must_use]
    pub fn as_str(&self) -> &str {
        &self.0
    }
}

impl fmt::Debug for RenderedResponse {
    fn fmt(&self, formatter: &mut fmt::Formatter<'_>) -> fmt::Result {
        formatter
            .debug_tuple("RenderedResponse")
            .field(&self.0)
            .finish()
    }
}

impl fmt::Display for RenderedResponse {
    fn fmt(&self, formatter: &mut fmt::Formatter<'_>) -> fmt::Result {
        formatter.write_str(&self.0)
    }
}

#[derive(Clone, Copy, Debug, Default, Eq, PartialEq)]
pub struct ResponseRenderer;

impl ResponseRenderer {
    #[must_use]
    pub fn render_interpretation(self, outcome: InterpretationOutcome) -> RenderedResponse {
        match outcome {
            InterpretationOutcome::Recognized { .. } => {
                RenderedResponse::fixed("Pedido reconhecido.")
            }
            InterpretationOutcome::ClarificationRequired { .. } => {
                RenderedResponse::fixed("Preciso de uma escolha.")
            }
            InterpretationOutcome::Abstained(InterpretationReason::Ambiguous) => {
                RenderedResponse::fixed("O pedido ficou ambíguo.")
            }
            InterpretationOutcome::Abstained(
                InterpretationReason::Unsupported
                | InterpretationReason::StaleCatalog
                | InterpretationReason::InsufficientEvidence,
            ) => RenderedResponse::fixed("Não consegui interpretar o pedido."),
        }
    }

    #[must_use]
    pub fn render_execution(self, outcome: ExecutionRenderOutcome) -> RenderedResponse {
        match outcome {
            ExecutionRenderOutcome::NotAttempted => {
                RenderedResponse::fixed("Nenhuma operação foi executada.")
            }
            ExecutionRenderOutcome::InProgress { .. } => {
                RenderedResponse::fixed("Execução em andamento.")
            }
            ExecutionRenderOutcome::Completed { .. } => {
                RenderedResponse::fixed("Operação concluída.")
            }
            ExecutionRenderOutcome::Stopped { .. } => {
                RenderedResponse::fixed("A execução foi interrompida.")
            }
            ExecutionRenderOutcome::Indeterminate { .. } => {
                RenderedResponse::fixed("Não foi possível confirmar o resultado da execução.")
            }
        }
    }
}
