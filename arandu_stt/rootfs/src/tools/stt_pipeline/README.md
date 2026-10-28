# Pipeline offline Arandu STT

Ferramentas independentes para descobrir gravações, preparar e validar datasets NeMo, realizar fine-tuning consolidado, comparar modelos e produzir uma exportação revisável. Nenhum comando altera o servidor Wyoming, o Home Assistant ou os pesos em produção. Preparar dados nunca inicia treinamento.

Consulte [resultados executados e limitações desta entrega](VERIFICATION.md) para o dataset real, testes e linha de base acústica.

**Atualização:** fine-tuning na RTX e app de comparação foram executados posteriormente. Veja [experimento real, métricas finais e app local](../comparison_app/README.md). O registro abaixo sobre ausência de NeMo se refere à preparação inicial; o treinamento posterior usou Docker Linux.

## Instalação

Execute os comandos abaixo na raiz **`arandu-stt-github`**, o checkout com remote `jaimevictor/Arandu-STT`. Python 3.10+ e FFmpeg/FFprobe no PATH bastam para preparar datasets e executar os testes. A cópia antiga da pasta superior foi arquivada; o workspace local aponta para este checkout canônico.

Windows PowerShell:

```powershell
winget install --id Gyan.FFmpeg -e
python -m venv .venv-dataset
.\.venv-dataset\Scripts\Activate.ps1
ffmpeg -version
ffprobe -version
python -m unittest discover -s tools/stt_pipeline/tests -v
```

Linux Debian/Ubuntu:

```bash
sudo apt-get install ffmpeg python3-venv
python3 -m venv .venv-dataset
source .venv-dataset/bin/activate
python -m unittest discover -s tools/stt_pipeline/tests -v
```

Para **avaliação ONNX**: `python -m pip install -r tools/stt_pipeline/requirements.txt`.

Para **NeMo**, use ambiente separado em Linux/WSL2 com GPU NVIDIA. NeMo não é suportado nativamente pelo pipeline de dependências no Windows. A preparação, inspeção, validação e avaliação ONNX funcionam no Windows e no Linux. Em WSL, prepare novamente o dataset para gerar caminhos Linux; não use manifests com caminhos `D:\...` no Linux.

