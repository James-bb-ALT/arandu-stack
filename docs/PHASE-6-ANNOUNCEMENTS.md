# Arandu Stack — Fase 6: Anúncios Domésticos e Intercom Determinístico

## 1. Visão Geral

A Fase 6 implementa o sistema de **anúncios domésticos unidirecionais (Intercom)** orientados a áreas e satélites para o Arandu Stack no Home Assistant.

### Características Centrais
- **100% Determinístico em Python:** Processamento em *fast-path* no agente de conversação (`conversation.py`), antes de qualquer invocação ao `LocalNluRuntime` ou aos motores neurais.
- **Zero Inferência Neural:** Frases reconhecidas como anúncios ou tentativas de anúncio nunca invocam o Decision Engine ou LLMs, prevenindo alucinações e comandos domésticos indevidos decorrentes do texto falado.
- **Prioridade Nativa de Entrega:**
  1. `assist_satellite.announce` (prioridade 1): Execução direta em satélites Assist com sinal sonoro (*chime*) opcional e voz nativa do Assist.
  2. `tts.speak` (prioridade 2 / fallback): Execução via reprodutor de mídia (`media_player.*`) associado com entidade TTS configurada (`tts.*`), sem armazenamento de áudio em cache (`cache=False`).
- **Segurança Estrita:** Não realiza streaming de áudio ao vivo, não captura microfones remotos e não cria canais bidirecionais contínuos.

---

## 2. Arquitetura e Contrato de Interceptação

### Localização no Fluxo de Conversação (`custom_components/local_nlu/conversation.py`)
A interceptação ocorre imediatamente após a etapa de preparação (`prepare`) e antes do runtime contextual:

```python
prepared, problem = await observe('prepare', prepare(self.hass, self._entry, user_input))
if problem is not None:
    result = problem
else:
    announcement_result = await async_maybe_handle_announcement(
        hass=self.hass,
        config_entry=self._entry,
        user_input=prepared,
    )
    result = (
        announcement_result
        if announcement_result is not None
        else await self._runtime.async_process(prepared)
    )
trace.finish(result, user_input.text, trace.include_text)
```

### Contrato de Retorno (`async_maybe_handle_announcement`)
- Retorna `None`: Apenas quando a frase **não** é uma intenção de anúncio (ex.: "me fala a temperatura", "fala comigo", "apaga a luz da sala"). O pedido segue o fluxo normal do NLU.
- Retorna `RuntimeResult`: Em qualquer frase reconhecida como anúncio, inclusive em casos de erro, destino ausente, recurso desligado, negação, condicional ou falha de entrega. Isso impede vazamento do texto do anúncio para camadas neurais.

---

## 3. Módulos Implementados

| Módulo | Responsabilidade |
|---|---|
| `custom_components/local_nlu/announcement_parser.py` | Parser determinístico puro sem dependência de HA. Extrai AST `AnnounceIntent` preservando pontuação, acentuação e caixa original da mensagem. |
| `custom_components/local_nlu/announcement_options.py` | Dataclass `AnnouncementOptions` com tipagem estrita, limites de segurança, suporte a horário silencioso (*quiet hours*) e saneamento de destinos. |
| `custom_components/local_nlu/announcement_delivery.py` | Mecanismo de resolução de destinos, verificação de exposição ao Assist, autorizações de leitura/controle do usuário, e execução de `assist_satellite.announce` / `tts.speak`. |
| `custom_components/local_nlu/announcement_sessions.py` | Gerenciamento de sessões em memória para diálogo multi-turno (solicitação de mensagem e confirmação de transmissão para toda a casa), janela de resposta (*reply*), deduplicação e limite de concorrência global (máx. 2). |
| `custom_components/local_nlu/announcement_router.py` | Orquestrador principal que integra parser, opções, identidade, sessões e entrega. |
| `custom_components/local_nlu/config_flow.py` | Etapa nativa de opções `announcements` no menu do Home Assistant para associar áreas a satélites ou media players. |

---

## 4. Léxico e Gramática Suportados (PT-BR)

### Verbos e Moldes Principais
- **Área específica com mensagem:**
  - *"avisa na cozinha que o almoço está pronto"* → Destino: `cozinha`, Mensagem: `"o almoço está pronto"`.
  - *"fala no quarto que já pode vir"* → Destino: `quarto`, Mensagem: `"já pode vir"`.
  - *"anuncia na sala: a reunião começou"* → Destino: `sala`, Mensagem: `"a reunião começou"`.
  - *"manda um recado pro escritório dizendo que estou chegando"* → Destino: `escritório`, Mensagem: `"estou chegando"`.
