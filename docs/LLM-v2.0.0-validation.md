# LLM 2.0 — validação executada em 2026-11-11

## Inferência real da imagem final

Imagem `arandu-assistant:7.0.0`, sem sobreposição de fontes do host, dois GGUF reais previamente baixados e verificados por SHA-256, CPU Ryzen 7 5700X/Docker Linux, duas threads, contexto 4096, temperatura zero, teto 384 tokens. Pesos montados somente leitura; nenhuma ação doméstica. Uma passagem sequencial por modelo, sem carga concorrente de outros testes. Não são medições em i5-6500T nem intervalos estatísticos de confiança.

São **37 fixtures técnicas**: 25 planos autorizados (incluindo compostos, contexto, parâmetros e consultas), 10 recusas esperadas e duas conversas gerais. Origem: exemplos aprovados do briefing e fixtures internas; não corpus representativo de fala. Não foram fabricadas 300 amostras nem usadas respostas do modelo como gold.

Baseline: caminho legado preservado do LLM **1.0.1** após Fase 2, com instrumentação de uso/CPU; não execução de um binário original 1.0.0. A melhora abaixo mede a implementação inteira, especialmente extração e restrições determinísticas novas. Não demonstra melhora neural nem generalização livre.

| Medida | Tucano legado v1 | Tucano v2 | Qwen3 v2 |
| --- | ---: | ---: | ---: |
| Plano/resultado completo correto | 12/37 | 37/37 | 37/37 |
| Intenções de todo o plano correto, positivos | 0/25 | 25/25 | 25/25 |
| Entidades de todo o plano correto, positivos | 0/25 | 25/25 | 25/25 |
| Parâmetros de todo o plano correto, positivos | 0/25 | 25/25 | 25/25 |
| Abstenção correta | 10/10 | 10/10 | 10/10 |
| Falsas ativações | 0 | 0 | 0 |
| Respostas geradas aceitas pelo contrato | 29/29 | 27/27 | 27/27 |
| Requisições com geração | 29/37 | 27/37 | 27/37 |
| Recusas sem geração | 8 | 10 | 10 |
| Confirmação solicitada pelo LLM | 0/37 | 0/37 | 0/37 |
| Wall p50/p95/p99, todos os casos, segundos | 1,697 / 2,516 / 10,304 | 3,172 / 5,294 / 7,322 | 3,986 / 5,598 / 17,552 |
| Inferência quente p50/p95/p99, só geração, segundos | 1,848 / 2,514 / 3,870 | 3,206 / 5,292 / 5,530 | 4,173 / 5,596 / 5,800 |
| Amostras quentes geradas | 28 | 26 | 26 |
| TTFT mediano, ms | não medido | 1239 | 1572 |
| Tokens/s mediano, incluindo prefill | 39,74 | 31,87 | 21,04 |
| Maior RSS amostrado do processo, MiB | 1229,9 | 1860,1 | 1981,1 |
| CPU mediana por pedido, ms | não comparável | 6260 | 8060 |
| Carga inicial incluindo hash, ms | 6782 | 4106 | 13159 |

As duas conversas foram classificadas corretamente nos três caminhos; respostas sem alvos/operações. JSON aceito não equivale a plano correto: a baseline frequentemente pede esclarecimento onde o contrato novo produz um plano. Confirmação zero nesta amostra não elimina confirmações das políticas HA, exercitadas em testes separados.

`cpu_ms` legado é acumulado de vida do processo e está explicitamente rotulado, portanto não foi comparado a delta por pedido do v2. RSS é amostra pós-requisição do processo llama-server, não pico exato do sistema; pode crescer com contextos/gramáticas sucessivos. TTFT vem do primeiro conteúdo SSE real, não da abertura da conexão. Tokens vêm do uso retornado pelo servidor. Recusas antes da geração têm zero tokens e não entram no cálculo de geração quente. Prefix cache foi solicitado desativado e os campos de uso observados permanecem no registro local.

**Não há ganho de velocidade comprovado.** Planos novos têm mais dados/validação e usam mais tempo/RAM que esclarecimentos legados. Qwen3 não venceu o Tucano neste teste; o default não mudou. A carga fria inclui disco/hash/runtime e foi medida uma vez por variante. Percentis são descritivos de amostra pequena e heterogênea.

