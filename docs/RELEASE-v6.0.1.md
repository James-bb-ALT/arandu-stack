# Arandu 6.0.1 — Fase 1 e NLU Hardening

App **5.0.1**, HACS **6.0.1**, NLU **0.6.3**, Decision/LLM **1.0.1**.
STT **4.0.1**, TTS e modelos preservados. Incremento Minor (`x.y.Z`) conforme
a classificação do projeto; NLU alinhado acima da cópia distribuída 0.6.2.

- Restrições operacionais recusam o plano inteiro em Rust, fallback, serviços
  isolados e executor HA. Propostas não podem ignorar negação, exclusão ou condição.
- Perguntas e conversa sem efeitos são vinculadas ao tipo real das operações.
  “Piada sem palavrões” continua sendo conversa; não autoriza automação.
- Formas naturais adicionais, sequência `e depois` e pronomes com único alvo atual.
  Mudanças de origem/catálogo invalidam referências recentes.
- Preservados: permissões, exposição, pré-validação, confirmação de uso único,
  cancelamento, IR/bindings, Music Assistant, seleção de STT e portas Wyoming.

Evidências e resultados efetivamente executados:
[relatório técnico](NLU-HARDENING-PHASE1.md) e
[validação](NLU-PHASE1-VALIDATION-2026-11-11.json).

Limites: HA físico sem sessão autenticada; suíte completa do corpus NLU depende
de gold ausente no snapshot; gate da antiga distribuição independente tem
artefatos ausentes/divergentes. Não há afirmação de segurança universal,
acurácia de fala humana ou desempenho no i5-6500T. Nenhum peso é publicado.
