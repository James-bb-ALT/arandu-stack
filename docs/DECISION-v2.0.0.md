# Decision 2.0 — app 6.0.0 / HACS 7.0.0

## Diagnóstico e escopo

A implementação parte de b1063da (Fase 1 integrada), não da baseline antiga indicada no briefing. A versão anterior já tinha média dos cosines, fast path explícito, grafia com confirmação, E5 residente, cache e batching. Esses mecanismos foram preservados. O gap era o ranking com descritores misturando ação/aparelho, poucas construções explícitas e calibração vinculada ao catálogo completo inclusive valores dinâmicos. O helper `decision_policy` histórico continua para diagnóstico/compatibilidade; ele não é o caminho de aceitação structured-v3.

Não há alteração de pesos, dependências, STT, TTS, Wake, Rust NLU ou geração LLM. A conversa geral implementada anteriormente permanece.

## Contrato e algoritmo

`structured-v3`, contrato versão 2, devolve `analysis.action`, `analysis.device`, `analysis.area`, evidências, candidatos/excluídos limitados no diagnóstico, contagens completas, fingerprints e métricas. Todos os candidatos autorizados são visitados; top 8 é somente apresentação. A média continua `(intent_score + target_score) / 2`. São duas consultas separadas em um lote; descritores de aparelho não contêm verbo ou área. A área filtra candidatos, não acrescenta uma probabilidade arbitrária.

A evidência explícita usa verbo ou construções fechadas, nomes/aliases/categoria, ações reais e localização. Área explícita prevalece sobre origem; “de lá” requer histórico autorizado. Área desconhecida, indisponibilidade, ambiguidade, comandos compostos/restritos e parâmetros avançados não suportados não viram liga/desliga. Switch/remote/relé só controla TV/ar/ventilador/luz quando nome/metadado autorizado e capacidade real sustentam a ação. Preferência de controle só desempata entidades do mesmo device_id, nunca aparelhos independentes com nome igual.

Ação e alvo inequívocos são resolvidos sem E5 e sem inventar confiança. Grafia de ação ou nome exige confirmação. Hipótese semântica sem calibração permanece diagnóstica ou pede confirmação; ranking não é autorização. O HA recompõe os campos independentemente, verifica catálogo, origem, permissões, disponibilidade e capacidades e continua único executor. Tokens, expiração, cancelamento, replay e revogação existentes são preservados. Ambiguidade real de entidade evita chamar o LLM inutilmente; quando há fallback, o LLM faz sua própria recuperação no catálogo completo, sem herdar o top 8 como universo.

## Calibração

Arquivo independente `e5-small-structured-v3-calibration.json`; o arquivo v2 permanece intacto. Há no mínimo 50 exemplos de treino, 30 de validação, máximo 500 e unicidade após normalização. Estratos separados para intenção explícita/semântica. Cada faixa aceita exige ao menos 30 exemplos em cada split. Confiança é o mínimo dos limites inferiores de Wilson 95% dos dois splits, nunca cosine renomeado. Mínimos automáticos: confiança 0,8, margem 0,08.

ECE/Brier medem frequências empíricas por faixa; também são informadas acurácia, cobertura e falsas aceitações. Validação exige proveniência representativa declarada, fonte/licença livre, cobertura mínima, ECE ≤0,1 e nenhuma falsa aceitação no conjunto coberto. A declaração de representatividade precisa de revisão humana: o software não consegue provar que uma amostra é representativa. Fixtures técnicas são explicitamente não validadas. O artefato persiste metadados/hash, não falas. Escrita atômica com fsync/replace.

A assinatura inclui pesos/revisão, algoritmo, descritores, aliases, áreas, capacidades, preferências e vínculo físico. Valores dinâmicos irrelevantes/generation não invalidam embeddings/calibração. Mudança de disponibilidade restringe o universo operacional e bloqueia aceitação até revalidar, sem descartar cache semântico por uma leitura de sensor.

**Não há corpus representativo fornecido ou calibração estatística de produção aprovada nesta entrega.** O gate neural permanece fechado por padrão; fast paths fundamentados e confirmação funcionam normalmente.

## Cache e concorrência