O ambiente de treino/exportação fixa **PyTorch/torchaudio 2.8.0** para manter o exportador ONNX usado pelo NeMo inspecionado. Instale o par CUDA conforme os [comandos oficiais da versão](https://pytorch.org/get-started/previous-versions/), em ambiente separado:

```bash
python -m pip install torch==2.8.0 torchaudio==2.8.0 --index-url https://download.pytorch.org/whl/cu128
python -m pip install -r tools/stt_pipeline/requirements-training.txt
python -c "import torch; print(torch.cuda.is_available())"
python -m pip freeze > training-environment.lock.txt
```

Para exportar: `python -m pip install -r tools/stt_pipeline/requirements-export.txt`.

O adaptador usa a API inspecionada do **NeMo 2.5.3**, com **Lightning 2.4.0**, exigida por essa versão. Essas dependências ficam fora do App Home Assistant. O lock do ambiente, a revisão do checkpoint, os hashes dos dados e a configuração do treino permitem reproduzir o experimento. Igualdade bit a bit entre GPUs, versões CUDA e sistemas não é garantida.

## 1. Inspecionar e preparar

Organização recomendada:

```text
D:/Audios/
  participante_01/
    roteiro.txt
    01.m4a.mp4
    02.ogg
  participante_02/
    roteiro_b/
      roteiro.txt
      01.mp4
      03.wav
```

A primeira pasta relativa à entrada identifica o participante. Subpastas podem identificar roteiros. Áudio e TXT devem compartilhar uma pasta ou o TXT deve estar em uma pasta ancestral dentro do participante. Havendo mais de um TXT candidato, informe `script` no mapeamento. Arquivos diretamente na entrada recebem o nome da entrada como participante. A quantidade de participantes e de frases é livre; não existe uma exigência fixa de sete.

```powershell
python tools/prepare_dataset.py inspect --input "D:\Audios" --output "./inspection"
python tools/prepare_dataset.py --input "D:\Audios" --output "./dataset" --seed 42 --ratios 0.8 0.1 0.1 --log-file "./pipeline.log"
python tools/prepare_dataset.py validate --dataset "./dataset"
```

`prepare` também aceita subcomando explícito. Entrada e saída devem ser separadas: nenhuma pode conter a outra. Isso evita converter originais ou redescobrir os WAVs gerados. Links simbólicos de arquivos são ignorados; arquivos acessados fora da entrada são rejeitados. Arquivos conhecidos de documentação/imagem/configuração são ignorados; demais candidatos são inspecionados pelo conteúdo com FFprobe, mesmo se a extensão estiver incorreta.

Cada áudio recebe um WAV **PCM signed 16-bit little-endian, mono, 16 kHz**, efetivamente decodificado. Não há redução de ruído, corte de fala, VAD ou normalização de volume. A duração vem do número real de amostras após conversão. Limites padrão: **0,15–35 s**, configuráveis por `--min-duration` e `--max-duration`. Silêncio completo é rejeitado; silêncio parcial não é removido. Uma gravação de silêncio com ruído baixo pode exigir revisão humana.

FFprobe registra contêiner, codec, canais, sample rate, duração declarada e índice do fluxo. Se houver vários fluxos, usa o único default; sem default inequívoco, exige `stream_index`. WAVs são lidos integralmente e decodificados novamente com FFmpeg. Falhas, arquivos vazios, duração anormal e associações duvidosas ficam fora dos manifests.

Use `--ffmpeg "C:/.../ffmpeg.exe" --ffprobe "C:/.../ffprobe.exe"` se não estiverem no PATH. Todas as operações aceitam `--verbose` e `--log-file`.

### Roteiros e correções

O parser suporta `1.`, `1)`, `01 -`, zeros iniciais e frases sem numeração. Preserva texto, acentos e pontuação; remove somente espaços nas bordas da linha e o marcador numérico. Cada frase deve ocupar uma linha. UTF-8/BOM é padrão; outro encoding precisa ser declarado.

Cabeçalhos explícitos (`#`, `Roteiro`, `Seção`, `Bloco`, linhas terminadas em `:` etc.) são excluídos. Linhas que parecem títulos, linhas em maiúsculas ou sem pontuação final são consideradas ambíguas. O roteiro fica bloqueado até revisão. Essa política conservadora também pode sinalizar frases legítimas; `include_lines` corrige isso. Numeração duplicada ou mistura de frases numeradas e não numeradas exige corrigir o TXT ou fornecer transcrições humanas explicitamente.

Em roteiros numerados, o identificador do áudio corresponde ao número escrito. Em roteiros sem numeração, corresponde à ordem das frases, começando em 1. Por exemplo, áudio `03.wav` continua associado à terceira frase se `02.wav` estiver ausente. Não compactamos identificadores. Se um áudio ultrapassar o número de frases de um roteiro sem numeração, bloqueamos todas as associações automáticas desse roteiro: pode haver uma frase ausente no meio. Nomes sem um único grupo numérico exigem mapeamento; a ordenação natural ajuda a inspeção, mas não inventa uma correspondência confiável.

Crie um JSON fora da entrada (adapte os caminhos, que usam `/`, e os números de linha, que começam em 1):

```json
{
  "version": 1,
  "scripts": {
    "participante_01/roteiro.txt": {
      "encoding": "utf-8-sig",
      "exclude_lines": [1, 7],
      "include_lines": [3]
    }
  },
  "audio": {
    "participante_01/01.m4a.mp4": {
      "script": "participante_01/roteiro.txt",
      "phrase_id": 1,
      "stream_index": 0
    },
    "participante_01/01_copia.ogg": {"skip": true}
  }
}
```

```powershell
python tools/prepare_dataset.py prepare --input "D:\Audios" --output "./dataset" --mapping "./mapping.json"
```

Para nomes como `roteiro2_frase3.mp4`, especifique `phrase_id`. Para corrigir uma transcrição humana, a entrada de áudio admite `text`, junto com `script` e `phrase_id`. Não existe transcrição automática nem inferência de texto ausente. `mapping.example.json` exemplifica o formato; seus caminhos precisam ser adaptados.

Duplicatas de participante/roteiro/frase são todas rejeitadas, em vez de escolher silenciosamente uma. Conteúdo PCM idêntico também rejeita todos os envolvidos, mesmo com nomes, participantes ou transcrições diferentes. Use `skip` para selecionar a gravação correta. Gravações repetidas reais, com PCM diferente, permanecem válidas e ficam agrupadas pela frase na divisão.

A associação por números não prova que a pessoa leu a frase correta. Erros de gravação, títulos não identificáveis e omissões em um roteiro sem numeração que não alteram as contagens podem ser indetectáveis sem escuta humana. Revise os metadados e a inspeção antes de treinar.

### Saída, validação e códigos de retorno

```text
dataset/
  audio/<participante_com_hash>/<participante>__<roteiro>__0001.wav
  manifests/train.jsonl
  manifests/validation.jsonl
  manifests/test.jsonl
  metadata.jsonl
  reports/validation.csv
  reports/errors.csv
  reports/statistics.json
  reports/conversion_cache.json
  reports/dataset_validation.json
  reports/release.json
```

Exemplo de entrada NeMo (caminho absoluto ilustrativo):

```json
{"audio_filepath":"D:/dataset/audio/participante_01_abcd1234/exemplo__0001.wav","text":"Acenda a luz da cozinha.","duration":2.4,"speaker":"participante_01","id":"participante_01__roteiro_a__0001"}
```

NeMo usa `audio_filepath`, `text` e `duration`; `speaker` e `id` preservam a identidade. Originais, linha do TXT, identificador de frase, fluxo, codec, hashes de origem/WAV/PCM e split ficam em `metadata.jsonl`. Os hashes incluídos no nome evitam colisões de nomes de pastas/roteiros.

Validação verifica conteúdo decodificável, formato PCM, parâmetros, duração, caminhos, transcrição, IDs, hashes, duplicatas, metadados e ausência de vazamento entre conjuntos. `release.json.ready=true` significa que **os exemplos incluídos** passaram. Erros de fontes excluídas ainda podem constar no relatório. Treinar repete a validação e exige conjuntos train/validation não vazios. Preparação interrompida invalida o release e exige nova execução; dados anteriores não são liberados silenciosamente.

Retornos: `0` sucesso; `1` falha de execução/dependência/configuração; `2` preparação com pendências, validação reprovada ou exportação reprovada. Preparação pode retornar `2` e produzir um subset válido: examine `statistics.json`, `errors.csv` e `release.json` antes de usá-lo. Datasets insuficientes para a divisão geram manifests vazios, metadados dos exemplos convertidos e release bloqueado.

Conversão incremental usa SHA-256 do original, stream, sample rate e SHA-256 do WAV existente. Alteração de áudio, seleção de stream ou WAV corrompido provoca nova conversão. Reexecutar sem mudanças preserva os WAVs e seus mtimes; manifests e splits mantêm conteúdo determinístico. Correções de TXT/mapeamento refazem associação/divisão, reaproveitando a conversão. WAVs antigos não referenciados podem permanecer na saída, mas não entram no treino. Não execute dois escritores simultâneos na mesma saída.

### Divisão e participantes

Padrão `--split-mode phrase`: todas as variantes da mesma frase, de qualquer participante, ficam no mesmo split. A chave usa NFC, casefold, remoção de pontuação e espaços normalizados; o texto armazenado permanece intacto. Os pesos da divisão são aproximados porque grupos de frases não podem ser quebrados. Pelo menos um grupo é reservado para cada proporção positiva. Com 80 frases compartilhadas por sete pessoas, há aproximadamente 80 grupos, e não 560 exemplos independentes.

`--split-mode speaker` agrupa também participantes, preservando simultaneamente o isolamento de frases. Quando todos gravam os mesmos roteiros, todos os locutores ficam conectados: uma divisão por participante sem vazamento de frases é impossível. O comando reporta isso e bloqueia a liberação. Use frases distintas entre grupos de locutores se precisar desse teste. A avaliação por participante é sempre calculada no teste principal, sem um modelo separado por pessoa.

## 2. Fine-tuning

A inspeção do código `arandu_stt/model_download.py`, `server.py` e `benchmarks/model_manifest.json` identificou:

| Item | Configuração confirmada |
|---|---|
| Arquitetura | FastConformer Large híbrido Transducer + CTC, aproximadamente 115M parâmetros |
| Framework/classe | NVIDIA NeMo, `EncDecHybridRNNTCTCBPEModel` |
| Checkpoint treinável | `nvidia/stt_pt_fastconformer_hybrid_large_pc`, arquivo `.nemo` |
| Revisão fixada | `036da25fa32e3f11c11105448482845950840685` |
| Tokenizador | SentencePiece, 128 tokens, preservado no fine-tuning |
| Áudio/features | 16 kHz mono, 80 features, subsampling 8 |
| Inferência Arandu | CTC offline, sherpa-onnx 1.13.8, CPU, greedy search |
| Pesos de produção | `csukuangfj/sherpa-onnx-nemo-stt_pt_fastconformer_hybrid_large_pc-int8`, `model.int8.onnx` + `tokens.txt` |
| Revisão de produção registrada | `f5df0c1e1db4a779d106f4722886557f3466cc42` |

Fontes primárias: [model card NVIDIA](https://huggingface.co/nvidia/stt_pt_fastconformer_hybrid_large_pc), [inventário/revisão do checkpoint](https://huggingface.co/api/models/nvidia/stt_pt_fastconformer_hybrid_large_pc), [fine-tuning NeMo](https://github.com/NVIDIA/NeMo/blob/v2.5.3/examples/asr/speech_to_text_finetune.py), [métricas híbridas NeMo](https://github.com/NVIDIA/NeMo/blob/v2.5.3/nemo/collections/asr/models/hybrid_rnnt_ctc_models.py), [exportação CTC sherpa-onnx](https://github.com/k2-fsa/sherpa-onnx/blob/master/scripts/nemo/fast-conformer-hybrid-transducer-ctc/export-onnx-ctc-non-streaming.py).

O projeto baixa pesos já convertidos. Não existia um treino ou exportador próprio no checkout. O novo adaptador segue a API de fine-tuning NeMo e o contrato de exportação do sherpa-onnx. A revisão fixada baixa somente o `.nemo` quando um comando NeMo é explicitamente executado. O checkpoint INT8 de inferência não é usado para treino. Os pesos NVIDIA possuem licença **CC BY-NC 4.0**, já documentada pelo projeto.

```bash
python tools/prepare_dataset.py train --dataset ./dataset --output ./runs/ptbr_001 --config tools/stt_pipeline/training.example.json
python tools/prepare_dataset.py train --dataset ./dataset --output ./runs/ptbr_001 --config tools/stt_pipeline/training.example.json --resume ./runs/ptbr_001/checkpoints/last.ckpt
```

O JSON configura batch size, GPU/CPU, precision, acumulação, learning rate, epochs, clipping, patience, seed e congelamento opcional do encoder. A configuração inicial para **RTX 3080 de 10 GB** usa batch 1 e acumulação 8. Mixed precision é selecionada somente com CUDA: BF16 quando suportado, senão FP16; CPU usa FP32. Isso é um ponto de partida, não uma garantia de memória para qualquer duração. Em OOM, reduza a duração máxima dos dados após revisão ou use `freeze_encoder=true`; acumulação não reduz a memória de um único exemplo. O treino nativo híbrido também calcula RNNT, podendo exigir extensões CUDA do NeMo.

Treino usa dados consolidados, preserva arquitetura/tokenizador e objetivo híbrido original. Early stopping e seleção do melhor checkpoint monitoram **`val_wer_ctc`**, porque a produção usa CTC. O teste fica fora do treinamento e da seleção. `run.json` registra configuração resolvida, hashes, versões, modelo e retomada. `checkpoints/last.ckpt` retoma estados de otimizador, scheduler/loops e treino; `best.nemo` é salvo com pesos do melhor checkpoint validado. Retomada exige mesmos manifests e configuração; reiniciar fine-tuning de outro `.nemo` é um novo experimento.

## 3. Avaliação comparativa

```bash
python tools/prepare_dataset.py evaluate --dataset ./dataset --model ./runs/ptbr_001/best.nemo --baseline-model nvidia/stt_pt_fastconformer_hybrid_large_pc --output ./evaluations/ptbr_001 --device cuda
```

Também é possível comparar com o **INT8 real de produção**, informando a pasta que contém `model.int8.onnx` e `tokens.txt`:

```bash
python tools/prepare_dataset.py evaluate --dataset ./dataset --model ./runs/ptbr_001/best.nemo --baseline-model ./models/original_int8 --output ./evaluations/vs_production
```

Ambos recebem os mesmos IDs, transcrições e WAVs do mesmo `test.jsonl`. NeMo usa explicitamente CTC, não RNNT. WER/CER são taxas globais por soma de erros/referências, e também são calculadas por participante. Resultado normalizado ignora caixa/pontuação, preserva acentos, inclui espaços no CER; há resultado verbatim separado para caixa/pontuação. `*_details.json` registra frases reconhecidas, substituições, inserções, deleções e contagem de palavras incorretas. Os manifests/transcrições de treinamento continuam originais.

`comparison.json` inclui hashes do teste e modelos, métricas, latência e memória. Uma melhoria exige WER estritamente menor e CER sem regressão. Nenhuma melhoria é declarada antes da comparação. `--split validation` serve para desenvolvimento; exportação exige avaliação `test`.

`speaker_coverage` informa participantes avaliados e participantes sem exemplos no split. Grupos de frases e roteiros desiguais podem deixar alguns participantes fora do teste; não calculamos métricas fictícias para eles. Revise as contagens por participante/split ao escolher seed e proporções.

Avaliação acústica não aplica o resolver lexical, padding opcional de produção ou transporte Wyoming; essas etapas devem ser avaliadas separadamente no Home Assistant. Latência inclui processamento completo por frase, batch 1 e um warm-up excluído. RSS é amostrado após warm-up e após cada frase: mede o processo inteiro, não pico contínuo exclusivo do modelo; bibliotecas carregadas podem afetar comparações. VRAM usa pico do allocator CUDA. Comparação NeMo/GPU versus ONNX/CPU tem backends diferentes. A comparação de quantização usa FP32/INT8 ambos no mesmo runtime CPU, com igual número de threads.

Para avaliar predições previamente geradas, use JSONL `{"id":"...","text":"..."}`, inclusive texto vazio para fala não reconhecida:

```bash
python tools/prepare_dataset.py evaluate --dataset ./dataset --predictions ./candidate.jsonl --baseline-predictions ./baseline.jsonl --output ./evaluations/offline
```

Os IDs devem corresponder exatamente ao conjunto, sem duplicatas. Predições fornecidas não habilitam exportação: não comprovam a identidade do checkpoint/inferência.

## 4. Exportação e quantização

```bash
python tools/prepare_dataset.py export --dataset ./dataset --checkpoint ./runs/ptbr_001/best.nemo --comparison ./evaluations/ptbr_001/comparison.json --output ./exports/ptbr_001 --threads 2
```

Pré-condições: melhoria inferida no teste contra checkpoint NVIDIA original ou INT8 cujo hash coincide com o registrado no projeto; checkpoint candidato e hashes do teste precisam corresponder à avaliação. O destino deve estar vazio. A produção nunca é sobrescrita.

O comando exporta o ramo CTC com NeMo, valida assinatura ONNX/80 features, gera `tokens.txt` na ordem original e adiciona metadados necessários ao sherpa-onnx. Quantização dinâmica **QUInt8**, conforme o exportador upstream, gera `int8/model.int8.onnx`. Ambos os grafos passam pelo checker ONNX e são usados por `OfflineRecognizer.from_nemo_ctc`, a mesma API de produção, em todo o teste. Isso mede precisão antes/depois, latência, RTF, RSS e tamanho dos arquivos.

Tolerâncias padrão para diferenças NeMo/FP32 e FP32/INT8: **0,005** absoluto em WER e CER (0,5 ponto percentual), ajustáveis. O INT8 final ainda precisa superar o WER do original e não piorar seu CER. Regressão bloqueia `export.json.ready_for_manual_deployment`; os arquivos ficam disponíveis para inspeção. Use diretório novo para repetir a exportação.

Entrega: `fp32/`, `int8/`, `model_config.yaml`, `quantization_evaluation/` e `export.json`. Somente `int8/model.int8.onnx` + `int8/tokens.txt` formam o candidato para implantação **manual**, após backup e teste no HAOS. Há prova do runtime offline, mas não teste automático do transporte Wyoming ou da instalação no Home Assistant. O protocolo/serviço existente permanece intacto.

## Demonstração e estado de verificação

Crie mídia sintética (tons, sem fala) para verificar infraestrutura:

```bash
python -m tools.stt_pipeline.demo --output ./dist/stt_pipeline_demo
python tools/prepare_dataset.py validate --dataset ./dist/stt_pipeline_demo/dataset
python -m unittest discover -s tools/stt_pipeline/tests -v
```

O destino da demonstração deve ser novo/vazio. São sete pastas com quantidades diferentes, seis roteiros numerados, um sem numeração, OGG, MP4, extensão composta, cabeçalhos, gravação ausente, duplicatas, arquivo vazio e corrupção deliberada. A pasta `dist` não é versionada. **Não treine com a demonstração**: seus textos são fixtures de associação, os áudios não contêm essas frases.

Os testes incluem conversão/decodificação reais com FFmpeg, preservação dos originais, cache, texto/IDs, omissões, leakage, validação e métricas conhecidas. Se FFmpeg estiver ausente, testes de mídia ficam explicitamente como skipped; não interprete isso como prova de conversão.

O usuário forneceu posteriormente a pasta de gravações reais. Os resultados e mapeamentos ficam em `inspection/real/` e `dataset/`, ignorados pelo Git para não publicar gravações ou transcrições pessoais. NeMo não está instalado no ambiente de implementação; não foi executado fine-tuning ou exportação de pesos. O checkpoint e APIs foram identificados nas fontes primárias; GPU/NeMo e instalação das dependências de treinamento continuam sem validação prática. Nenhuma melhoria de modelo é declarada.

Para arquivos com datas do WhatsApp ou quantidade divergente de frases, uma ferramenta auxiliar produz evidências para revisão usando o modelo acústico original, sem resolver lexical:

```bash
python tools/review_recordings.py --input "D:/Audios" --output ./inspection/acoustic_review --model-dir ./models/original_int8 --mapping ./mapping.json
```

O mapeamento é opcional e permite resolver cabeçalhos/linhas do roteiro antes da comparação. O destino deve ser novo/vazio. A ferramenta decodifica os áudios para uma pasta separada, registra hipóteses ASR e as frases mais próximas do roteiro em `review.csv`/`review.json`. Não cria manifests, não confirma associações e não usa hipóteses como transcrições de treinamento. Frases repetidas, leituras diferentes e matches ruins continuam exigindo revisão. A ordem cronológica só deve ser aplicada quando confirmada pelo usuário ou sustentada por evidência revisada.
