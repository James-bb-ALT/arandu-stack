# Fase 4 — concorrência, filas e observabilidade

Estado: candidato experimental validado localmente; sem homologação física. Baseline integrado
`1832469`, incluindo Fases 3.1 e 3.2. Resultados abaixo separam HA simulado, motores
reais e observação física. Nenhum resultado no Ryzen substitui o i5-6500T/HD 530.

## Execução e limites

O runtime admite até 20 pedidos, usa quatro slots NLU e serializa cada identidade,
origem e conversa. A espera neural tem orçamento separado: um pedido ativo e quatro
em espera. Um pedido lento no LLM não ocupa os slots NLU. A configuração avançada
permite ajustar concorrência, espera e prazo total (padrão 45 s; intervalo 5–90 s).
Decision tem prazo de 10 s; LLM, 30 s. Saturação é explícita, sem fallback neural
adicional. Três falhas de transporte abrem o circuito por 15 s; somente uma tentativa
de recuperação é admitida. Ações HA não recebem retries automáticos.

O commit de efeitos bloqueia os IDs de entidades e dispositivos em ordem estável.
Permissões, capacidades, alvos, confirmações e estado relativo são verificados dentro
desse bloqueio. Dispositivos independentes continuam concorrentes; dois incrementos
no mesmo dispositivo observam os estados intermediários. Cancelar uma conversa não
cancela outra origem. Sessões ativas não são removidas pelo TTL.

Agendamentos usam dois slots independentes, quota de 50 jobs e prazo de entrega de
20 s. O claim persistente antecede o efeito: reiniciar durante um disparo deixa o job
interrompido, sem repetição automática. Ações guardam a autoridade original e a
revalidam no vencimento; jobs legados sem essa evidência não podem executar serviços.
Timers e lembretes mantêm notificação e TTS configurado separados da sessão Assist.

O TTS limita a oito chunks PCM em memória, aplica backpressure e mantém ordem e
cancelamento por stream. A fila de síntese é limitada. O caminho Assist continua
aguardando a síntese completa: primeira amostra no protocolo não comprova reprodução
audível nem autoriza habilitar streaming físico. Piper mantém seu comportamento
original de threads. O limite manual é experimental e desligado por padrão: duas
threads reduziram CPU da frase longa de 1672 para 609 ms, mas aumentaram a duração
de síntese de 209 para 302 ms neste Ryzen. Nenhuma voz é trocada por esse ajuste.

O monitor coleta saúde, RSS da família do processo, CPU e pressão de memória, com
reinício limitado e backoff. Pressão de RAM produz estado degradado, sem matar
modelos arbitrariamente. CPU/GPU, modelos e thresholds existentes são preservados.

## Rastreamento

O Fluxo mostra filas, normalização, NLU, modelos, preflight, execução e scheduler.
Durações usam relógio monotônico; UTC é usado somente para timestamps. A janela é
limitada a 50 registros/300 s e calcula p50/p95/p99, erros, timeouts e saturação.
Exports JSON/CSV não incluem fala por padrão. Jobs usam uma referência opaca, sem
nomes de dispositivos ou conteúdo do lembrete.

Wake, playback e a junção áudio→Assist ficam `not_observed` quando o contrato público
não oferece identificação confiável. Captura/buffering/resample e inferência STT são
medições locais distintas. A duração da integração não é chamada de áudio-a-áudio.

## Comparação reproduzível

`tests/phase4_benchmark.py <output.json> [worktree]` usa 40 pedidos, oito sessões,
cinco lotes e NLU simulado com atraso fixo de 30 ms. O mesmo script roda em processos
separados sobre o baseline integrado e sobre a implementação. Host Windows/Ryzen
5700X compartilhado; nenhum serviço físico HA é acionado.

