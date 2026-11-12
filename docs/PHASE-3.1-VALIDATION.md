# Fase 3.1 — validação executada em 11/11/2026

## Matriz real: três modelos × dois backends

| Combinação | Resultado | Dimensão | Política correta | Intenção diagnóstica (15 rótulos distintos) | Consulta sem cache p50 / p95 / p99 ms | Consulta em cache p50 / p95 ms | Maior RSS amostrado MiB |
| --- | --- | ---: | ---: | ---: | --- | --- | ---: |
| `e5-small:cpu` | pass | 384 | 29/29 | 15/15 | 17.05 / 20.51 / 22.14 | 0.96 / 1.34 | 834.1 |
| `e5-small:gpu_intel` | unsupported local / GPU not_run | — | — | — | não medido | não medido | não medido |
| `minilm-multilingual:cpu` | pass | 384 | 29/29 | 14/15 | 15.97 / 18.83 / 20.28 | 0.99 / 1.27 | 850.1 |
| `minilm-multilingual:gpu_intel` | unsupported local / GPU not_run | — | — | — | não medido | não medido | não medido |
| `e5-base:cpu` | pass | 768 | 29/29 | 15/15 | 55.25 / 65.24 / 76.17 | 0.97 / 1.30 | 1102.9 |
| `e5-base:gpu_intel` | unsupported local / GPU not_run | — | — | — | não medido | não medido | não medido |

**Nenhuma falsa ativação nos 29 cenários de política**, em cada modelo CPU. Alvo e área diagnósticos: 15/15 nos três. Esses são exemplos aprovados/fixtures técnicas, não taxas de acerto de fala humana nem rótulos produzidos pelo modelo.

O MiniLM errou a intenção de “Acnde o ventilador” no ranking forçado: `turn_off` em vez de `turn_on`. O fast-path de hipótese ortográfica/confirmada manteve a política correta; esse ranking não executa ações. E5 Base não melhorou a qualidade desta amostra frente ao Small. Default E5 Small/CPU preservado.

## Metodologia e limites dos números

Imagem final `arandu-phase31:verified`, sem sobreposição de fontes. Pesos montados somente leitura no benchmark. Ryzen 7 5700X, Docker Linux/WSL2, duas threads, FP32; nenhuma GPU Intel exposta. Não é teste no i5-6500T/HD 530, nem host dedicado com frequência/carga global controladas. Os outros testes pesados próprios foram encerrados durante a medição.

- Política: 29 frases únicas (15 positivas e 14 ambiguidades/recusas), um warm-up e três repetições por frase; qualidade pela última resposta. Inclui ação/alvo/área, grafia, ambiguidades, negação, condição, exclusão, parâmetros não suportados e compostos recusados.
- Ranking diagnóstico: apenas as 15 positivas, `semantic_only=True` para medir embeddings mesmo quando a política resolveria sem modelo. Não é um caminho de execução HA. Há 45 observações com query cache hit e 45 com query cache miss por modelo, três repetições em cada modo; **90 observações não são 90 falas independentes**. Descritores permanecem quentes no teste de query miss.
- A tabela separa consultas com e sem cache. Percentis são descritivos de amostra curta, sem intervalos de confiança. Throughput é sequencial observado, não capacidade garantida de produção.
- RSS é o maior valor pós-requisição observado do processo, não pico instantâneo do sistema nem limite de RAM garantido. Vocabulario curto e pesos mapeados podem subestimar outro uso. GPU/VRAM/utilização não foram medidos.

| Modelo CPU | Carga da instância após unload ms | Tokenização mediana ms | Embeddings medianos ms | Ranking mediano ms | CPU mediana ms | Throughput query miss req/s |
| --- | ---: | ---: | ---: | ---: | ---: | ---: |
| `e5-small:cpu` | 8948.0 | 0.654 | 14.969 | 0.027 | 34.566 | 56.9 |
| `minilm-multilingual:cpu` | 5504.9 | 0.653 | 13.881 | 0.028 | 32.215 | 61.4 |
| `e5-base:cpu` | 7031.6 | 0.648 | 53.070 | 0.028 | 110.747 | 17.8 |

Carga foi medida uma vez na sequência Small → MiniLM → Base. Small inclui a primeira importação dos frameworks; os demais reutilizam bibliotecas já carregadas. Não houve flush de cache de arquivos do SO. Portanto esses valores não são startup frio isolado ou comparação estatística entre modelos. CPU é delta de tempo do processo e pode exceder wall time por usar duas threads. Tokenização/embeddings/ranking são relógios separados de etapas reais, não valores estimados.

## Referência Decision 2.0 real

Executado o pacote Decision 2.0 da imagem `arandu-assistant:6.0.0`, com runner extraído do SHA imutável `ca4e67d55d5f7b54c181057f4187432fc2d83cc7`, mesmos pesos E5 Small e catálogo. Resultado: `baseline-v2` 23/29, `structured-v3` 29/29, zero falsas ativações. A Fase 3.1 manteve 29/29 na política estruturada; **não declara ganho de acurácia do default que já resolvia esses casos**.

