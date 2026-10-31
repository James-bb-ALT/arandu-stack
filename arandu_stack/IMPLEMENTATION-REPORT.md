# Relatório de implementação — camadas Decision/LLM e AI Lab

Entrega app 1.0.0 / HACS 2.0.0: redesign dos dois painéis, identidade e ícones SVG,
temas, fluxo condicional e organização do workspace. Perfil ReSpeaker sem BLE,
buffers HTTP persistentes e diagnóstico de RAM; configuração validada/C++ gerado
com ESPHome 2026.10.1, sem linkedição/upload ou teste no hardware. Frontend verificado
em Chromium com fixtures identificadas e chamadas controladas, sem HA real.
137 testes Python aprovados, 1 ignorado; 6 testes JavaScript aprovados.
Relatório, capturas e limitações em `docs/design/README.md` na raiz do repositório.

Entrega app 0.7.1 / HACS 1.3.1 / LLM 0.2.3: resumo NLU limitado no último fallback, pistas Decision compactas e orientação para discordar preservando a fala original. 137 testes Python aprovados, 1 ignorado. Sem medição de inferência real nesta alteração.

Implementado no repositório `arandu-stack`, branch `main`, inicialmente limpa.
Implementação validada localmente; a publicação está registrada no histórico Git.
Nenhuma instalação no HA ou alteração de uma pipeline real foi feita nesta sessão.

## Entrega

App 0.7.0 / HACS 1.3.0 / Decision 0.3.0: ação inferida pelo Decision aceito é
executável sem confirmação verbal, com confiança conjunta calibrada >= 80%, margem
suficiente e alvo inequívoco. A integração revalida aceitação, alvo e permissões;
negação, condição e ação literal conflitante não são sobrepostas. Texto original
preservado; diagnóstico registra confiança e inferência do verbo. LLM permanece
com confirmação para hipóteses incertas. Sem calibração válida não existe uma
porcentagem de confiança utilizável; nenhuma calibração real foi fabricada.
Validação: 135 testes Python passaram, 1 ignorado; 6 testes JavaScript passaram.
Não houve teste/calibração no HA do usuário. NLU e LLM mantêm versões anteriores.

App 0.6.0 / HACS 1.2.0 / NLU 0.6.0: famílias de tipos físicos reutilizadas na
gramática e no índice e escolha de backend de energia por capacidade real.
Cobertura inclui switch IR reconhecido por ID, media_player homônimo somente
de leitura, aliases, cômodo de origem/área explícita e referências incidentais.
Registros residenciais, entidades e transcrições do usuário não foram copiados.

TTS do ReSpeaker: os logs fornecidos identificam falha de alocação da thread
md_reader no firmware após a entrega da URL. O guia ESPHOME-AUDIO.md e o fragmento
examples/esphome/respeaker-audio-memory.yaml preparam stacks compatíveis em PSRAM,
sem alterar rede, pinos, formato de áudio ou duplicar reprodução pelo agente.
É um ajuste para mesclar ao firmware existente, não uma imagem instalada.

Validação desta entrega: 84 testes Rust selecionados passaram, Clippy sem warnings
e formatação válida; 133 testes Python passaram, 1 ignorado; 6 testes JS passaram.
O comando de testes Rust completo também foi tentado e não passou por ausência
dos corpus/snapshots congelados antigos no pacote importado. Esses dados não foram
inventados nem regenerados a partir de saídas do produto. O fragmento ESPHome foi
analisado como YAML e conferido com as opções oficiais; o firmware completo e o
funcionamento físico continuam pendentes de compilação/instalação no aparelho.

HACS 1.1.0: menu nativo para as seções de configuração, listas de associações de
áudio e identidade e cadastro de várias associações antes de concluir. Finalização
desmarcada por padrão; envio vazio conclui e salva a sessão. Correspondência entre
satélite/dispositivo usa o vínculo do registro, sem perder autorização ou adivinhar
saídas conflitantes. Cadastro e execução distinguem exposição, disponibilidade,
capacidade e permissões. Validação: 133 testes Python passaram, 1 ignorado.
Sem reprodução do estado real ou validação visual no HA do usuário. App e modelos
mantêm versões anteriores, pois a alteração está na integração HACS.

HACS 1.0.0: controles locais de volume do satélite (escala 0–10 ou porcentagem),
associação explícita de saída entre integrações e cancelamento autenticado que
interrompe processamento/descarta a fila por usuário e origem. Parada de reprodução
via media_stop/media_pause, quando expostos pela saída; sem novo TTS após cancelar.
Não altera firmware nem garante escuta durante reprodução em todos os dispositivos.
App 0.5.2 e serviços 0.2.2 preservados. Validação: 126 testes Python passaram,
1 ignorado; 6 testes JavaScript passaram. Sem teste físico em Fully Kiosk,
Echo Muse ou ReSpeaker. Novas funcionalidades classificadas em X.0.0 conforme usuário.