| Métrica | Baseline integrado | Fase 4 |
|---|---:|---:|
| p50 | 167,67 ms | 65,40 ms |
| p95 | 290,81 ms | 93,14 ms |
| p99 | 336,76 ms | 93,19 ms |
| Throughput | 28,03 pedidos/s | 87,38 pedidos/s |
| RSS máximo amostrado | 36,25 MiB | 34,33 MiB |
| CPU do processo | 62,5 ms | 62,5 ms |
| Sucessos | 40/40 | 40/40 |
| Efeitos simulados inesperados | 0 | 0 |

Isso mede a remoção da serialização global na orquestração; não mede inferência
neural, áudio, dois satélites físicos ou as metas de produto no i5.

`tests/phase4_scenarios.py` usa o servidor Rust real com HA simulado: luz, sensor,
agendamento, timers pizza/bolo, despertador, lembrete, exclusão e negação. A execução
nativa posterior passou 9/9 cenários, manteve os dois nomes e não acionou a TV
excluída. A integração corrigiu o binding dos parâmetros temporais/exclusões e a
extração de nomes de timers; comandos restritos sem prova tipada continuam recusados.

`tests/phase4_audio_real.py <output> [worktree]` sintetiza oito frases próprias com
Piper Faber e transcreve o mesmo WAV com Orukeet INT8. Use
`ARANDU_STT_AUDIO_SOURCE` para parear exatamente as entradas e
`ARANDU_STT_MODEL_PATH` para pesos existentes. Relatórios incluem SHA-256, carga,
inferência, correção, CPU, RSS, WER/CER e clipping. As falas são sintéticas; não são
um corpus humano de acurácia. Os testes Windows reais do Lab e Wyoming passaram
com `ARANDU_STT_PYTHON` apontando para o intérprete local.

## Motores reais e gargalos

Os testes reais usam pesos existentes, dois threads configurados e host compartilhado.
Decision preserva `OMP_WAIT_POLICY=PASSIVE`, `KMP_BLOCKTIME=0` e BLAS/MKL com dois
threads em ambos os lados. Comparar a biblioteca isolada sem essas variáveis com o
serviço configurado seria uma comparação inválida; esses ensaios foram descartados.

| Etapa / gargalo | Baseline | Fase 4 | Interpretação |
|---|---:|---:|---|
| E5 Small, consulta sem cache, p50/p95/p99 | 23,24 / 32,31 / 47,74 ms | 22,72 / 25,18 / 27,52 ms | modelo e calibração preservados |
| MiniLM, consulta sem cache, p50/p95/p99 | 22,28 / 25,30 / 25,83 ms | 24,74 / 30,34 / 32,42 ms | variação desfavorável; não é ganho universal |
| E5 Base, consulta sem cache, p50/p95/p99 | 57,98 / 67,74 / 69,84 ms | 57,11 / 70,42 / 76,52 ms | custo do modelo maior permanece |
| Orukeet, inferência mediana de 32 amostras | 124,70 ms | 122,28 ms | mesmos oito WAVs, RAW idêntico 8/8 |
| Orukeet, carga | 1972 ms | 1940 ms | carga separada de inferência |
| Piper, frase curta quente, mediana de 3 | 24,45 ms | 23,75 ms | não mede som audível |
| Piper, frase longa quente, mediana de 4 | 209,43 ms | 222,47 ms | +13 ms; buffer limitado como benefício operacional |
| Piper, primeira carga + frase curta | 3258 ms | 3136 ms | voz Faber explicitamente escolhida no ensaio |

Decision passou 29/29 fixtures por modelo CPU em ambos os lados, sem falsa ativação;
o smoke numérico contra SentenceTransformer passou. Consulta com cache p50 ficou
entre 0,90–0,98 ms antes e 0,94–0,99 ms depois. Carga fria: 9,5–12,8 s antes e
9,3–12,7 s depois. RSS máximo amostrado, processo acumulado por ensaio: E5 Small
830→830 MiB, MiniLM 845→845 MiB e E5 Base 1096→1098 MiB. GPU Intel foi recusada
explicitamente por ausência do dispositivo; utilização, memória compartilhada e
overhead GPU são `not_run`. Não houve troca automática para CPU.