Mesma instância E5, duas threads CPU, queries em lote de até dois vetores, caches de descritores (32.768) e consultas (128), namespace v3. Pesos não são recarregados por pedido. Sem processo adicional de modelo, threads de espera ativas ou novo serviço. Serviço serializado com 429 para ocupação, timeout 180 s e trabalho protegido até liberação, sem callback executável. Transporte original de loopback/ingress e cabeçalho interno, CSRF e autenticação HA são preservados. Limites de corpo, catálogo, falas, aliases e exemplos impedem trabalho ilimitado.

Métricas: preparação, carga, recuperação/cache, inferência, validação, fila, total, CPU e RSS quando disponíveis. Tempo HTTP inclui rede/validação e não equivale a CPU exclusiva.

## App, AI Lab e rollback

Configuração Decision permite selecionar `structured-v3` ou `baseline-v2`. O worker recebe a seleção nas opções já existentes. Serviço CLI assume structured-v3; API Python sem seleção conserva baseline-v2 para consumidores antigos. Resultado em voo é descartado se a política mudar no gateway.

A seleção persiste em `decision-policy.json`, esquema 1, separado do `modules.json` versão 2. Assim o app anterior consegue ler suas configurações no rollback. Instalação atualizada sem política usa o novo padrão; arquivo inválido cai para baseline-v2 com erro visível. Demais configurações não são descartadas. Para voltar ao algoritmo anterior, selecione baseline-v2 e salve no painel; rollback de código usa v6.0.1. Os artefatos de calibração coexistem.

O AI Lab nativo usa o WebSocket autenticado do HA. Seleciona algoritmo nos testes, mostra campos/evidências/localização/candidatos, scores separados, cache e tempos. Comparação executa os dois algoritmos sequencialmente, no mesmo catálogo/texto de entrada autorizado, até 20 textos por lote. Não troca o algoritmo de produção, não emite token de execução nem chama serviços domésticos. Métricas rotuladas e JSON podem ser exportados. O teste normal mantém simulação padrão e revisão de execução existente.

## Reprodução e limites

Execute `python -m pytest tests -q` no venv do projeto, os contratos de cada pacote com seu PYTHONPATH e `node --test tests/test_ai_lab_panel.js`. Não use pytest sobre toda a árvore de snapshots: fontes vendorizadas têm módulos homônimos, dependências e ambientes próprios.

Build: `docker build -t arandu-assistant:6.0.0 arandu_stack`. `tests/decision_v2_check.py` roda com Python do venv Decision da imagem final, modelos E5 já instalados offline e `ARANDU_DECISION_MODELS`/`ARANDU_DECISION_OUTPUT`. Não baixa pesos. A/B usa o mesmo encoder residente, algoritmos explicitamente selecionados, 29 requisitos/fixtures técnicas, três repetições e catálogos de 8/256 entidades. A referência executável é baseline-v2 preservada do HEAD anterior (Decision 1.0.1, mesma classificação 1.0.0 com hardening).

Integração opt-in: `ARANDU_DECISION_STACK_URL=http://127.0.0.1:8770` e `ARANDU_NLU_ENDPOINT=http://127.0.0.1:11569`, depois `pytest tests/test_decision_v2.py tests/test_phase1_e2e.py tests/test_semantic_safety.py -q`. Usa Rust, gateway, serviço e E5 reais; executor HA simulado, sem residência real. `tests/intelligence_stack_check.py` valida também Tucano real preservado. No contêiner isolado, `ARANDU_DECISION_VALIDATE_MUTATIONS=1 python tests/decision_gateway_check.py` testa salvar/reiniciar ambos os algoritmos, restaura a escolha inicial e envia oito pedidos simultâneos de comparação sem execução. Não apontar esse teste para uma instalação residencial.

Resultados e comandos efetivamente executados: [evidência](DECISION-VALIDATION-2026-11-11.json). Não extrapolar o pequeno conjunto técnico para fala humana, taxa produtiva de LLM, calibração ou i5-6500T. Os gates históricos/corpus ausentes documentados na Fase 1 permanecem limitações anteriores; não foram recriados a partir da saída do produto. A instância HA física exige autenticação e não é declarada aprovada sem teste real.

## Resultado da imagem final

244 testes Python e 181 subtestes passaram; três testes STT opt-in foram ignorados. Passaram também 88 testes Rust disponíveis, dez contratos Decision/LLM e dez testes JavaScript. Build Docker e API reais passaram, sem fontes sobrepostas; o pacote Decision instalado foi comparado byte a byte com suas fontes empacotadas. ZIP HACS 7.0.0 verificado com CRC, conteúdo e compilação dos arquivos Python.

