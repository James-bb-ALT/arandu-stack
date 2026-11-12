# Entrega Conversacional do LLM (Assist, AI Lab e TTS) — `v11.1.2`

Este documento descreve o contrato e o fluxo ponta-a-ponta de respostas conversacionais (`kind: "conversation"`) introduzido/corrigido no hotfix `v11.1.2`.

## 1. Problema resolvido

Anteriormente, respostas conversacionais válidas retornadas pelo LLM no contrato v2 (`contract_version: 2, kind: "conversation", response_text: "..."`) podiam:
- Receber `clarification_question = "Não entendi o que você deseja..."` no `ai_lab_core.pipeline`, mascarando a resposta conversacional.
- Cair em `KeyError: 'proposal'` no `ai_lab.py` quando `raw['kind'] == 'conversation'` não possuía o campo legado `proposal`.
- Falar `clarification_question` ou `proposal.reason` no `ai_lab_panel.js::speak()` caso `response_text` estivesse vazio.
- Cair no fluxo legado em `intelligence.py::fallback()` sem validação explícita de `kind == "conversation"` e sem erro explícito `llm_conversation_empty` / `llm_conversation_contradiction`.

## 2. Contrato e garantias

1. **Resposta conversacional válida (`kind == "conversation"` + `1 <= len(response_text.strip()) <= 1000`):**
   - `intelligence.py::fallback`: retorna `proposed=()`, `trace['llm']['status'] = 'conversation'` e `response_text` preservado.
   - `contextual_runtime.py`: retorna `RuntimeResult(code='query_success', speech=reply, reason='llm_conversation')`.
   - `conversation.py::_render`: entrega `speech=reply`, `error_code=None` ao Assist/TTS, sem chamar serviços de dispositivos do HA.
   - `ai_lab_core.py::pipeline`: define `final_kind = 'conversation_response'`, `clarification_question = None`, `requires_review = False`, `response_text = reply.strip()`.
   - `ai_lab.py` e `ai_lab_panel.js`: exibem `response_text` com estágios `validation: skipped ("Nenhuma ação HA proposta")` e `response: executed ("Resposta gerada pelo LLM")`; `speak()` sintetiza exclusivamente `response_text`.

2. **Envelope inválido ou vazio (`response_text` ausente/vazio/ > 1000 chars):**
   - `intelligence.py::fallback`: marca `trace['llm']['status'] = 'error'`, `reason = 'llm_conversation_empty'`.
   - `ai_lab_core.py::pipeline`: marca `status = 'error'`, `final_kind = 'error'`, `reason = 'llm_conversation_empty'`, `clarification_question = None`.
   - `ai_lab_panel.js::speak()`: lança erro explícito (`llm_conversation_empty`) e nunca sintetiza texto de clarificação genérica.

3. **Envelope contraditório (`kind == "conversation"` com `operations` ou `proposal.target_entities`):**
   - `intelligence.py::fallback`: rejeita com `status = 'error'`, `reason = 'llm_conversation_contradiction'`.

## 3. Verificação automatizada

Coberto por `tests/test_llm_conversation_delivery.py` (mocks de LLM e fixtures locais; reprodução física em satélite HAOS não executada nesta entrega).
