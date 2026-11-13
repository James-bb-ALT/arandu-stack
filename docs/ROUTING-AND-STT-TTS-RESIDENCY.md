# Roteamento NLU → Decision → LLM e Residência Real de STT/TTS (`v11.1.3`)

## 1. Contrato único de encaminhamento (`fallback_policy.py`)

O módulo puro `custom_components/local_nlu/fallback_policy.py` unifica produção (`intelligence.py`, `contextual_runtime.py`) e AI Lab (`ai_lab_core.py`) sob `classify_fallback()` e `RouteCategory`:
- `NLU_RESOLVED`, `RECOVERABLE`, `CLARIFY`, `SAFETY_BLOCK`, `PENDING_DIALOGUE`, `UNSUPPORTED`.
- `target_not_found` (sem candidatos) e `no_compatible_capability` (com ação/domínio recuperável) deixam de ser rotulados como `nlu_safety_or_dialogue_refusal`.
- Precedência absoluta para bloqueios de segurança (`permission_denied`, `stale`, `semantic_negation`, `semantic_condition`, `unsupported_exclusion`, `prompt_injection`, `user_scope_unknown=False`, `explicitly_denied=True`) e sessões pendentes.

## 2. Consulta agregada determinística de luzes (`light_aggregation.py`)

- Consultas read-only como `"Quantas luzes estão ligadas na casa?"`, `"quantas luzes acesas tem?"`, `"quantas luzes estão desligadas?"`, `"quantas luzes na cozinha estão ligadas?"`, `"tem alguma luz acesa?"` e `"quais luzes estão acesas?"` são avaliadas deterministicamente sobre entidades `light` autorizadas e deduplicadas por `entity_id`.
- Diferencia zero luzes ligadas de ausência de luzes autorizadas e informa luzes `unknown`/`unavailable` sem revelar entidades ocultas e sem depender de `origin_area` ou LLM.

## 3. Residência real de STT e TTS (`RESIDENT` / `LAZY` / `OFF`)

- **STT (`RESIDENT` padrão):** `Engine` pré-carrega o backend no startup em `RESIDENT`, adia para o primeiro áudio com lock em `LAZY` (`state='waiting_first_use'`) e bloqueia inferência com `stt_disabled` em `OFF`.
- **TTS (`RESIDENT` padrão):** `SpeechEngine` e `PocketEngine` pré-carregam a voz principal selecionada (Piper ou Pocket) em `RESIDENT`, mantêm cache limitado fixando a voz primária, adiam para a primeira síntese em `LAZY` e bloqueiam síntese Wyoming/Web com `tts_disabled` em `OFF`. Configurações legadas (`preload`, `pocket_preload`) são migradas automaticamente.
- **Dashboard & Control Plane:** `GET /api/stt/models` e `GET /api/tts/status` expõem telemetria real de carga, estado e RSS do processo.

## 4. Limitações de verificação

Testes unitários e de integração executados com stubs/mocks de backends STT/TTS e LLM; medição física em hardware HAOS/satélites permanece **PENDENTE**.
