# Arandu v11.2.0 — Pipeline Universal & Model Hub Unificado

## Resumo das Correções (R01–R16)

| ID | Área | Correção aplicada |
|----|------|-------------------|
| R01 | `contextual_runtime.py` | Removido o gate `eligible()` antes do orquestrador progressivo V5 para que toda entrada não-reservada possa percorrer NLU v5 -> Decision -> LLM + Home Graph. |
| R02 | `fallback_policy.py` | Regra genérica: qualquer status diferente de `plan` (exceto `SAFETY_BLOCK`, `PENDING_DIALOGUE` e `invalid_request`) roteia como `RECOVERABLE` para `Decision`/`LLM`. |
| R03 | `contextual_runtime.py` | Falhas no orquestrador V5 agora registram `last_v5_error` e `LOGGER.warning(...)` em vez de supressão silenciosa. |
| R04 | `arandu_llm/structured.py` | Removido o retorno precoce de `clarification` antes de `completion()` para casos recuperáveis, preservando bloqueios determinísticos de segurança antes do carregamento de pesos. |
| R05 | `automation_contract.py` | Validação v2 verifica autorização do alvo no catálogo e compatibilidade de capacidade sem exigir igualdade circular com `analyze()`. |
| R06 | `progressive_orchestrator.py` | Leitura de `response_text`, `question` e `plan.question` em respostas `clarification`/`refusal`. |
| R07 | `contextual_runtime.py` | `result(..., preserve_clarification=True)` preserva esclarecimentos `missing_slot` vindos do pipeline progressivo sem sobrescrever com `'Não entendi.'`. |
| R08 | `progressive_contract.py`, `dialogue_coordinator.py` | Introduzido `UnderstandingFrameV2` (`frame_version=2`) e `PendingRequest.merge_into_frame()` para preenchimento tipado de slots sem concatenação `original (reply)`. |
| R09 | `queries_set.py` | Tokens de domínio e prefixos de consulta agora derivam dinamicamente de `generated_semantics_ptbr` (`SURFACE_TO_CONCEPTS` e `GROUP_FORMS["query"]`). |
| R10 | `ai_lab_core.py` | Campo `diagnostics` unificado com `nlu_status`, `route_reason`, `decision_status`, `llm_called`, `llm_generated_tokens`, `final_stage`, `terminal_reason`, `conversation_id` e `timings_ms`. |
| R11 | `contextual_runtime.py` | `self._progressive_orchestrator` instanciado uma única vez por `ContextualRuntime`, compartilhando `HomeGraphCache` entre requisições. |
| R12 | `model_registry`, `arandu_llm`, `server.py`, `app.js` | Registro único em `arandu_stack/model_registry/models.json` + `model_registry.py`; 5 LLMs baixáveis (`tucano`, `qwen3`/`qwen3-0.6b`, `lfm2.5-1.2b`, `lfm3-1.7b`, `qwen3-1.7b`) e 2 catalogados como `source_unverified` (`qwen3.5-2b`, `qwen3-4b-instruct-2507`). |
| R13 | `arandu_decision`, `server.py` | Todos os 3 modelos Decision (`e5-small`, `minilm-multilingual`, `e5-base`) integrados ao hub unificado e `request_traces.py`. |
| R14 | `arandu_stt/model_download.py` | `ADAPTED_URL` corrigida para `https://github.com/James-bb-ALT/arandu-stack/releases/download/stt-model-v2.2.0`. |
| R15 | `server.py`, `app.js` | Adicionados endpoints `GET /api/tts/models` e `POST /api/tts/{action}` e listagem de vozes no gerenciador TTS da UI, respeitando `needs_auth` (`pocket-rafael`) e descoberta local (`pt_BR-arandu-medium`). |
| R16 | `arandu_llm/structured.py` | Histórico recente (`deque(maxlen=16)`) e penalidades `frequency_penalty`/`presence_penalty` para respostas conversacionais não-repetitivas. |

## Escopo de Validação

- **Executado neste ambiente (Windows dev / sem GPU NVIDIA / sem daemon HAOS):**
  - `cargo test --locked --offline` no motor Rust NLU (`arandu_stack/rootfs/modules/arandu_nlu/rootfs/src/addon/engine`).
  - `pytest tests/` incluindo a nova matriz `tests/test_pipeline_universal_v11_2.py` (1.050 expressões, 200 aliases arbitrários, 100 pares `not_equivalent`, 100 diálogos multi-turno, 100 falhas recuperáveis, 50 consultas coletivas, 50 recusas de segurança com 0 chamadas HA, 20 casos de paridade AI Lab e 25 itens do `model_registry`).
- **Pendente em hardware real (HAOS amd64 Intel i5-6500T / 16 GiB RAM):**
  - Download completo, verificação SHA-256 em `/data` e carga real em RAM/OpenVINO dos 25 modelos no add-on HAOS em execução.
  - Modelos `qwen3.5-2b`, `qwen3-4b-instruct-2507` e vozes Piper sem pinagem permanecem marcados como `downloadable=false` (`source_unverified`), e `pocket-rafael` permanece marcado como `needs_auth=true` (`GATED`).
