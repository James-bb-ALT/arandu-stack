# Arandu Assistant 11.0.0 — Fases 5 e 6 Integradas (2026-11-12)

Integração unificada das **Fases 5 (NLU Avançado: Ações Contextuais, Automações e Listas)** e **Fase 6 (Intercom e Anúncios Domésticos Determinísticos)** sobre a base de concorrência e observabilidade da Fase 4.

## 1. Destaques da Release

### Fase 5 — NLU Avançado, Ações Contextuais, Automações e Listas
- **Gramática Contextual em Rust (NLU v0.7.0):** Reconhecimento de troca de antecedente com elipse (*"liga a luz da sala"* → *"agora a da cozinha"*), além de desfazimento (*undo*) com validação e comparação do estado atual.
- **Automações Seguras:** Distinção estrita entre habilitação e execução:
  - *"ativa/desativa automação X"*: altera o estado (`automation.turn_on` / `automation.turn_off`) sem disparar ações.
  - *"executa automação X"*: executa com a flag de segurança `skip_condition: false`, garantindo a avaliação integral das condições do Home Assistant.
- **Gerenciamento de Listas de Afazeres (`todo.*`):** Comandos nativos para adicionar, remover, concluir e atualizar itens com amarração por UID, preservação do contexto do diálogo e confirmação obrigatória de uso único para limpeza de concluídos.
- **Consultas e Histórico Limitados:** Consultas agregadas (soma de energia, duração de estados binários, tendências e picos) com granularidade horária sem sobreposição e revalidação de autoridade após I/O.
- **Defesa de Segurança:** Instruções operacionais citadas no texto de listas ou automações nunca são transformadas em comandos para aparelhos residenciais; zero fallback neural indevido.

### Fase 6 — Intercom e Anúncios Domésticos Determinísticos
- **Fast-Path 100% Determinístico em Python:** Interceptação no agente de conversação (`conversation.py`), antes de qualquer invocação ao runtime ou modelos neurais.
- **Prioridade Nativa de Entrega:**
  1. `assist_satellite.announce` (Prioridade 1): Execução direta em satélites Assist com sinal sonoro (*chime*) opcional e voz nativa do Assist.
  2. `tts.speak` (Prioridade 2 / fallback): Execução em reprodutores de mídia (`media_player.*`) associados via entidade TTS configurada (`tts.*`), com `cache=False`.
- **Proteção Adversarial Absoluta:**
  - Frases citando ordens (*"fala na sala que desliga a luz do quarto"*) entregam estritamente o áudio falado; **zero chamadas** ao domínio `light` ou outros atuadores físicos.
  - Rejeição determinística de planos mistos (*"avisa na cozinha e apaga a luz"*), condicionais contínuas (*"se eu chegar..."*) e negações (*"não avisa..."*).
  - Sanitização rígida de conteúdo (1–280 caracteres, máx. 512 bytes UTF-8; bloqueio de tags HTML/XML/SSML, templates Jinja, URLs e caracteres de controle).
- **Transmissão Geral (*Broadcast*):** Exige opt-in explícito nas opções e confirmação de uso único (*"Quer anunciar esse recado em X dispositivos?"*) com *fingerprint* imutável.
- **Multi-turno, Resposta e Concorrência:**
  - Solicitação de mensagem pendente para ordens sem conteúdo (*"avisa no quarto"* → *"Qual recado você quer enviar?"*).
  - Janela de resposta (*reply window*) de 30 segundos de volta ao satélite remetente (*"responde que já vou"*).
  - Prevenção automática de eco local (satélite remetente é excluído da lista de alvos no broadcast).
  - Limite global de 2 anúncios concorrentes e controle de flood/cooldown por origem.
- **Interface Gráfica Nativa:** Nova seção de opções `announcements` no menu de opções do Home Assistant (`config_flow.py`) com seletores nativos de Área, Satélite Assist, Media Player e TTS.

---

## 2. Versões dos Componentes

- **Arandu Stack App:** `11.0.0`
- **HACS Integration (`local_nlu`):** `11.0.0`
- **NLU Engine (Rust):** `0.7.0`
- **Decision Engine:** `3.0.0` *(preservado)*
- **LLM Engine:** `2.0.0` *(preservado)*
- **STT Engine:** `4.0.2` *(preservado)*
- **TTS Engine:** `0.2.2` *(preservado)*

---

## 3. Validação e Testes

- **Rust Suite:** `cargo fmt --check` aprovado; `cargo clippy --all-targets -- -D warnings` com 0 avisos; `cargo test` com 80+ testes unitários e de integração aprovados.
- **Python Suite:** 378 testes aprovados, 41 pulados (testes de hardware real / opt-in), 181 subtestes aprovados e 0 falhas na suíte geral `pytest tests/`.
- **Fluxos Combinados E2E:** 7 fluxos ponta a ponta aprovados em `tests/test_fases_5_6_combined.py`:
  1. Troca de antecedente contextual sem inferência neural.
  2. Comandos de lista `todo.*` sem interceptação indevida de anúncio.
  3. Comandos de automação com `skip_condition: false` estrito.
  4. Anúncio citando comando com zero efeito colateral doméstico.
  5. Broadcast com bloqueio por padrão e confirmação única quando ativo.
  6. Isolamento entre múltiplos satélites, cancelamento e deduplicação de retentativas.
  7. Processamento ponta a ponta do agente de conversação com zero chamadas neurais.
- **Ressalvas de Teste Físico:** Ensaios executados em ambiente simulado de Home Assistant e harness local. Nenhum som ou comando físico foi emitido na residência durante os testes.

---

## 4. Instruções de Atualização e Rollback

1. **Atualização:**
   - Atualize o app no Supervisor e a integração no HACS em conjunto.
   - Reinicie o Home Assistant para carregar as novas rotas e seletores de opções.
2. **Rollback:**
   - Caso deseje desativar os anúncios imediatamente sem reiniciar o Home Assistant, desmarque o switch **Ativar avisos por voz direcionados** nas opções da integração.
   - Para reverter a instalação para a versão anterior, utilize o backup do Home Assistant anterior à atualização.