Orukeet p95/p99 por índice mais próximo: 422,57/461,19→424,90/449,39 ms; WER/CER
por frase constam na evidência. Há erros preexistentes: “Pronto”→“Tanto”,
“timer”→“Time” e horário reconhecido incorretamente. Negação/exclusão foram
preservadas nas fixtures pareadas; isso não é prova de acurácia em fala humana.
RSS amostrado do STT: 1064→891 MiB; TTS: 247→282 MiB. São máximos amostrados,
não atribuição causal de economia nem média do host. Piper auto ORT permanece
0/0 em ambos os lados. A variante manual de duas threads está desligada por padrão.
Piper é estocástico: hashes PCM diferentes não demonstram truncamento; ordem,
backpressure e fim completo são verificados separadamente. Houve clipping pequeno,
não nulo; qualidade acústica física não foi validada.

No Docker final, o mesmo WAV retornou “Não ligar a luz da sala.” nas portas de
validação 10354→10350 e 10244→10212. Piper entregou `AudioStop` e 6/61 chunks
completos, frase curta/longa. Primeira frase Faber incluiu carga fria (~2116 ms);
frase longa quente: primeiro chunk ~131 ms, total ~273 ms. Isso é tempo de protocolo,
não início de playback. O identificador Wyoming público legado foi preservado.

Com Rust e Tucano reais, os dois timers foram criados em 9,49 ms enquanto o LLM
estava pendente; quatro notificações simuladas venceram antes da resposta. A piada
teve resposta de conversa sem serviço HA; 671 ms quente neste ensaio (outra execução
fria: ~9 s). O alvo de 2–3 s não é garantido em carga fria. Sleep timer criado e
cancelado, zero efeitos inesperados. Falha injetada somente no processo Decision do
container de teste recuperou em 16,84 s, uma tentativa, demais módulos iniciados.

Matriz LLM offline: 37 fixtures técnicas por combinação, sem serviços HA, mesmo
llama.cpp/template/pesos e orçamento de dois threads. Os contratos v2 mantiveram
37/37 para Tucano e Qwen3 0.6B; v1 manteve 12/37 (contrato legado limitado). Todos
os pares tiveram zero falsa ativação e zero erro técnico, sem confundir recusa
correta com decodificação correta dos parâmetros. Não é gold representativo.

| LLM quente, geração p50/p95/p99 | Baseline | Fase 4 |
|---|---:|---:|
| Tucano v1 | 1976 / 2600 / 3962 ms | 1814 / 2449 / 3823 ms |
| Tucano v2 | 3005 / 4281 / 4413 ms | 2988 / 4202 / 4392 ms |
| Qwen3 0.6B v2 | 4091 / 5261 / 5642 ms | 4331 / 5524 / 5728 ms |

Tokens/s medianos: Tucano v2 34,06→34,62, Qwen3 v2 21,67→20,33. CPU por pedido
mediana v2: 5850→5830 ms e 7885→8400 ms, respectivamente. RSS máximo ~1860→1859
MiB e ~1982→1981 MiB. A métrica CPU legada v1 é acumulada desde o início do processo,
não custo por pedido; a evidência marca essa diferença. Carga fria e detalhes estão
no JSON. O resultado misto não promove nova otimização neural nem troca de modelo.

## Cobertura dos quinze cenários

