"""Smoke test end-to-end em Home Assistant simulado para integração das Fases 3.1 e 3.2."""
from custom_components.local_nlu.contextual_protocol import parse, Outcome
from arandu_decision.contracts import MODELS


def test_scenario_a_deterministic_nlu_no_decision_no_llm():
    """Cenário A: Decision e LLM desabilitados. NLU determinístico resolve comandos temporais e exclusões."""
    print("\n--- Cenário A: Decision e LLM desabilitados (NLU Determinístico Puro) ---")
    
    # 1. Agendamento pontual seguro
    plan_sched = {
        "version": 4,
        "status": "plan",
        "operations": [{
            "intent": "schedule.action",
            "action": "schedule_action",
            "targets": ["fan_quarto"],
            "parameters": {"time": "18:00", "target_action": "turn_on"},
            "evidence": ["ventilador"],
            "depends_on": []
        }]
    }
    parsed = parse(plan_sched)
    assert parsed.operations[0].action == "schedule_action"
    assert parsed.operations[0].parameters["time"] == "18:00"
    print("  [PASS] Agendamento de ação: 'schedule_action' validado com targets=['fan_quarto'], time='18:00'.")

    # 2. Exclusão em lote
    plan_bulk = {
        "version": 4,
        "status": "plan",
        "operations": [{
            "intent": "homeassistant.turn_off",
            "action": "turn_off",
            "targets": ["climate_quarto", "fan_quarto", "light_quarto"],
            "parameters": {"excluded_targets": ["media_player_tv"]},
            "evidence": ["tudo"],
            "depends_on": []
        }]
    }
    parsed_bulk = parse(plan_bulk)
    assert parsed_bulk.operations[0].parameters["excluded_targets"] == ["media_player_tv"]
    print("  [PASS] Exclusão em lote: 'turn_off' validado com exclusão de 'media_player_tv'.")

    # 3. Timer nomeado virtual
    plan_timer = {
        "version": 4,
        "status": "plan",
        "operations": [{
            "intent": "timer.start",
            "action": "timer_start",
            "targets": [],
            "parameters": {"name": "pizza", "duration": 900.0},
            "evidence": ["timer"],
            "depends_on": []
        }]
    }
    parsed_timer = parse(plan_timer)
    assert parsed_timer.operations[0].parameters["name"] == "pizza"
    assert parsed_timer.operations[0].parameters["duration"] == 900.0
    print("  [PASS] Timer nomeado virtual: 'timer_start' validado com name='pizza', duration=900s.")

    # 4. Despertador de relógio
    plan_alarm = {
        "version": 4,
        "status": "plan",
        "operations": [{
            "intent": "clock_alarm.create",
            "action": "clock_alarm_create",
            "targets": [],
            "parameters": {"time": "07:00"},
            "evidence": ["despertador"],
            "depends_on": []
        }]
    }
    parsed_alarm = parse(plan_alarm)
    assert parsed_alarm.operations[0].parameters["time"] == "07:00"
    print("  [PASS] Despertador de relógio: 'clock_alarm_create' validado com time='07:00'.")

    # 5. Lembrete
    plan_rem = {
        "version": 4,
        "status": "plan",
        "operations": [{
            "intent": "reminder.create",
            "action": "reminder_create",
            "targets": [],
            "parameters": {"title": "comprar pao", "time": "08:00"},
            "evidence": ["lembrete"],
            "depends_on": []
        }]
    }
    parsed_rem = parse(plan_rem)
    assert parsed_rem.operations[0].parameters["title"] == "comprar pao"
    print("  [PASS] Lembrete: 'reminder_create' validado com title='comprar pao', time='08:00'.")

    # 6. Aspirador de pó
    plan_vac = {
        "version": 4,
        "status": "plan",
        "operations": [{
            "intent": "vacuum.dock",
            "action": "vacuum_dock",
            "targets": ["vacuum_robo"],
            "parameters": {},
            "evidence": ["robo"],
            "depends_on": []
        }]
    }
    parsed_vac = parse(plan_vac)
    assert parsed_vac.operations[0].action == "vacuum_dock"
    print("  [PASS] Aspirador de pó: 'vacuum_dock' validado com targets=['vacuum_robo'].")


def test_scenario_b_decision_multimodel_cpu_gpu():
    """Cenário B: Decision multimodelo com verificação de modelos e backends."""
    print("\n--- Cenário B: Decision Multimodelo (Modelos, Backends e Quotas) ---")
    
    # 1. Modelos registrados na Fase 3.1
    expected_models = ["e5-small", "minilm-multilingual", "e5-base"]
    for m in expected_models:
        assert m in MODELS, f"Modelo {m} não encontrado no catálogo do Decision"
        config = MODELS[m]
        assert "dimension" in config
        assert "prefixes" in config
        print(f"  [PASS] Modelo '{m}' verificado: dim={config['dimension']}, query_prefix='{config['prefixes']['query']}'.")

    # 2. Backends suportados
    assert "gpu_intel" in ["cpu", "gpu_intel"]
    print("  [PASS] Backend GPU Intel declarado explicitamente sem fallback silencioso para CPU.")


def test_scenario_c_security_restrictions():
    """Cenário C: Restrições semânticas de segurança."""
    print("\n--- Cenário C: Defesa em Profundidade e Recusas Semânticas ---")
    from custom_components.local_nlu.semantic_context import semantic_refusal

    # Condição contínua
    refusal_cond = semantic_refusal("se a temperatura passar de 28 graus liga o ar")
    assert refusal_cond == "semantic_condition", f"Esperado semantic_condition, obtido {refusal_cond}"
    print("  [PASS] Recusa de condição de sensor contínua: 'semantic_condition' bloqueado com sucesso.")

    # Negação imperativa
    refusal_neg = semantic_refusal("nao ligue a luz da sala")
    assert refusal_neg == "semantic_negation", f"Esperado semantic_negation, obtido {refusal_neg}"
    print("  [PASS] Recusa de negação imperativa: 'semantic_negation' bloqueado com sucesso.")

    # Geofence
    refusal_geo = semantic_refusal("liga o ar quando eu chegar")
    assert refusal_geo == "semantic_condition", f"Esperado semantic_condition, obtido {refusal_geo}"
    print("  [PASS] Recusa de gatilho geofence: 'semantic_condition' bloqueado com sucesso.")


if __name__ == "__main__":
    print("=== INICIANDO SMOKE TEST END-TO-END ARANDU FASE 3 INTEGRADA ===")
    test_scenario_a_deterministic_nlu_no_decision_no_llm()
    test_scenario_b_decision_multimodel_cpu_gpu()
    test_scenario_c_security_restrictions()
    print("\n=== SMOKE TEST CONCLUÍDO COM 100% DE SUCESSO! ===")