| Medida técnica | baseline-v2 | structured-v3 |
|---|---:|---:|
| Acerto conjunto ou abstenção correta | 23/29 | 29/29 |
| Ranking positivo (intenção + entidade) | 10/15 | 15/15 |
| Resolução direta | 7 | 12 |
| Sem proposta | 20 | 14 |
| Ativação indevida na fixture | 0 | 0 |
| Latência warm p50, catálogo pequeno | 3.78 ms | 0.68 ms |
| Latência warm p95, catálogo pequeno | 6.44 ms | 4.04 ms |
| Latência warm p99, catálogo pequeno | 7.37 ms | 4.54 ms |
| Ranking warm p50, 256 entidades | 18.24 ms | 16.00 ms |
| Ranking warm p95, 256 entidades | 24.03 ms | 18.58 ms |
| Ranking warm p99, 256 entidades | 25.02 ms | 20.48 ms |

A promoção do padrão apoia-se no ganho de ranking/cobertura nestas fixtures e na preservação das recusas; baseline-v2 permanece disponível. Catálogo pequeno: 29 entradas × três execuções, percentis sobre 58 repetições warm. Catálogo grande: uma execução com cache frio e 30 warm por algoritmo. São amostras curtas sujeitas a ruído; não há garantia universal de latência. Ambos acertaram o último alvo do catálogo grande.

Carga E5 compartilhada: 6,83 s. Na última repetição v3 grande: 255 hits de descritores, dois hits de consultas, zero misses. RSS observado aproximadamente 805 MiB antes e 811 MiB depois, no mesmo processo sequencial; não foi demonstrada redução de RAM. Amostra ociosa de um segundo consumiu cerca de 0,055 ms de CPU de processo; não é monitoramento prolongado.

O fluxo real de calibração foi exercitado com E5, 50 entradas de treino e 30 de validação numeradas e marcadas FIXTURE_TECNICA. Gravou artefato separado, preservou o anterior e manteve `validated=false`. ECE/Brier zero nessa fixture não constituem validação estatística de produção.

A API salvou/reiniciou e confirmou ambos os algoritmos. Oito pedidos simultâneos reais: um 200, sete 429 no serviço (502 tipado no gateway existente), sem proposta executável. Rust → Decision → executor HA simulado executou a paráfrase autorizada sem chamar LLM e recusou restrições. Comparação pelo handler nativo usou o mesmo catálogo e não criou token/efeito. A taxa global de uso do LLM antes/depois não foi medida.

HA físico: probe de `homeassistant.local:8123/api/` retornou 403, sem sessão administrativa; validação física/HAOS/satélite permanece pendente. Sem medição no i5-6500T e sem corpus representativo. As limitações históricas do gate completo/corpus NLU constam do relatório da Fase 1; não são declaradas aprovadas aqui.

## Arquivos e revisão

- `decision_context.py` na HACS e cópia idêntica no pacote: campos, evidências e assinatura semântica.
- Decision `structured.py`, `adapter.py`, `service.py`: ranking, calibração, métricas, seleção e comparação.
- App `settings.py`, `modules.py`, `server.py`: seleção, persistência compatível e descarte de resposta durante mudança de política. Worker existente já repassa as opções.
- HACS `intelligence.py`, `ai_lab.py`, `ai_lab_core.py`, `ai_lab_panel.js`: validação independente, fallback, comparação autenticada e observabilidade.
- `tests/test_decision_v2.py`, `decision_v2_check.py`, `decision_gateway_check.py`, testes de painel/segurança e smoke da stack: provas unitárias, integração e modelo real.
- Manifests, config/pyproject/runtime Decision, README, INTELLIGENCE, CHANGELOG e este relatório: versões e documentação.

Diff revisado quanto a autorização, capacidade real, restrições da Fase 1, ambiguidade, confirmação, concorrência, cache, migração e ausência de alterações em NLU/LLM/STT/TTS/Wake. A revisão corrigiu o fallback indevido de área desconhecida, inclusive quando uma entidade não relacionada contém o nome do cômodo. A proteção recebeu regressão automatizada. A lista exata dos arquivos alterados acompanha a evidência JSON.
