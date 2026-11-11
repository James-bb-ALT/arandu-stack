# Fase 3.1 — Decision multimodelo, CPU e GPU Intel

## Base e isolamento

Base comum imutável: **`46da3b4d911bba384c375dfca539ff0e5c132364`**, `origin/main` em 11/11/2026, com a Fase 3 integrada. Referência histórica Decision 2.0: `ca4e67d55d5f7b54c181057f4187432fc2d83cc7`. Desenvolvimento exclusivo em `feat/arandu-fase-3-1-decision-multimodel`, worktree físico `arandu-stack-phase31`. Nenhum arquivo parcial da Fase 3.2 foi incorporado.

Versão proposta do componente: **Decision 3.0.0** (nova etapa de features, conforme nomenclatura do projeto). App 7.0.0 e HACS 8.0.0 permanecem como base; a versão global final não foi escolhida nesta branch. Publicação somente por branch/PR. Sem merge, tag ou GitHub Release. Não inicia Fase 4.

## Implementação

O serviço permanece passivo, offline, sem credenciais HA. NLU v4, executor HA, validação de identidade, permissões, catálogo, restrições semânticas, confirmação e preflight existentes permanecem intactos. O Decision continua propondo apenas ligar/desligar/consultar capacidades autorizadas; não acrescenta serviços arbitrários nem ações temporais.

`EmbeddingBackend` tem implementações CPU/SentenceTransformer e OpenVINO GPU Intel. `Encoder.infer()`, `readiness()` e `calibrate()` continuam disponíveis. Algoritmos `baseline-v2` e `structured-v3` permanecem selecionáveis. O fast-path determinístico e recusas semânticas não carregam embeddings nem exigem GPU.

Registro, proveniência e SHA-256 de **cada arquivo** estão em [`model_manifests.json`](../arandu_stack/rootfs/modules/arandu_decision/rootfs/src/src/arandu_decision/model_manifests.json). Instalação individual por HTTPS/revisão fixa, limite de tamanho/tempo/espaço, staging, hash completo, promoção por rename e recuperação do diretório anterior em exceção. Artefatos válidos existentes são verificados e reutilizados. Inferência não baixa nem converte modelos. Pesos/caches não entram no Git/imagem.