- **Satélite ou alto-falante explícito:**
  - *"diz no alto-falante da cozinha que acabou o café"* → Destino: `alto-falante da cozinha`.
  - *"manda uma mensagem para o satélite do quarto que terminou"* → Destino: `satélite do quarto`.
- **Transmissão para toda a casa (*Broadcast*):**
  - *"avisa na casa toda que vamos sair"* / *"anuncia em todos os cômodos que chegou a encomenda"*
  - Exige opt-in na configuração (`announcements_allow_all = True`) e confirmação de uso único (*"Quer anunciar esse recado em X dispositivos?"*).
- **Diálogo Multi-turno (Destino sem mensagem):**
  - Usuário: *"avisa na cozinha"*
  - Assistente: *"Qual recado você quer enviar?"*
  - Usuário: *"O almoço está pronto"* → Anúncio enviado para a cozinha.
- **Resposta direcionada (*Reply Window*):**
  - Satélite que recebeu anúncio recentemente (janela de 30s): *"responde que já vou"*
  - Entrega o recado diretamente de volta ao satélite remetente, sem broadcast.

---

## 5. Proteções Adversariais e Segurança

1. **Comandos domésticos citados dentro do anúncio:**
   - *"fala na sala que desliga a luz da cozinha"* → O texto falado é `"desliga a luz da cozinha"`; **zero** chamada ao serviço `light.turn_off`.
   - *"avisa no quarto que destranca a porta"* → Mensagem de áudio pura; **zero** chamada ao serviço `lock.unlock`.
2. **Negações e Condicionais:**
   - *"não avisa na cozinha que cheguei"* → Rejeitado como negação; zero envio de áudio e zero ação.
   - *"se eu chegar, avisa na cozinha"* → Rejeitado como condicional contínua; zero envio de áudio.
3. **Planos Mistos:**
   - *"avisa na cozinha e apaga a luz"* → Rejeitado deterministamente para evitar execução parcial.
4. **Filtros de Injeção e Limites de Conteúdo:**
   - Comprimento da mensagem limitado entre 1 e 280 caracteres (máx. 512 bytes UTF-8).
   - Bloqueio de tags HTML/XML/SSML (`<speak>`, `<script>`, etc.).
   - Bloqueio de templates Jinja (`{{ ... }}`, `{% ... %}`).
   - Bloqueio de URLs executáveis (`http://`, `https://`).
   - Rejeição de caracteres de controle ASCII (< 32 exceto tabulação/nova linha).
5. **Permissões e Exposição ao Assist:**
   - Entidades de destino precisam estar explicitamente expostas ao Assist (`exposed_entities`).
   - O usuário autenticado da requisição precisa ter permissões de `read` e `control` no Home Assistant para as entidades de destino. Usuários administradores não realizam bypass da exigência de exposição.
6. **Prevenção de Eco Local:**
   - Em transmissões para toda a casa, o satélite de origem da fala é automaticamente desconsiderado da lista de alvos para evitar eco imediato sobre o locutor.

---

## 6. Configuração no Home Assistant

1. No Home Assistant, acesse **Configurações** → **Dispositivos e Serviços** → **Arandu Assistant** → **Configurar**.
2. No menu principal, selecione **Avisos por voz (Intercom) — áreas e satélites**.
3. Configure os parâmetros:
   - **Ativar avisos por voz direcionados:** Habilita o recurso.
   - **Permitir avisos para toda a casa:** Habilita o broadcast com confirmação.
   - **Tocar sinal sonoro antes do anúncio:** Emite chime antes de falar nos satélites.
   - **Entidade TTS para media players:** Entidade TTS para saída em reprodutores de mídia convencionais (ex.: `tts.piper`).
   - **Intervalo mínimo (Cooldown):** Tempo mínimo entre avisos da mesma origem (padrão: 5 segundos).
   - **Horário silencioso (Opcional):** Início e fim (formato `HH:MM`).
   - **Associações de Área:** Selecione a área e o dispositivo correspondente (`assist_satellite.*` ou `media_player.*`).
4. Clique em **Salvar e concluir**.

---

## 7. Instruções de Rollback

Caso seja necessário desativar ou reverter a funcionalidade:
1. **Desativação imediata sem reinicialização:**
   - Abra as opções da integração e desmarque o switch **Ativar avisos por voz direcionados**.
   - Todas as frases de anúncio retornarão recusa determinística imediata sem afetar o processamento normal do NLU.
2. **Reversão de Código Git:**
   - Caso queira reverter a branch da Fase 6:
     ```bash
     git checkout feat/arandu-fase-6-anuncios
     git revert HEAD
     ```
   - O ponto de corte em `conversation.py` voltará a chamar diretamente `_runtime.async_process`.
