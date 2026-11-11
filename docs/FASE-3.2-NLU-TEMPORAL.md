# Relatório Técnico — Fase 3.2: NLU Determinístico e Protocolos Temporais

**Módulo:** Arandu Stack / Arandu NLU 2.0  
**Branch de Entrega:** `feat/arandu-fase-3-2-nlu-temporal`  
**Base SHA:** `ca4e67d55d5f7b54c181057f4187432fc2d83cc7`  
**Escopo:** Agendamentos seguros, despertadores, lembretes, timers tipados/nomeados, exclusões coletivas por cômodo e léxico doméstico coloquial.

---

## 1. Sumário Executivo e Diretrizes

A **Fase 3.2** expande o motor contextual determinístico do **Arandu NLU** em Rust e a camada de execução companion no Home Assistant, implementando:
1. **Agendamentos de ações pontuais seguras** ("desliga o ar daqui a 30 minutos", "liga o ventilador às 18:00", "desliga a música em dez minutos").
2. **Despertadores e alarmes de relógio tipados** ("acorda às 7 da manhã", "define um alarme para as 22h", "quais despertadores eu tenho", "soneca", "desliga o alarme").
3. **Lembretes** ("me lembra de tomar o remédio às 15h", "cria um lembrete para comprar pão amanhã às 8 da manhã").
4. **Temporizadores nomeados e virtuais** ("coloca um timer de dez minutos", "marca um timer para pizza de 15 minutos", "quanto falta no timer da pizza", "cancela todos os timers", "acrescenta 5 minutos ao timer do arroz").
5. **Exclusões coletivas pontuais** ("desliga tudo, menos a TV", "apaga as luzes exceto o abajur", "desliga tudo menos a TV e o ventilador"), resolvendo escopo físico e `device_id` cruzado.
6. **Léxico doméstico coloquial de aspirador** ("passa o aspirador na sala", "limpa a cozinha", "manda o robô aspirador para a base", "pausa a limpeza").

### Não-Objetivos Estritos (Garantias de Segurança)
- **Zero automações permanentes por voz:** comandos condicionais permanentes não são criados via voz.
- **Zero condições contínuas de sensores:** expressões como *"se a temperatura passar de 28 graus liga o ar"* continuam bloqueadas com recusa fail-closed (`semantic_condition`).
- **Zero intercom/broadcasting:** não há canais de transmissão indiscriminada.
- **Independência Neural:** O pipeline temporal é 100% determinístico; **nunca** faz fallback para Decision ou LLM neural para inventar agendamentos ou contornar restrições.

---

## 2. Arquitetura e Contratos

```
+-----------------------------------------------------------------------------------+
| Utterance de Voz (PT-BR)                                                         |
+-----------------------------------------------------------------------------------+
                                         |
                                         v
+-----------------------------------------------------------------------------------+
| Motor Rust (local-nlu v0.6.3 / addon/engine)                                      |
|  - safety.rs: permite apenas formas temporais/exclusões tipadas; bloqueia 'se'    |
|  - slots.rs: clock_time (HH:MM), relative_delay (s), recurrence (daily/weekdays)  |
|  - grammar.rs: parse_schedule_action, parse_clock_alarm, parse_reminder, timers   |
|  - resolver.rs: deduplicação, device_id link, exclusão bulk (expected - excluded) |
|  - contract.rs: Wire Protocol v4 additive (parameters.name, time, duration, etc.) |
+-----------------------------------------------------------------------------------+
                                         |
                                (Plan v4 JSON)
                                         v
+-----------------------------------------------------------------------------------+
| Camada Home Assistant (custom_components/local_nlu)                               |
|  - contextual_protocol.py: validação estrita de tipos e quotas (sem mocks)        |
|  - contextual_runtime.py: revalidação live de catálogo e despacho determinístico   |
|  - scheduled_jobs.py: Store persistente (local_nlu_scheduled_jobs), heap monotônico|
|  - scheduled_delivery.py: TTS voice announcement + persistent_notification        |
|  - scheduled_execution.py: revalidação preflight e execução da ação agendada       |
+-----------------------------------------------------------------------------------+
```

---

## 3. Matriz de Cobertura e Frases Aprovadas