| ID | Fonte / licença declarada | Revisão | Dimensão | Perfil | Bytes instalados |
| --- | --- | --- | ---: | --- | ---: |
| `e5-small` | [intfloat/multilingual-e5-small](https://huggingface.co/intfloat/multilingual-e5-small), MIT | `614241f622f53c4eeff9890bdc4f31cfecc418b3` | 384 | query:/passage:, mean pooling, 256 tokens | 488223777 |
| `minilm-multilingual` | [sentence-transformers/paraphrase-multilingual-MiniLM-L12-v2](https://huggingface.co/sentence-transformers/paraphrase-multilingual-MiniLM-L12-v2), Apache-2.0 | `e8f8c211226b894fcb81acc59f3b34ba3efd5f42` | 384 | sem prefixo E5, pooling/tokenizer próprios, 128 tokens | 479729010 |
| `e5-base` | [intfloat/multilingual-e5-base](https://huggingface.co/intfloat/multilingual-e5-base), MIT | `d128750597153bb5987e10b1c3493a34e5a4502a` | 768 | query:/passage:, mean pooling, 256 tokens | 1129465329 |

Os três pesos foram baixados/verificados e inferidos de verdade em CPU. O tamanho publicado soma os arquivos pinados, sem cache IR adicional. MiniLM é só ligeiramente menor em disco que E5 Small; não se promete redução proporcional de RAM/latência.

## GPU Intel: contrato e resultado real

OpenVINO **2024.6.0** e ONNX **1.17.0** são extras do Decision, instalados no Python 3.12 do app. PyTorch 2.9.1, SentenceTransformer 5.2.0 e Transformers 4.57.3 preservados. O container Bookworm inclui OpenCL ICD e driver Intel de espaço de usuário; não instala driver/kernel no HAOS. `video: true` pede mapeamento suportado pelo Supervisor. Sem `full_access`, `SYS_ADMIN`, desativação de proteção ou alterações no host.

`prepare` exporta os pesos locais com atenção eager, ONNX opset 14, dimensões batch/sequência dinâmicas e IR FP32 (`compress_to_fp16=False`). Conversor `torch-onnx14-eager-mean-pooling-v1`. Artefatos IR têm hashes e vínculo a fonte/conversor/versões; preparação é explícita e separada da inferência. O modelo residente é descarregado durante a preparação e restaurado depois.

GPU exige: dispositivo enumerado por `ov.Core`, IR íntegro, compilação com target **`GPU`**, `EXECUTION_DEVICES` contendo exclusivamente GPU, smoke numérico finito e dimensão correta. Nunca usa `AUTO`/`HETERO` nem fallback CPU. Enumeração significa `not_tested`; apenas a carga/smoke GPU permite `ready`. Presença de `/dev/dri` não autoriza prontidão.

**Ambiente medido:** Docker Linux/WSL2, Ryzen 7 5700X, kernel `6.18.33.2-microsoft-standard-WSL2`; nenhum render node, `clinfo -l` sem plataformas, OpenVINO enumerou somente `CPU`. Driver do container: `intel-opencl-icd 22.43.24595.41-1`, loader `ocl-icd-libopencl1 2.3.1-1`. Portanto as três combinações GPU são **unsupported neste ambiente**, inferência GPU **not_run**. A HD 530/HAOS física é **not_tested**, não foi declarada incompatível nem acelerada.

Fontes: [requisitos OpenVINO 2024](https://docs.openvino.ai/2024/about-openvino/release-notes-openvino/system-requirements.html), [plataformas Intel legadas](https://github.com/intel/compute-runtime/blob/master/documentation/LEGACY_PLATFORMS.md), [mapeamento de vídeo pelo Supervisor](https://contributors.home-assistant.io/apps/reference/configuration/#hardware). Gen9/Skylake exige a família de driver apropriada; drivers regulares Intel posteriores à separação legacy1 não devem ser presumidos compatíveis. Suporte documental não substitui compilação/inferência no equipamento alvo.

O IR **E5 Small** foi exportado de verdade e comparado numericamente em **OpenVINO CPU** com SentenceTransformer: erro absoluto máximo registrado nas evidências, dentro de `atol=2e-5, rtol=2e-4`. Isso verifica conversão/pooling, **não execução GPU**. IR dos outros modelos e hardware Gen9 permanecem sem validação física; o caminho de preparação é genérico e disponível, mas esses resultados não foram inventados.

## Seleção, rollback e compatibilidade

Dashboard: instalar/verificar cada modelo, preparar IR quando desejado, escolher modelo e processador, aplicar com confirmação. Só Decision é reconfigurado; os demais módulos não reiniciam. A API exige identidade, integridade e smoke antes de persistir. Troca durante inferência é recusada como ocupado. Falha restaura seleção anterior; se a recuperação também falhar, estado/log indica indisponibilidade, sem afirmar carga.

Seleção fica em **`decision-model-policy.json` v1**, separado de `modules.json` v2 e de `decision-policy.json` v1 (algoritmo). O app anterior continua lendo as opções antigas. Defaults E5 Small/CPU e seleções salvas são preservados; política nova corrompida recupera esses defaults com erro visível. Salvamento de modelo/backend pela rota genérica é recusado: deve passar por aplicação verificada. Novo HACS omite o modelo fixo na produção e usa a seleção do serviço; clientes antigos ainda podem pedir explicitamente E5 Small.

Serviço loopback 11557: `/status`, `/infer`, `/install`, ações `install`, `prepare`, `apply`, `load`, `unload`, `readiness`, `calibrate`, `decision`, `compare`. App: `/api/decision/models`, `install`, `prepare`, `apply`. Limites anteriores de catálogo/texto/corpo preservados; modelo/backend são allowlists fechadas. HA nativo usa WebSocket admin autenticado, endpoint configurado e transporte interno protegido. Externos/Origin/CSRF inválidos não alteram configuração.

Um modelo/backend residente por Encoder. Troca limpa vetores de descritores/consultas. Mudança de algoritmo também limpa cache. A assinatura inclui manifest/revisão/hash/tokenizer/perfil, runtime, precisão, backend e, em GPU, conversor e dispositivo/driver. Versões de bibliotecas são fixas por processo e consultadas uma vez; trocar bibliotecas exige reinício. IR e calibrações possuem namespaces próprios. Não se compartilha confiança CPU/GPU nem entre modelos, mesmo com equivalência numérica parcial.

Calibração estruturada preserva catálogo semântico, escopo operacional, proveniência representativa, estratos independentes e limite de Wilson. Sem artefato representativo válido, score bruto não autoriza aceitação automática. Piso efetivo de confiança **0,80** e margem **0,08**, ou valores maiores configurados. Arquivos antigos permanecem em disco e são ignorados pela assinatura nova; não são migrados como confiança válida. Readiness distingue calibração de runtime carregado; `can_accept` exige ambos. Resolução explícita/hipóteses de confirmação continuam disponíveis sem calibração neural.

## Laboratório e medições

AI Lab oferece matriz de até três modelos × dois backends, mesmos exemplos/rótulos, warm-up 0–2, repetições 1–5, até 20 exemplos e 60 medições por combinação. Execução sequencial, limite global e restauração do residente anterior. Comparação nunca fornece token nem executa ação HA. Uma seleção de teste isolado não promove modelo à produção; aplicar ao Assist exige confirmação separada.

Mostra estado efetivo, dimensão, uso ou não de embeddings, cache hit/miss, carga, tempos de tokenização/embeddings/ranking, latência total/percentis, CPU e RSS amostrado. GPU/VRAM/utilização não medidas aparecem ausentes, não como zero. O fast-path não é chamado de inferência GPU. Ordenação por qualidade, p95 ou RAM; exports JSON/CSV de métricas não contêm falas, catálogo, nomes ou IDs residenciais e não criam histórico automático adicional. CSV protege fórmulas.

Rótulos parciais medem apenas campos fornecidos, com denominadores próprios. Plano completo exige os quatro campos de referência ou recusa rotulada sem alvos. Sem rótulo não há acurácia. Casos determinísticos/recusas e requisições com embeddings são contados separadamente. Uma matriz de comandos explícitos pode medir majoritariamente a política determinística; não prova qualidade neural.

Resultados, matriz de seis combinações, percentis, amostras, baseline real, equivalência e testes executados: **[PHASE-3.1-VALIDATION.md](PHASE-3.1-VALIDATION.md)** e **[evidências JSON](evidence/decision-multimodel-phase31.json)**. Não houve calibração de confiança com fixtures nem promoção de outro default. Medições locais não são números da HD 530/i5-6500T nem corpus representativo de fala.

## Handoff para integração

1. Revisar/mesclar a Fase 3.1 sobre a Fase 3 já integrada. Rebase/reconciliar a Fase 3.2 sobre esse resultado, preservando ambos os conjuntos de opções/endpoints.
2. Conciliar versões globais e descrições; Decision propõe 3.0.0. Sincronizar app/runtime/HACS/documentação na release combinada. Não reaproveitar calibrações anteriores para assinatura nova.
3. Validar em HAOS/HD 530: `/dev/dri`/permissões, OpenCL legado, enumeração OpenVINO, preparação IR, compilação GPU exclusiva, inferência numérica, RAM e A/B com rótulos reais. Se falhar, manter CPU e o motivo visível. Só depois avaliar promoção/benefício GPU.
4. Publicar a release combinada conforme AGENTS e iniciar Fase 4 apenas após integrar/validar as duas branches.

Superfícies compartilhadas tocadas: `README.md`, `arandu_stack/CHANGELOG.md`, `arandu_stack/config.yaml` (somente descrição/video, versão preservada), `arandu_stack/Dockerfile`, `arandu_stack/app/{module_worker.py,modules.py,settings.py,server.py,static/app.js}`, `custom_components/local_nlu/{ai_lab.py,ai_lab_core.py,ai_lab_panel.js,intelligence.py,strings.json,translations/en.json,translations/pt.json,translations/pt-BR.json}`. São os pontos a reconciliar com a Fase 3.2. Demais alterações são componente Decision, testes e documentação específicos.

**Não tocados:** gramática/parser Rust, scheduler, alarme/lembrete/timer, `contextual_runtime.py`, `contextual_protocol.py`, `capabilities.py`, `config_flow.py`, manifest HACS, `APP_VERSION`, root `AGENTS.md`, fontes STT/LLM/TTS/Wake. Nenhum merge/tag/release foi executado por esta tarefa.