| # | Cenário | Evidência / limite |
|---|---|---|
| 1 | Luz e sensor | Rust HTTP real + HA simulado, 5 repetições cada |
| 2 | Três Decision CPU/GPU | 3 CPU reais; 3 GPU unsupported; rollback no navegador |
| 3 | LLM curto e conversa | inferência real e matriz de fixtures; sem efeitos HA |
| 4 | Satélites simultâneos | origens/conversas simuladas concorrentes; satélites físicos not_run |
| 5 | Mesmo dispositivo e distintos | testes de locks/commit e execução simulada |
| 6 | Comando relativo concorrente | releitura sob lock; nenhum incremento perdido na fixture |
| 7 | Cancelamento | modelo e buffers simulados; playback físico not_run |
| 8 | Confirmação cruzada | usuário/origem errados recusados, confirmação única |
| 9 | Lote com exceção | Rust real; zero efeito na TV excluída |
| 10 | Pizza + bolo durante LLM | Rust/Tucano reais; nomes preservados; entregas simuladas |
| 11 | Alarme/lembrete durante LLM | entrega simulada independente da fila neural |
| 12 | Sleep timer cancelado | Rust real + scheduler, zero efeito após cancelar |
| 13 | Restart e órfãos | Store simulado; claim interrompido não repete; setup arma jobs |
| 14 | STT/TTS curto/longo | WAVs pareados, inferência nativa real e Wyoming Docker |
| 15 | Falha/timeout/GPU/RAM | falha real de processo; timeout/circuit/RAM simulados; GPU ausente real |

## Verificação, reprodução e promoção

- Suíte raiz final com Stack/Rust reais: **346 testes e 200 subtestes aprovados**,
  quatro opcionais ignorados, oito warnings de `aiohttp.AppKey`. Inclui o novo
  teste de timeout do scheduler e de saturação HTTP; workload isolado passou 19/19.
- STT nativo/Lab/Wyoming e admissão direta: quatro aprovados separadamente. TTS:
  29 aprovados. Rust: 103 Windows e 103 Linux; `cargo fmt --check` aprovado.
- JavaScript: 15 aprovados; sintaxe app/Fluxo válida. YAML/JSON/metadados válidos.
- Docker final construído. Chromium em contexto novo salvou/reiniciou/restaurou
  filas TTS e capturas STT, confirmou modelo real, etapas e exports JSON/CSV sem texto.
  Decision CPU selecionado/verificado; GPU indisponível preservou seleção anterior.
- A suíte Python legada NLU com o HACS ativo tem os mesmos quatro testes falhos
  no baseline e depois: três expectativas antigas de mensagens de identidade e uma
  de `requires_review`. Demais: 153 aprovados, 68 ignorados, 65 subtestes. Validadores
  antigos de distribuição/HACS dependentes de Ruby não executados neste Windows.
  Não são apresentados como suíte verde nem corrigidos mudando expectativas.
- Primeira rodada iniciada antes do Decision pronto teve três falhas de conexão;
  a rodada posterior aguardou readiness. Uma expectativa de ambiente detectou a
  remoção indevida de PASSIVE, corrigida preservando a política do baseline.
- HAOS físico, i5-6500T/HD 530, reprodução audível, cancelamento físico durante
  playback e correlação completa com satélites: `not_run`/`not_observed`. CPU média,
  pico de GPU e latência áudio-a-áudio física não foram inferidos de métricas locais.

Os scripts `phase4_benchmark.py`, `phase4_scenarios.py`, `phase4_audio_real.py`,
`phase4_wyoming_real.py`, `phase4_live_concurrency.py`, `phase4_dashboard_check.py`,
`decision_multimodel_check.py` e `llm_v2_check.py` são opt-in. Use instância isolada,
pesos existentes e diretórios de saída próprios; não aponte para HA residencial.
`docs/PHASE-4-EVIDENCE.json` guarda agregados, hashes dos relatórios e ambientes.
Logs/WAVs locais não são empacotados. Componentes: app/HACS 10.0.0, NLU 0.6.5,
STT 4.0.2, TTS 0.2.2; Decision/LLM/Wake preservados. Publicação como pré-release;
não declara homologação dos gates físicos.

O commit `549dc61` foi incorporado: catálogo/residência LLM isolados, 20 testes;
sem novas rotas ou promoção de LFM/Qwen3 1.7B. Tucano permanece padrão. Consulte
`LLM-MODEL-RESIDENCY.md` para os limites desse alicerce.

Intercom, rotinas por voz, administração de automações/listas, elipse avançada e
consultas históricas continuam fora desta fase. Nenhuma promoção presume que esses
gates pendentes passaram.