A comparação adicional com `SentenceTransformer.encode` nos três modelos CPU teve erro absoluto máximo zero nas duas entradas de referência, confirmando prefixos/perfil/pooling/normalização do adaptador. Não demonstra equivalência CPU/GPU nem autoriza compartilhar calibração. Não se treinou modelo ou calibrou confiança com fixtures.

## GPU, IR e dispositivo

OpenVINO 2024.6.0, ONNX 1.17.0; dispositivos enumerados: ['CPU']. Kernel `6.18.33.2-microsoft-standard-WSL2`. Pacotes do container: `intel-opencl-icd	22.43.24595.41-1; ocl-icd-libopencl1:amd64	2.3.1-1`. Sem render nodes, `clinfo -l` sem plataformas. GPU local unsupported; HD 530 física not_tested. Nenhum kernel/driver do host foi alterado. `video: true` só solicita acesso suportado pelo Supervisor.

IR E5 Small exportado e comparado em target **CPU**: erro máximo `6.705522537231445e-08`, dentro de atol 2e-5 / rtol 2e-4. Execução confirmada: `['CPU']`; validação GPU explicitamente **false**. Não houve inferência/benchmark real da HD 530. Exportação dos outros dois modelos não foi validada fisicamente em GPU.

## Testes e integração executados

- `python -m pytest -q tests` com stack isolado e Rust real: **281 passaram, 200 subtestes passaram, três testes acústicos STT opcionais ignorados, sete avisos aiohttp AppKey**. `probatio` foi instalado somente no venv deste worktree para executar também a regressão de descoberta HACS.
- `node --test tests/test_ai_lab_panel.js`: **15 passaram**. Syntax checks dos dois frontends passaram.
- Build completo do Docker; hashes do serviço/backend/contrato/registry e JavaScript da dashboard comparados ao código local. Versão runtime Decision 3.0.0 confirmada.
- API real: instalação/verificação HTTP, aplicar os três modelos, embeddings 384/384/768, seleção implícita na inferência, política/algoritmo/limiares mais altos aplicados imediatamente, GPU indisponível com rollback, tentativa de troca durante inferência recusada, controles de Origin/CSRF/allowlist/GET.
- Chromium headless, contexto novo: escolha MiniLM/CPU pela dashboard, confirmação, carga real, erro GPU visível e estado CPU anterior preservado; zero page errors. O teste foi adaptado à CSP existente, sem adicionar unsafe-eval à aplicação.
- Reinício do container: MiniLM/CPU persistido e carregado; requisição sem campo model inferiu com MiniLM.
- AI Lab nativo com servidor real: seis combinações, mesmos rótulos/catálogo, três CPU corretas e três GPU unsupported; sem tokens/efeitos na comparação. Teste individual E5 Base realizou embeddings reais de 768 dimensões, com confiança ausente sem calibração.
- Regressões de NLU Rust HTTP, LLM real Tucano/Qwen3, executor HA simulado, catálogo stale, permissão revogada, identidade/origem, timeout/cancelamento sem ação tardia e confirmação única passaram dentro da suíte integrada. Nenhum arquivo de gramática/executor/scheduler foi modificado.
- Unitários novos cobrem registry/hash/revisão/licença, staging/rollback, prefixos/dimensões, cache/calibração por combinação, rejeição de fallback GPU para CPU, pooling mascarado e execução GPU exclusiva simulada. Mocks testam contratos, não validam a HD 530.

## Contenção Decision e LLM real

Três repetições, um warm-up, E5 Small e Tucano CPU no mesmo container/slot compartilhado, query cache quente: Decision sozinho p50/p95 **6.44/7.41 ms**; atrás do LLM p50/p95 **660.02/660.62 ms**. Throughput misto observado **2.91 requisições/s** (seis requisições no total). A fila do Decision acompanhou o tempo real de geração do LLM. Não há promessa de latência sob contenção.

Contenção com STT: **not_run**, sem modelo/áudio STT provisionado neste worktree isolado. GPU em contenção: **not_run**, dispositivo ausente. Isso não foi apresentado como medição zero ou aceleração.

## Limites e publicação

HA físico autenticado e HD 530/HAOS não foram exercitados; nenhuma ação doméstica em instância compartilhada foi executada. Os endpoints, frontend e integração foram implementados/testados localmente; permanece a validação física. Não há corpus representativo/licenciado independente suficiente para calibrar confiança ou alegar qualidade de fala humana. GPU segue experimental e indisponibilidade explícita; CPU e default anterior funcionam.

Entrega por branch/PR, sem merge/tag/release nesta tarefa. Versão global e release combinada dependem da integração com a Fase 3.2. Veja [contrato de handoff e arquivos compartilhados](PHASE-3.1-DECISION-MULTIMODEL.md) e [evidências JSON](evidence/decision-multimodel-phase31.json).