## Integração e regressão

- `python -m pytest -q tests`, com `ARANDU_DECISION_STACK_URL=http://127.0.0.1:8770` e `ARANDU_NLU_ENDPOINT=http://127.0.0.1:11569`: **269 passaram, 200 subtestes passaram, três STT opcionais ignorados, seis avisos aiohttp AppKey**.
- `node --test tests/test_ai_lab_panel.js`: **13 passaram**, incluindo plano tipado, renderização e exportação sem dados residenciais.
- Rust offline disponível: **88 passaram** (51 lib + 18 contextual + 5 http + 3 v2_resolve + 8 v2_interpret_http + 3 v3_interpret_http), NLU 0.6.3 real da distribuição. Os gates históricos com corpus congelado ausente não foram declarados aprovados.
- Build Docker final completo, app 7.0.0, LLM 2.0.0, revisão llama.cpp preservada.
- `tests/llm_gateway_check.py` contra imagem final: aplicação Tucano/Qwen3/Tucano confirmada por identidade e hash, inferência real, segunda chamada sem carga, seis solicitações simultâneas (uma aceita; cinco recusadas como ocupado pelo upstream 429, transportadas como 502 pelo gateway existente), saída real truncada com teto 64 recusada, negociação legada preservada.
- Mesmo script em outro container contendo **somente Tucano**: Qwen3 ausente recusado, configuração e residente Tucano recuperados, inferência seguinte válida. Não removeu pesos do cache principal.
- `tests/intelligence_stack_check.py`: E5 real residente, ação explícita, confirmação de grafia, três conversas reais (piada/capital/definição), Orukeet padrão e transporte protegido. Nova negociação aceita v2.
- `tests/decision_gateway_check.py`, opt-in de mutações isoladas: seleção/salvamento/reinício baseline/v3, concorrência E5, zero efeitos HA.
- Testes opt-in da suíte: Rust real → gateway/Decision → executor HA simulado; AI Lab nativo → gateway → **ambos os GGUF reais**, comparação com mesmo catálogo/rótulo e nenhum token/efeito; teste individual tipado → validação real do adaptador de ventilador HA simulado.

A suíte testa plano completo inválido sem efeito parcial, relativos com estado HA alterado, limite/passo sem clamp, revogação de permissão, mudança de origem/catálogo/usuário, timeout sem ação tardia, confirmação única, instalação em staging/corrupção, status sem pesos, transporte/CSRF/Origin, JSON/evidência/ID/parâmetros forjados, recuperação de perfil e compatibilidade de arquivos. Testes com mocks provam esses contratos; os números de inferência acima vêm de pesos reais.

Comparação adicional da **cascata real Rust → Decision → LLM → HA simulado**, mesmo catálogo, Tucano e 384 tokens: “Deixa o ar em vinte e três graus” foi resolvido pelo Rust nos dois contratos, sem LLM. Depois, com estado HA alterado para 24 °C, “Aumenta mais dois” passou por Decision recusado e LLM: legado retornou `no_match` sem efeito adicional (3768 ms); v2 propôs/executou 26 °C no serviço simulado (3616 ms). Uma amostra por versão, não benchmark de percentis nem evidência de ganho de velocidade. O teste restaura contrato v2 ao terminar.

Reinício real preservou Tucano/contrato v2/perfil 384, com status residente `null` até a primeira inferência. Uma execução imediatamente após reinício encontrou dois HTTP 503 enquanto o Decision inicializava; depois da prontidão, a suíte completa passou em 269 testes. Não foi ocultada indisponibilidade inicial como inferência bem-sucedida.

## Limites da entrega

Não há instância HA física autenticada acessível para testar microfone/Assist/dispositivos reais. Os endpoints/frontend/executor foram implementados e exercitados localmente; permanece pendente a validação física, não a escrita da integração. Conversão de áudio, STT/Wyoming, Wake e TTS não foram alterados nesta fase; os três testes STT opcionais não foram reportados como executados.

Sem corpus representativo, sem teste no i5 e sem generalização comprovada fora das construções cobertas. Qwen3 e release ficam experimentais. Métricas/casos publicáveis: [evidence/llm-v2.0.0.json](evidence/llm-v2.0.0.json). Pesos, caches, logs brutos e dados da casa não entram na publicação.