| Frase Testada | Ação Planejada (`action`) | Alvos / Parâmetros Chave | Resultado |
|---|---|---|---|
| *"Liga a luz depois de dez minutos"* | `schedule_action` | `targets: ["light"]`, `delay: 600.0`, `target_action: "turn_on"` | **Aprovado** (`plan`) |
| *"Liga o ventilador às 18 horas"* | `schedule_action` | `targets: ["fan"]`, `time: "18:00"`, `target_action: "turn_on"` | **Aprovado** (`plan`) |
| *"Liga o ventilador daqui 10 minutos"* | `schedule_action` | `targets: ["fan"]`, `delay: 600.0`, `target_action: "turn_on"` | **Aprovado** (`plan`) |
| *"Liga o ventilador às 18:00"* | `schedule_action` | `targets: ["fan"]`, `time: "18:00"`, `target_action: "turn_on"` | **Aprovado** (`plan`) |
| *"Desliga tudo, menos a TV"* | `turn_off` | `targets: [luz, fan, ar]`, `excluded_targets: ["tv"]` | **Aprovado** (`plan`) |
| *"Coloca um timer de dez minutos"* | `timer_start` | `duration: 600.0`, `targets: ["timer"]` (ou virtual se s/ HA) | **Aprovado** (`plan`) |
| *"Marca um timer para pizza de 15 minutos"*| `timer_start` | `name: "pizza"`, `duration: 900.0` | **Aprovado** (`plan`) |
| *"Quanto falta no timer da pizza"* | `timer_query` | `name: "pizza"` | **Aprovado** (`plan`) |
| *"Acrescenta 5 minutos ao timer do arroz"*| `timer_change` | `name: "arroz"`, `relative: 300.0` | **Aprovado** (`plan`) |
| *"Define um despertador para as 07:00"* | `clock_alarm_create` | `time: "07:00"` | **Aprovado** (`plan`) |
| *"Quais despertadores eu tenho"* | `clock_alarm_list` | `targets: []` | **Aprovado** (`plan`) |
| *"Soneca"* / *"Adia o despertador"* | `clock_alarm_snooze`| `duration: 600.0` | **Aprovado** (`plan`) |
| *"Cria um lembrete para comprar pão às 8h"*| `reminder_create` | `title: "comprar pao"`, `time: "08:00"` | **Aprovado** (`plan`) |
| *"Quais lembretes eu tenho"* | `reminder_list` | `targets: []` | **Aprovado** (`plan`) |
| *"Passa o aspirador na sala"* | `vacuum_area` / `vacuum_start` | `area: "sala"`, `domain: "vacuum"` | **Aprovado** (`plan`) |
| *"Manda o robô aspirador para a base"* | `vacuum_dock` | `domain: "vacuum"` | **Aprovado** (`plan`) |
| *"Pausa a limpeza"* | `vacuum_pause` | `domain: "vacuum"` | **Aprovado** (`plan`) |
| *"Se estiver quente, liga o ar"* | N/A | Bloqueio de condição contínua | **Recusado** (`semantic_condition`) |
| *"Não ligue a luz da sala"* | N/A | Bloqueio de negação imperativa | **Recusado** (`semantic_negation`) |
| *"Liga o ventilador quando eu chegar"* | N/A | Bloqueio de gatilho geofence | **Recusado** (`semantic_condition`) |

---

## 4. Persistência e Recuperação contra Falhas

1. **Store JSON Monotônico:** A fila de agendamentos é gravada em `homeassistant.helpers.storage.Store` com versionamento (`local_nlu_scheduled_jobs`).
2. **Quota Estrita:** Limite rígido de `MAX_ACTIVE_JOBS = 50` para evitar exaustão de recursos.
3. **Comportamento em Reinicialização:**
   - Na inicialização (`async_load`), se o HA esteve offline durante a expiração do job, ele **não dispara atrasado de forma destrutiva**; é honestamente marcado como `missed`.
   - Se possuir recorrência ativa (`daily`, `weekdays`), calcula a próxima ocorrência e agenda o próximo ciclo automaticamente.
4. **Entrega Confiável:**
   - Todo alarme/lembrete/timer concluído gera um `persistent_notification.create` imediato (à prova de falha).
   - Se configurado reprodutor de áudio (`delivery_media_player`), dispara anúncio por voz via `tts.speak` / `tts.google_translate_say`.

---

## 5. Resultados de Validação

- **Testes Unitários e de Integração Rust:**
  - `cargo test`: **103 testes aprovados, 0 falhas**.
  - `tests/contextual.rs`: **19 testes aprovados, 0 falhas**.
- **Testes Unitários e de Integração Python (pytest):**
  - `pytest tests/`: **247 testes aprovados, 19 ignorados/skipped, 0 falhas**.
  - Suítes dedicadas da Fase 3.2:
    - `tests/test_phase32_temporal.py`: 7 testes aprovados.
    - `tests/test_phase32_scheduler.py`: 6 testes aprovados.
    - `tests/test_phase32_exclusions.py`: 2 testes aprovados.
    - `tests/test_phase32_vacuum_lexicon.py`: 1 teste aprovado.
    - `tests/test_phase32_notifications.py`: 3 testes aprovados.
- **Isolamento de Branches:**
  - Nenhuma alteração efetuada nos arquivos de titularidade do Codex (`arandu_decision/**`, `arandu_stack/app/{modules.py,settings.py,server.py}`, `custom_components/local_nlu/{ai_lab.py, ai_lab_core.py, ai_lab_panel.js}`).
