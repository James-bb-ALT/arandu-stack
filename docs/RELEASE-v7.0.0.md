# Arandu 7.0.0 — Decision 2.0

App **6.0.0**, integração HACS **7.0.0**, Decision **2.0.0**. NLU 0.6.3, LLM 1.0.1, STT 4.0.1, TTS e Wake preservados. Nova etapa de release conforme a classificação do projeto.

- Classificação separada de intenção, dispositivo e área; média dos cosines preservada, E5 residente e cache limitado. Ação/alvo explícitos dispensam embeddings; grafia pede confirmação.
- Calibração v3 independente, estratos, Wilson, ECE/Brier e proveniência. Fixtures técnicas não liberam aceitação neural automática. O HA revalida e continua único executor.
- App permite selecionar e reiniciar structured-v3 ou baseline-v2. Política em arquivo separado preserva leitura das configurações pelo app anterior no rollback.
- AI Lab nativo compara os dois algoritmos no mesmo catálogo, sem trocar produção, gerar token ou executar ações; mostra campos, evidências, candidatos, cache, scores e tempos.

**Validação real:** 244 testes Python e 181 subtestes, 88 Rust disponíveis, dez contratos e dez testes JavaScript passaram. Três testes STT opt-in foram ignorados. Build Docker, API, E5 e Tucano reais passaram. Foram testados salvar/reiniciar ambos os algoritmos, concorrência com recusa de ocupação e Rust → Decision → HA simulado, sem LLM para a paráfrase fundamentada.

**A/B técnico com E5:** 23/29 → 29/29 em acerto conjunto/abstenção correta; ranking positivo 10/15 → 15/15; nenhuma ativação indevida nas fixtures. p95 warm pequeno 6,44 → 4,04 ms; ranking com 256 entidades 24,03 → 18,58 ms (30 amostras warm). Medição no Ryzen 7 5700X/Docker; não é avaliação de fala humana nem garantia produtiva. Não foi demonstrada redução de RAM.

**Limites:** sem corpus representativo, a calibração de produção não está aprovada e seu gate neural permanece fechado. Calibração real foi exercitada com 50/30 fixtures e manteve `validated=false`. HA físico/HAOS/satélite não validado: probe local retornou 403 sem sessão administrativa. i5-6500T e taxa global de uso do LLM não medidos. As pendências históricas do corpus/gate completo NLU continuam documentadas.

Instale app e HACS desta etapa juntos. baseline-v2 permanece selecionável; rollback de código para v6.0.1 conserva módulos/configurações e artefato de calibração anterior. O ZIP anexo contém somente fontes da integração, sem pesos ou credenciais.

[Relatório técnico e arquivos modificados](https://github.com/James-bb-ALT/arandu-stack/blob/v7.0.0/docs/DECISION-v2.0.0.md) · [Evidência reproduzível](https://github.com/James-bb-ALT/arandu-stack/blob/v7.0.0/docs/DECISION-VALIDATION-2026-11-11.json).