Atualização app 0.5.2 / HACS 0.8.2 / Decision e LLM 0.2.2: inferências serializadas
entre processos, duas threads por modelo por padrão, espera passiva e modelos
reutilizados em memória. Prompt do sistema reduzido de 983 para 671 caracteres;
contexto sem dumps e diagnósticos redundantes. Roteamento de produção preservado.
Validação: 108 testes Python passaram, 1 ignorado; 4 testes JavaScript passaram.
Sem benchmark no i5-6500T ou build de imagem. Verificação Linux pendente por falha
do disco WSL; exclusão entre processos verificada no Windows.

Atualização app 0.5.1 / HACS 0.8.1: resolução diferencia a referência principal
ao aparelho de menções em sensores associados e verifica a ação compatível.
Ambiguidades reais listam os candidatos. O Lab ganhou seleção explícita da área
de teste, compartilhada pelas três camadas e separada da política de produção.
Validação: **193 testes Python, 4 testes JS e 27 subtestes passaram**; 68 testes
com Rust real continuam dependentes do ambiente. O catálogo real do usuário e
as inferências com seus pesos não foram acessados nesta sessão.

Atualização app 0.5.0 / HACS 0.8.0: Decision agora publica diagnósticos separados
de ação, dispositivo e área; o LLM recebe essa análise quando necessário.
Hipóteses incertas são confirmadas na conversa, vinculadas à sessão e ao catálogo,
antes de qualquer execução. A análise não muda o score calibrado do Decision.
Clientes antigos recebem a hipótese marcada como esclarecimento, sem execução.

Validação desta atualização: **188 testes Python, 3 testes JS e 27 subtestes
passaram**. Outros 68 testes exigem o binário Rust real. As seções abaixo registram
a entrega e a validação iniciais; o guia e o CHANGELOG descrevem o comportamento atual.

- Integração HACS 0.7.0: opções booleanas independentes, desligadas por padrão, na etapa nativa **Camadas de inteligência**.
- App 0.4.0 e serviços internos Decision/LLM 0.1.1.
- NLU primeiro; fallback somente para falhas semânticas permitidas, com executor e confirmações HA preservados.
- LLM-only usa recuperação própria; produção exige evidência independente de dispositivo único.
- AI Lab com resumo, ação proposta, nomes/áreas, hipóteses rejeitadas, diagnósticos expansíveis, política real consultável e erros TTS separados.
- Correções de contrato, resposta HTTP fragmentada, síntese TTS no app único e reutilização de um servidor LLM.
- README, DOCS, CHANGELOG e traduções atualizados.

[Guia de ativação, arquitetura, segurança, erros encontrados e matriz manual](INTELLIGENCE.md).

## Validação executada

Ambiente isolado local Python 3.13, aiohttp 3.13.3, pytest, probatio e tzdata;
Node 24.19.0. Nenhum peso de modelo foi baixado ou carregado.

| Conjunto | Resultado |
|---|---|
| `pytest tests -q` | 68 passaram; 4 subtestes passaram |
| AI Lab + contextual + identity + capabilities + spatial | 78 passaram; 68 ignorados; 18 subtestes passaram |
| Contratos do serviço Decision | 5 passaram |
| Contratos do serviço LLM | 5 passaram |
| `node --test tests/test_ai_lab_panel.js` | 2 passaram |
| `node --check custom_components/local_nlu/ai_lab_panel.js` | passou |
| `compileall` da integração, controlador e serviços alterados | passou |
| Manifest, strings e traduções JSON | 4 arquivos válidos |
| `git diff --check` | passou; Git avisou apenas sobre normalização CRLF/LF |

Total: **156 testes Python + 2 testes JS**, além de 22 subtestes.
Os 68 testes ignorados exigem o binário Rust real (`ARANDU_NLU_BINARY`).

O comando `cargo` não está no PATH. O shim encontrado em
`C:\Users\victo\.cargo\bin\cargo.exe` falhou porque o rustup não tem
toolchain padrão configurada. Não existe binário Rust compilado nos caminhos
`target/release` deste snapshot. O gate `tools/mlp-check` citado nas
instruções do snapshot não existe aqui. Esses gates **não foram declarados aprovados**.

As regressões antigas com fakes de HA foram alinhadas ao caminho atual de
preparação do Stack e à compatibilidade por protocolo. O teste de processo usa
mock portátil para `os.killpg`, ausente no Windows, mantendo as verificações
de ambiente sem credenciais e ownership.

## Cobertura e revisões

