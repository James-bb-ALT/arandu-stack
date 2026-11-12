# Arandu Assistant 11.0.1 — Correção de Consistência do NLU Pós-Merge 5+6 (2026-11-12)

Release corretiva incremental sobre a base `11.0.0` (`51017f1453b33060ed37bbd4061990a61f36944a`) para eliminar inconsistências de sessão, autorização, ciclo de vida temporal e consultas parciais sem alterar Decision (`3.0.0`), LLM (`2.0.0`), STT (`4.0.2`) ou TTS (`0.2.2`).

## 1. Matriz Problema → Correção → Evidência

| Problema | Causa | Arquivo / Função | Mudança | Teste | Resultado |
|---|---|---|---|---|---|
| Bypass de confirmação em broadcast de 2 turnos | `ClarifyMessageSession` chamava `_execute_delivery` direto | `announcement_router.py` (`_prepare_announcement`) | Unificação em `_prepare_announcement`; `kind=="all"` sempre gera `ConfirmationSession` e revalida política/fingerprint | `tests/test_nlu_postmerge_announcements.py::test_two_stage_broadcast_requires_confirmation` | Aprovado |
| Mudança de política entre pedido e `"sim"` aceita | Fingerprint não cobria política completa nem revalidava `enabled`/`allow_all`/quiet hours | `announcement_delivery.py` (`get_dest_config_hash`), `announcement_router.py` | Fingerprint inclui `enabled`, `allow_all`, `require_confirm_all`, `tts_entity`, `chime`, `max_targets`, `quiet_hours`, `conversation_id`; falha consome sessão | `tests/test_nlu_postmerge_announcements.py::test_config_shift_between_prompt_and_confirm_rejects` | Aprovado |
| Autorização falhava aberta (`fail-open`) | `is_entity_exposed` retornava `True` sem API e `check_user_permission` sem `permissions` | `announcement_delivery.py` (`is_entity_exposed`, `check_user_permission`, `deliver_announcement`) | Retorno `False` em ausência/exceção; checagem antes de cada `async_call`; parada em revogação com retorno `partial` / `accepted_by_ha` | `tests/test_nlu_postmerge_announcements.py::test_fail_closed_exposure_and_permissions`, `test_revocation_between_targets_stops_and_reports_partial` | Aprovado |
| Truncamento silencioso, substring e fallback inválido em `tts.speak` | `all_targets[:max_targets]`, `key in norm_dest` e `assist_satellite.*` enviado a `tts.speak` | `announcement_delivery.py` (`resolve_targets`, `_select_single_destination`, `_validate_target_ready`) | Recusa `too_many_targets`, exige match exato ou esclarecimento (`ambiguous_destination`), e bloqueia `tts.speak` sem `media_player` vinculado | `tests/test_nlu_postmerge_announcements.py::test_max_targets_exceeded_refuses_without_truncation`, `test_satellite_fallback_never_passes_satellite_to_tts_speak` | Aprovado |
| Separador `que` capturando dentro de palavras e 2º turno sem validação | Regex `\s*(?:que)` sem `\b` e falta de `validate_announcement_message` | `announcement_parser.py`, `announcement_router.py` | `\s+(?:...)\b` no parser e validação pura aplicada em todos os turnos | `tests/test_nlu_postmerge_announcements.py::test_parser_word_boundary_on_que_inside_destination`, `test_second_turn_invalid_messages_rejected_without_neural_fallback` | Aprovado |
| Horário silencioso no fuso do host e sessão global | `datetime.now()` local e `hass.data` sem `entry_id` | `announcement_options.py`, `announcement_router.py`, `__init__.py` | Uso de `homeassistant.util.dt`, store por `entry_id` e limpeza em `_async_options_updated`/`async_unload_entry` | `tests/test_nlu_postmerge_announcements.py::test_quiet_hours_overnight_with_ha_timezone` | Aprovado |
| Vazamento de `"sim"` entre Fase 5 e Fase 6 | `conversation.py` interceptava anúncio sem invalidar sessão operacional e vice-versa | `dialogue_coordinator.py`, `conversation.py`, `runtime.py`, `contextual_runtime.py` | `DialogueCoordinator` por `(user_id, origin_id, conversation_id, config_entry_id)` invalida sessão oposta antes de efeitos | `tests/test_nlu_postmerge_sessions.py` | Aprovado |
| Timers/lembretes órfãos em `ringing`, saturação e recorrência perdida | `_async_handle_due_jobs` marcava `ringing` para tudo; recorrência só em `async_dismiss_job` | `scheduled_jobs.py`, `scheduled_delivery.py` | `completed` para timer/lembrete entregue, `failed/saturated` na saturação, recorrência idempotente no disparo e status honesto (`notification_accepted` / `tts_service_accepted`) | `tests/test_nlu_postmerge_temporal.py` | Aprovado |
| Cancelamento/edição ambígua e `scheduled_action_cancel` quebrado | `async_cancel_jobs` cancelava tudo sem filtro; `handle_temporal` passava `name=target_action` | `scheduled_jobs.py`, `contextual_runtime.py` | `AmbiguousJobError` + esclarecimento determinístico em `contextual_runtime.py`; filtro por `target_action` e `targets` | `tests/test_nlu_postmerge_temporal.py` | Aprovado |
| Fidelidade `todo_update` e histórico parcial `today` rejeitado | `energy_result` exigia `last_end == end` exato mesmo em horas parciais | `engine/tests/test_phase5.rs`, `advanced_queries.py` (`energy_result`) | Cobertura verificada até última hora fechada (`0 <= gap < 3600`) informando `dados de HH:MM até HH:MM` sem extrapolar | `tests/test_nlu_postmerge_todo_history.py`, `cargo test --test test_phase5` | Aprovado |

## 2. Termos de Status de Entrega

- **`accepted_by_ha` / `tts_service_accepted` / `notification_accepted`:** O serviço do Home Assistant aceitou a chamada sem erro síncrono. **Não** comprova que o alto-falante reproduziu som audível (`audible_playback_confirmed` não é inferido sem telemetria real de hardware).
- **`partial`:** Pelo menos um destino aceitou a chamada e um destino subsequente falhou ou teve permissão revogada; a transmissão foi interrompida imediatamente.
- **`visual_only`:** Notificação persistente aceita, porém TTS indisponível ou com erro.

## 3. Matriz de Migração de Agendamentos Persistidos (`async_load`)

| Estado Persistido (`v11.0.0`) | `delivery_status` Persistido | Novo Estado (`v11.0.1`) | Efeito Executado no Restart? |
|---|---|---|---|
| `dispatching` ou `ringing` | `dispatching` | `interrupted` (`delivery_status='interrupted'`) | Não (sem replay de efeito incerto) |
| `ringing` | `saturated` | `failed` (`delivery_status='saturated'`) | Não (libera cota) |
| `ringing` (`timer`/`reminder`) | `delivered` | `completed` | Não (libera cota) |
| `pending` vencido (> 60s) com recorrência | `None` | Avança para próxima ocorrência `pending` futura | Reagenda |
| `pending` vencido (> 60s) sem recorrência | `None` | `missed` | Não |

## 4. Limitações Físicas Declaradas

- Todos os testes automatizados (402 Python + 104 Rust) executam em ambiente simulado de Home Assistant / HTTP local sem hardware HAOS físico ou máquina Intel i5-6500T.
- Nenhum aviso audível ou comando físico foi emitido na residência durante os testes.
- Requer validação em campo no HAOS real com satélites físicos para confirmar reprodução audível real e integração com Recorder 2026.11.1 em produção.
