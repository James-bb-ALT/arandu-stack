# STT multimodelo e laboratório de voz

App **4.0.0**, STT **4.0.0**, integração HACS **5.0.0**. Nova etapa de release pela inclusão de funcionalidades, conforme `AGENTS.md`. NLU, Decision, LLM, Wake e TTS mantêm suas versões.

## Modelos e licenças

| ID | Motor e origem dos pesos | Licença dos pesos |
|---|---|---|
| `fastconformer`, `adapted` | [Arandu FastConformer adaptado](https://github.com/jaimevictor/arandu-stack/releases/tag/stt-model-v2.2.0), derivado de [NVIDIA](https://huggingface.co/nvidia/stt_pt_fastconformer_hybrid_large_pc) | CC BY-NC 4.0, **não comercial**; atribuição NVIDIA e Arandu |
| `fastconformer`, `original` | [Conversão sherpa-onnx INT8](https://huggingface.co/csukuangfj/sherpa-onnx-nemo-stt_pt_fastconformer_hybrid_large_pc-int8) | CC BY-NC 4.0, **não comercial** |
| `whisper_tiny` | [Systran/faster-whisper-tiny](https://huggingface.co/Systran/faster-whisper-tiny) | MIT |
| `whisper_base` | [Systran/faster-whisper-base](https://huggingface.co/Systran/faster-whisper-base) | MIT |
| `whisper_small` | [Systran/faster-whisper-small](https://huggingface.co/Systran/faster-whisper-small) | MIT |
| `parakeet_ptbr_30s` | [alefiury/parakeet-tdt-v3-ptBR-30s-onnx](https://huggingface.co/alefiury/parakeet-tdt-v3-ptBR-30s-onnx), FP32, onnx-asr | CC BY 4.0 |
| `parakeet_tagarela` | [alefiury/parakeet-tdt-0.6b-v3-ptBR-TAGARELA-onnx](https://huggingface.co/alefiury/parakeet-tdt-0.6b-v3-ptBR-TAGARELA-onnx), FP32, onnx-asr | CC BY 4.0 |
| `orukeet_int8` | [oruk/orukeet](https://huggingface.co/oruk/orukeet), transducer INT8, sherpa-onnx | CC BY-SA 4.0 |
| `granite_turboctc_q4` | [Conversão GGUF multilíngue](https://huggingface.co/vladers/granite-speech-5.0-470m-turboctc-GGUF) do IBM Granite Speech 5, Q4_K_M | Apache-2.0 |

Runtimes: [faster-whisper](https://github.com/SYSTRAN/faster-whisper) e [CTranslate2](https://github.com/OpenNMT/CTranslate2) (MIT), sherpa-onnx (Apache-2.0). Os pesos não acompanham o Git nem a imagem. As URLs, hashes e diretórios do FastConformer adaptado foram preservados.

O ambiente virtual STT é isolado dos pacotes globais e não instala PyTorch. faster-whisper 1.2.1 e CTranslate2 4.6.0 são fixados; setuptools 80.9.0 mantém a compatibilidade com `pkg_resources`.

Whisper usa CPU INT8, português, uma transcrição por vez, beam 1, temperatura zero, sem histórico de texto, timestamps de palavras ou VAD adicional. Padrão de duas threads; ajuste entre uma e quatro. O adaptado permanece FP32, como a implementação anterior.

## Novos motores: instalação e runtime

Os oito IDs usam as mesmas rotas, seletores, cache e aplicação confirmada. A configuração antiga `whisper_profile` continua sendo o perfil de produção dos motores de vocabulário aberto; seu nome foi preservado para compatibilidade. Configurações antigas não são substituídas nem promovidas automaticamente. Apenas FastConformer usa `model_variant=adapted|original`.

| ID | Download previsto | Revisão dos pesos |
|---|---:|---|
| `parakeet_ptbr_30s` | 2.550 MB | `ba4da45f60395f13b12da197d3448e66f6ec79c7` |
| `parakeet_tagarela` | 2.550 MB | `47586335c0b00ba273205f0ed3ef5a2cd651c143` |
| `orukeet_int8` | 487 MB; 672 MB extraídos | `55a984d46f68323301837194ce647c702f55facc` |
| `granite_turboctc_q4` | 279 MB | `88845afaa7beadca4b9ec545df0dd7332349ab88` |

MB decimais. O catálogo expõe tamanho previsto, arquivos obrigatórios, arquitetura, runtime, licença, revisão, hashes conhecidos, tamanho instalado e prontidão. A interface mostra o download antes da instalação. Pré-verificação reserva download + arquivos instalados + 256 MB; uma substituição conserva a cópia anterior até a promoção. Falhas preservam os outros modelos.

Os dois Parakeet incluem `encoder-model.onnx.data` (2,435 GB), encoder, decoder/joint, configuração, vocabulário e frontend. Compartilham **onnx-asr 0.12.0**, ONNX Runtime CPU já usado na stack e preprocessamento NumPy. A resolução é explicitamente offline na carga, com threads intra-op configuradas e inter-op 1. TDT não usa prompt de idioma: o argumento `language` documentado pelo onnx-asr é destinado a Whisper/Canary; não é necessário passá-lo ao TAGARELA. Não instala NeMo, Torch ou Transformers no ambiente STT. [API do runtime](https://github.com/istupakov/onnx-asr/tree/v0.12.0).

Orukeet usa `OfflineRecognizer.from_transducer`, `model_type=nemo_transducer`, features 128, greedy CPU. O pacote fornecido é fixado por revisão e SHA256 `f9191f30178cc9122ce2f023bf9fefafc822028307b0efa4caff645ba3fe8d0a`. Antes da extração, verifica o pacote completo; só copia arquivos obrigatórios regulares, rejeita caminhos absolutos/traversal, links, duplicatas e tamanho descomprimido excessivo. Conserva `LICENSE-WEIGHTS` e `NOTICE.md`. Não usa a API CTC do FastConformer. [Documentação da exportação](https://github.com/Oruk-AI/orukeet/blob/main/export/onnx/README.md).

Granite baixa **somente Q4_K_M**, LICENSE e NOTICE. O GGUF aceito tem SHA256 `959646de88d43100a82459da1c0c9d3a2b1a7536b54338b9c4fc9535bdb30970` na revisão multilíngue acima. A carga exige `pt` nas capacidades do motor; conversão somente inglesa é recusada. Q8_0 não é uma opção nesta release: não houve avaliação humana que justificasse mais 506 MB.

A imagem compila [transcribe.cpp](https://github.com/handy-computer/transcribe.cpp/tree/5700f127e38dd53c4735ea23ef743b4b538eacd4) em revisão `5700f127e38dd53c4735ea23ef743b4b538eacd4`, binding **0.4.1** e bibliotecas CPU compartilhadas, com configuração x86 conservadora. Instala o binding construído da mesma revisão, sem buscar outro provider binário, e define `TRANSCRIBE_LIBRARY` para a biblioteca compilada. O adaptador mantém **Model + Session persistentes** no Wyoming; carrega uma vez e mede inferência aquecida separadamente. Não inicia CLI por frase nem acrescenta servidor público. O laboratório destrói seu processo temporário após cada modelo, preservando a produção. O build compila código, nunca baixa pesos. Os Dockerfiles integrado e independente incluem o mesmo runtime.

A instalação muda de Baixando para Verificando quando começa a validação dos arquivos; o worker verifica hashes e carrega o candidato antes de promover seu diretório e registrar `load_validated`. Instalado indica cache disponível; Pronto indica o motor ativo confirmado. O custo de iniciar processo, imports e hashes continua no total do teste, separado de `model_load_ms` e `inference_ms`.

Comparação aceita **1–8 modelos distintos**, um processo temporário por vez, 90 s por modelo; transporte HA limitado a 780 s para acomodar oito. Upload/conversão continuam limitados a 12 s e 1,4 MB. Apenas FastConformer e Whisper Base começam marcados na comparação; marcar os demais não baixa pesos. Modelos ausentes retornam erro individual sem impedir os instalados.

## Instalar e selecionar

1. Atualize o app e a integração HACS; reinicie o Home Assistant para registrar o módulo frontend novo.
2. Em Arandu Assistant, abra **STT → Configurar**. Selecione o motor e, para FastConformer, a variante. Em **Pesos e modelo ativo**, clique em **Baixar**. A instalação é explícita; `auto_download=false` continua padrão na stack.
3. Aguarde **Instalado**. Use **Salvar e reiniciar** e confirme a troca. A aplicação carrega/verifica o candidato antes da troca, reinicia somente STT, consulta a identificação real e verifica Wyoming. Em caso de falha, restaura configuração e processo anteriores.

Salvar sem reiniciar mantém uma configuração pendente. **Modelo ativo** mostra o processo realmente carregado, distinguindo a seleção salva. Configurações antigas sem `stt_model` usam `fastconformer` com a variante existente. Não há promoção automática para outro motor.

O cache fica em `/data/modules/stt/models/`; no app STT independente, em `/data/models/`. Instalação Hugging Face usa `snapshot_download` somente os arquivos necessários de cada motor, fixa o commit SHA resolvido e grava hashes SHA256, origem e revisão no manifesto. Os arquivos LFS são comparados com SHA256 de origem quando disponível. A carga confere os hashes sem acessar a rede. Instalações FastConformer antigas sem manifesto podem ser adotadas offline; a revisão de origem do original legado é indicada como desconhecida, nunca inventada. Downloads usam staging e trava entre processos; uma falha preserva a instalação anterior. Uma substituição interrompida é recuperada de `.previous` na próxima verificação.

Estados: `not_installed`, `downloading`, `verifying`, `installed`, `loading`, `ready`, `error`. Falhas permitem nova tentativa. Não informe URLs arbitrárias: o catálogo é fechado.

## Gravar e comparar dentro do Home Assistant

Abra **Arandu AI Lab → STT**. O botão **Testar STT** do app abre `/arandu-ai-lab#stt` na janela principal do HA. As abas Testar, Comparar e Avançado permanecem disponíveis.

1. Clique em **Gravar áudio**, autorize o microfone, fale e clique em **Parar gravação**. HTTPS ou localhost é necessário. A captura termina automaticamente antes de 12 segundos e libera as tracks ao parar, sair da aba ou desconectar o painel.
2. Ouça no player. Como alternativa, envie WAV, WebM/Opus, Ogg ou MP4/AAC, até 1,4 MB e 12 segundos. Arquivos estéreo de alta taxa podem atingir o limite de bytes antes do limite de duração.
3. Selecione um modelo para transcrever ou marque vários e clique em **Comparar modelos**. A conversão FFmpeg ocorre uma vez para PCM16 mono 16 kHz. Todos os modelos recebem exatamente os mesmos bytes; o SHA256 do PCM aparece no resultado.
4. Opcionalmente informe **Texto que foi falado** para WER/CER. Para verificar o alvo, informe **Entidade falada** como trecho exato, por exemplo `luz da sala`. Essa métrica compara texto anotado; não significa resolução de uma entidade HA.
5. Exporte JSON ou CSV. **Limpar histórico** remove as medições em memória. Áudio e transcrições não são persistidos pelo laboratório; exportações exigem clique explícito.

Downloads não começam durante comparações. Modelos temporários são avaliados sequencialmente e encerrados ao terminar. O modelo de produção permanece em seu processo. O teste STT não chama NLU, Decision, LLM nem serviços domésticos. Para promover o vencedor, escolha-o no seletor e use **Usar este modelo no Assist**, revisando a confirmação com modelo atual e candidato.

## Correção lexical e segurança de comandos

FastConformer mantém seu `ContextualResolver`, inventário e opções históricas. Whisper e os quatro novos motores usam `normalize_only` e vocabulário aberto por padrão; `raw` e `full` também estão disponíveis, sem reinstalação. O perfil experimental de uma comparação não muda o perfil de produção.

Uma correção lexical que altera a presença de `não` ou troca uma ação explícita ligar/desligar é descartada e mantém o RAW. Isso protege contra alterações do corretor; **não comprova que o modelo acústico reconheceu corretamente a negação**. Compare com áudio humano e gabarito antes de escolher o motor. A confiança lexical continua heurística, sem probabilidade acústica calibrada.

## Transporte e limites

O painel usa o WebSocket autenticado do HA e exige administrador ativo. `ai_lab.py` encaminha somente ao `stack_endpoint` da entrada configurada, com validação local, DNS fixado e sem redirects. O parâmetro legado `stt_endpoint` não é usado.

Contrato no controle existente:

| Endpoint | Operação |
|---|---|
| `GET /api/stt/models?variant=adapted` | Catálogo, instalação, candidato salvo e modelo ativo |
| `POST /api/stt/install` | Inicia instalação explícita; acompanhe pelo catálogo |
| `POST /api/stt/test` | Um modelo, áudio Base64 e referência opcional |
| `POST /api/stt/compare` | Lista de até oito modelos, mesmo áudio |
| `POST /api/stt/apply` | Candidato e `confirm=true`; pré-validação, reinício e rollback |

Ingress mantém CSRF. Chamadas internas exigem simultaneamente origem de rede Supervisor/Core permitida, ausência de `Origin` e marcador de laboratório; o cabeçalho sozinho não autoriza acesso externo. O serviço de diagnóstico STT em 11560 é somente loopback, sem porta publicada. Não existe dependência da porta legada 8765 nem endpoint público de áudio.

Há uma operação de laboratório por vez, sem fila crescente; entradas concorrentes recebem erro. Uploads ficam abaixo de 2 MiB incluindo Base64/envelope. FFmpeg valida conteúdo, bloqueia protocolos remotos, limita threads/alocação/saída, tem timeout de 15 s e watchdog de RSS de 512 MiB no Linux. Processos de teste têm timeout de 90 s e limite monitorado de RSS de 3 GiB no Linux (4 GiB para Parakeet FP32). Instalação tem timeout de 900 s. O carregamento de produção recusa RSS acima do mesmo orçamento por modelo. Inferência Wyoming tem prazo de 60 s; após expiração, a operação nativa já iniciada pode terminar em segundo plano, mas a trava impede novas inferências concorrentes naquele motor. Um runtime travado pode exigir reinício STT; o laboratório temporário é encerrado pelo supervisor de processo.

STT continua em **10350**, gateway em **10212**. O identificador Wyoming público `arandu-fastconformer-ptbr-int8` é preservado para clientes existentes. A descrição e os diagnósticos revelam o motor real, variante, revisão, threads, memória e carregamento. O contrato com o NLU não muda.

## Métricas

Resultados incluem `audio_duration_ms`, `audio_decode_ms`, `model_load_ms`, `queue_ms`, `inference_ms`, `lexical_ms`, `request_total_ms`, `realtime_factor`, `process_rss_mb`, RAW e FINAL. Inferência Whisper consome todos os segmentos antes de parar o cronômetro. Carregamento mede o construtor do modelo; imports, verificação e criação do processo também aparecem no total, sem serem atribuídos à inferência.

`stop_to_transcript_ms` é medido no navegador desde Parar até a resposta. Inclui espera humana antes de enviar, upload, rede, HA e processamento. `browser_wait_before_send_ms` separa essa espera; `browser_request_ms` mede o envio/resposta. A diferença para o total no Arandu é apresentada como transporte/orquestração, sem afirmar precisão de um relógio de rede isolado. Upload de arquivo não possui instante de fim da fala: esse campo fica nulo.

WER/CER usam caixa e acentos normalizados, pontuação removida. Ação e negação são verificações textuais, não uma execução NLU. Sem referência, qualidade fica **Não avaliado**. Mediana é calculada por modelo com medições reais, mostrando `n`. P95 fica indisponível até 20 amostras. Comparações usam processos temporários; tempos de carga e de inferência permanecem separados.

## Verificação reproduzível

Testes comuns não baixam pesos:

```powershell
python -m pytest tests -q
python -m pytest arandu_stack/rootfs/modules/arandu_stt/rootfs/src/arandu_stt/tests -q
node --test tests/test_ai_lab_panel.js
```

Use Python com as dependências STT instaladas para os testes acústicos. Com modelos previamente instalados e um WAV local:

```powershell
$env:ARANDU_STT_PYTHON = 'CAMINHO_DO_PYTHON_STT'
$env:ARANDU_STT_TEST_DATA = 'CAMINHO_DA_PASTA_DATA'
$env:ARANDU_STT_TEST_WAV = 'CAMINHO_DO_AUDIO_WAV'
$env:ARANDU_STT_TEST_MODELS = 'whisper_base,whisper_tiny,whisper_small,fastconformer'
python -m pytest tests/test_stt_real.py -q
python tests/stt_browser_check.py
docker build -t arandu-assistant:4.0.0 ./arandu_stack
```

O teste de navegador requer Playwright/Chromium e usa a porta local 8770. Testa o componente nativo com API e pesos reais através de uma fixture local; autenticação/permissão HA tem testes separados. Isso não substitui instalação no HA Core. A suíte acústica verifica operação offline, silêncio, liberação de processos e transcrição pelo Wyoming com o ID legado.

### Medições históricas da release v4.0.0 (10/11/2026)

Uma amostra de 2.496 ms produzida **sinteticamente** pelo Microsoft Maria Desktop PT-BR, “Liga a luz da sala”, em AMD Ryzen 7 5700X, Windows/Python 3.13, CPU INT8 nos Whispers, duas threads. Não é voz humana, benchmark no i5-6500T, avaliação geral de qualidade nem comprovação das metas de mediana/P95. Havia outras tarefas de desenvolvimento na máquina.

| Modelo | Carga (ms) | Inferência (ms) | Resultado RAW |
|---|---:|---:|---|
| FastConformer adaptado FP32 | 1528,7 | 74,0 | Liga a luz da sala |
| Whisper Tiny | 220,0 | 386,4 | Liga a luz da sala. |
| Whisper Base | 380,5 | 781,3 | Liga a luz da sala. |
| Whisper Small | 1145,1 | 2540,1 | Liga a luz da sala. |

FastConformer original INT8 também foi instalado e transcreveu o mesmo PCM em teste separado: carga 1484,7 ms, inferência 84,6 ms, WER RAW 0,2 e FINAL normalizado 0.

Um resultado por modelo; não há P95. Os quatro receberam o mesmo SHA256 PCM. WER/CER normalizados foram zero nessa única frase. Precisão de voz humana, negações, entidades e latência no equipamento de produção continuam exigindo gravações reais no painel.

### Verificação da stack Linux

`tests/stt_docker_check.py` testa a dashboard real e o app em um container Linux isolado, com dados de validação e pesos locais: aplicação de Base, comparação de oito motores (cinco instalados e três erros individuais por ausência de pesos) enquanto o Wyoming continua atendendo, instalação pelo cache, aplicação real de Orukeet, recuperação de candidato ausente e troca Tiny/Base. Use somente uma instância descartável, `ARANDU_LOCAL_CONTAINER=1` e portas publicadas em `127.0.0.1`; o script pode mudar a configuração dessa instância. O build final usa Python 3.12 e ambiente STT sem pacotes globais.

As regressões Rust NLU disponíveis (`--lib`, `contextual`, `http`, `v2_resolve`, `v2_interpret_http`, `v3_interpret_http`) foram executadas offline no estágio `nlu_builder`, montando `rootfs/src/data` em `/data`: **84 testes passaram**. A suíte completa foi tentada, mas este snapshot não contém `data/mlp/project-authored-synthetic-v1.jsonl` nem os corpora `evaluation/ptbr-independent` exigidos por outras suítes. Esses dados não foram recriados nem tratados como regressões aprovadas.

O Home Assistant local respondeu HTTP 401 em `/api/`; este ambiente não fornece credencial administrativa HA. Registro do painel, restrição de administrador, WebSocket e encaminhamento têm testes automatizados, mas instalação no Core e captura do microfone físico não foram validadas nessa instância.

## Troubleshooting e rollback

- **Painel não aparece:** atualize HACS 5.0.0, reinicie HA e confirme acesso administrativo. Recarregue a página para carregar `stt.js` novo.
- **Configure o endpoint:** nas opções da integração, use o hostname interno do app e porta 8099. `127.0.0.1` do Core não aponta automaticamente ao app.
- **Não instalado / integridade inválida:** use Baixar novamente. Confira conectividade apenas durante instalação. Não edite manifestos para ignorar hashes.
- **Microfone indisponível:** confirme HTTPS, permissões e suporte MediaRecorder; upload continua disponível. MP3 não faz parte da allowlist inicial.
- **Falha de áudio:** use até 12 s e 1,4 MB. Arquivo renomeado para WAV não converte seu conteúdo. Detalhes privados e áudio não são registrados em logs de erro.
- **Ocupado / timeout:** aguarde a operação ou reinicie somente STT. Não inicie vários downloads ou comparações paralelas. Small pode ser lento no i5-6500T.
- **Rollback manual:** selecione `fastconformer` e a variante anterior instalada e confirme aplicar. Configuração anterior fica em `stt-last-good.json`; os pesos antigos são preservados. Em downgrade do app, remova os campos novos da configuração se a versão antiga rejeitar opções desconhecidas, mantendo cópia de segurança.

O resultado de instalação em Home Assistant OS, o microfone físico do usuário e a validação humana no i5-6500T devem ser registrados separadamente; testes locais e sintéticos não os substituem.

## Validação desta extensão

Consulte [registro de validação da extensão](STT-EXTENDED-VALIDATION-2026-11-10.json) e [release v5.0.0](RELEASE-v5.0.0.md). Apenas **Orukeet** foi baixado nesta tarefa; FastConformer e Whisper reutilizaram caches existentes. Parakeet 30s, TAGARELA e Granite têm adaptadores/instalação/testes de contrato, mas **não tiveram inferência comprovada com seus pesos** nesta entrega. O runtime Granite compilado foi importado em Linux e identificou CPU, sem Torch no venv STT.

O áudio técnico disponível é sintético, não gravação humana. Não há classificação de precisão entre os seis candidatos, avaliação de negações/dispositivos humanos, P95 ou promessa de latência no i5-6500T. Foram medidas 1, 2 e 4 threads para FastConformer, Base e Orukeet no Ryzen local; os tempos estão no registro e não substituem avaliação no hardware de produção. A comparação com gravação humana fica disponível no AI Lab, com métricas e exportação.