A matriz cobre as quatro combinações, NLU resolvido sem modelos, Decision aceito
sem LLM, rejeição/indisponibilidade com fallback permitido, ausência de usuário,
permissão negada, exposição revogada, alvo inexistente/indisponível, ação inválida,
empate de nomes, mudança de catálogo/política durante inferência, timeout,
cancelamento e execução duplicada.

Os testes HTTP usam serviço LLM real com adaptador fake e recuperação real;
nenhum processo Decision é necessário. O teste de lifecycle verifica duas
inferências com apenas um subprocesso e descarregamento explícito. O teste do
gateway cobre WAV, cabeçalho obrigatório e erro upstream sem repasse de corpo
sensível. Os testes de painel distinguem ranking, hipótese, autorização,
simulação e falha de áudio.

Foram feitas duas revisões locais: correção/fail-closed e fronteiras de
segurança/privacidade. Não houve revisão por agentes independentes.
A revisão identificou e corrigiu propagação de cancelamento, leitura HTTP parcial,
mensagem TTS genérica e indicação de instalação desconhecida como ausência.

## Limitações e pendências de ambiente

- Instalação/atualização real no HACS, persistência no HA e mudança de toggles no frontend precisam de verificação HAOS.
- Assist com voz, Supervisor/Wyoming, áudio e renderização desktop/mobile não foram exercitados numa instalação HAOS.
- As redes neurais reais não foram executadas. Os tempos observados de 8.834,2 ms e 11.899,9 ms não foram reproduzidos nem atribuídos a uma causa sem evidência.
- Fallback LLM de produção é conservador: um alvo explícito e inequívoco. Negação, condição, múltiplos alvos e evidência insuficiente pedem reformulação. O Lab mantém propostas para revisão.
- O timeout de produção é 30 s por camada. Uma carga fria pode excedê-lo; não há execução tardia. A inferência interna pode terminar sob lock, sem acesso ao executor HA.

## Arquivos alterados/adicionados

- `README.md`
- `arandu_stack/CHANGELOG.md`
- `arandu_stack/DOCS.md`
- `arandu_stack/app/module_worker.py`
- `arandu_stack/app/server.py`
- `arandu_stack/config.yaml`
- `arandu_stack/rootfs/modules/arandu_decision/config.yaml`
- `arandu_stack/rootfs/modules/arandu_decision/rootfs/src/pyproject.toml`
- `arandu_stack/rootfs/modules/arandu_decision/rootfs/src/src/arandu_decision/__init__.py`
- `arandu_stack/rootfs/modules/arandu_decision/rootfs/src/src/arandu_decision/adapter.py`
- `arandu_stack/rootfs/modules/arandu_decision/rootfs/src/src/arandu_decision/service.py`
- `arandu_stack/rootfs/modules/arandu_llm/config.yaml`
- `arandu_stack/rootfs/modules/arandu_llm/rootfs/src/pyproject.toml`
- `arandu_stack/rootfs/modules/arandu_llm/rootfs/src/src/arandu_llm/__init__.py`
- `arandu_stack/rootfs/modules/arandu_llm/rootfs/src/src/arandu_llm/adapter.py`
- `arandu_stack/rootfs/modules/arandu_llm/rootfs/src/src/arandu_llm/service.py`
- `arandu_stack/rootfs/modules/arandu_nlu/rootfs/src/tests/mlp/test_ai_lab.py`
- `arandu_stack/rootfs/modules/arandu_nlu/rootfs/src/tests/mlp/test_contextual_capabilities.py`
- `arandu_stack/rootfs/modules/arandu_nlu/rootfs/src/tests/mlp/test_contextual_identity.py`
- `custom_components/local_nlu/__init__.py`
- `custom_components/local_nlu/ai_lab.py`
- `custom_components/local_nlu/ai_lab_core.py`
- `custom_components/local_nlu/ai_lab_panel.js`
- `custom_components/local_nlu/config_flow.py`
- `custom_components/local_nlu/const.py`
- `custom_components/local_nlu/contextual_runtime.py`
- `custom_components/local_nlu/diagnostics.py`
- `custom_components/local_nlu/manifest.json`
- `custom_components/local_nlu/runtime.py`
- `custom_components/local_nlu/strings.json`
- `custom_components/local_nlu/translations/en.json`
- `custom_components/local_nlu/translations/pt-BR.json`
- `tests/test_control.py`
- `tests/test_nlu_discovery_flow.py`
- `arandu_stack/INTELLIGENCE.md`
- `custom_components/local_nlu/intelligence.py`
- `tests/test_ai_lab_panel.js`
- `tests/test_intelligence_routing.py`
- `tests/test_intelligence_services.py`
- `arandu_stack/IMPLEMENTATION-REPORT.md`
