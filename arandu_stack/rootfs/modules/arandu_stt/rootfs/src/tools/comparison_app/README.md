# Arandu — comparação acústica local

Atualização de 14/10/2026: `start.ps1` abre por padrão o modelo FP32 anterior
(versão 2.1.0) contra o novo treino em `runs/jaime_continued_20261014_human_balanced`.
As colunas aparecem como **ANTERIOR 2.1** e **NOVO TREINO**. O servidor também
aceita `--baseline-model` para selecionar o modelo da primeira coluna.

Após avaliação prática do proprietário, o novo modelo foi promovido para a
versão padrão 2.2.0. A comparação continua disponível contra a versão 2.1.

App em **http://127.0.0.1:8765**. O servidor permanece rodando no Windows para a entrega. Depois de reiniciar a máquina, execute na raiz do checkout:

```powershell
powershell -File tools/comparison_app/start.ps1
```

Para executar em primeiro plano:

```powershell
python -m pip install -r tools/comparison_app/requirements.txt
python tools/comparison_app/server.py --experiment runs/jaime_continued_20261014_human_balanced --baseline-model runs/rtx3080_20261013/export/fp32 --port 8765
```

FFmpeg/FFprobe precisam estar no PATH para arquivos enviados pelo usuário. O app usa os modelos locais em `models/original_int8` e no diretório do experimento. NeMo não precisa estar instalado no Windows: os artefatos ONNX são reconhecidos por sherpa-onnx.

## Experiência

- Reprodução e comparação lado a lado das 170 gravações; filtros por participante, treino, validação e teste.
- Referência do roteiro, transcrição dos dois modelos, palavras diferentes em destaque, WER/CER por frase e latência.
- Upload de OGG, WAV, MP4, M4A ou WebM; gravação pelo microfone do navegador; limite de 35 segundos/15 MB. Referência opcional para áudio novo.
- Curva real de validação, melhor época e métricas dos sete participantes no teste.
- Áudio novo é processado em uma pasta temporária e excluído após a resposta; não é adicionado ao dataset nem usado para novo treino. O servidor escuta apenas em loopback, sem serviços externos de transcrição.

O microfone depende da permissão do navegador. Abra `http://127.0.0.1:8765` no Chrome ou Edge, selecione **Meu áudio**, clique em **Gravar no microfone**, permita o acesso e use **Parar e comparar**. A gravação é enviada automaticamente aos dois modelos, sem precisar apertar outro botão. O limite de captura é 34 segundos, com margem para o limite de 35 segundos do servidor. Referência textual é opcional.

A comparação usa áudio mono/16 kHz e decodificação CTC greedy, sem resolver lexical ou transporte Wyoming. O app só abre modelos com escrita finalizada. Em 14/10/2026, o proprietário solicitou explicitamente a promoção FP32: publicada como padrão do Arandu STT 2.1.0, com rollback para o original. O gate automático do experimento foi preservado como evidência histórica.

## Experimento executado na RTX 3080

NeMo 2.5.3/PyTorch 2.8.0 foram instalados em Docker Linux, isolados do Python Windows. O Ubuntu registrado no WSL tinha o disco ausente; Docker já estava funcional e permitiu acesso CUDA.

Foi adaptado o decoder CTC original: **66.177 parâmetros** de **113.034.978**, encoder e ramo RNNT congelados. Representações do encoder foram calculadas em CPU; forward/backward e AdamW do decoder foram executados na RTX em BF16. Essa estratégia conserva o modelo acústico com aproximadamente nove minutos de dados e não exige manter todo o backbone na VRAM.

Treinamento: batch 8, LR 0,0003, AdamW, clipping 1, seed 42, até 80 épocas, patience 12. Foram concluídas **36 épocas**, selecionando a **época 24** somente pela validação. Dos 136 exemplos do conjunto train, **126** tiveram alinhamento CTC viável; dez frases rápidas exigiam mais frames do que o encoder produzia e foram excluídas do gradiente, sem cortar fala ou mudar transcrições. Os sete locutores permaneceram representados. Validação e teste ficaram intactos, com 17 exemplos cada.

Validação: WER original 17,0543%, melhor WER 15,5039%. A execução de treino/cache/salvamento levou aproximadamente 61 segundos após preparar o ambiente; instalação e avaliação/exportação levaram tempo adicional. `run.json` registra dispositivo, precisão, parâmetros e pico de alocação CUDA; `last.pt` guarda parâmetros, AdamW, scaler, RNG e seleção para retomada. `best.nemo` contém o checkpoint selecionado.

### Resultado realmente servido pelo app

Benchmark no **Windows**, usando a mesma API sherpa-onnx do app e o mesmo teste de 17 frases/135 palavras/729 caracteres:

| Métrica | Original INT8 | Adaptado FP32 |
|---|---:|---:|
| WER | 20,00% — 27 erros | 20,00% — 27 erros |
| CER | 6,5844% — 48 erros | 6,0357% — 44 erros |

Há pequena redução de CER, **sem redução de WER**. Não há evidência suficiente para declarar melhoria geral ou substituir produção. O conjunto de teste é pequeno e contém de um a seis exemplos por participante. O app permite ver tanto frases que melhoraram quanto frases que pioraram.

A avaliação do `.nemo` em Linux mostrou ganho pequeno, mas ele não foi preservado integralmente no exportador/frontend de inferência. FP32 e INT8 passaram pelo checker ONNX e foram decodificados no runtime existente. O INT8 treinado regrediu em WER contra o FP32 no teste Linux, então o gate de produção foi reprovado e o app usa **FP32 de revisão**. Diferenças numéricas entre bibliotecas/compilações Windows e Linux também alteraram uma palavra da linha de base; os números exibidos no app usam o benchmark Windows.

## Artefatos locais

```text
runs/rtx3080_20261013/
  best.nemo
  last.pt
  run.json
  history.json
  progress.json
  encoder_cache.pt
  ctc_eligibility.json
  weight_verification.json
  environment.lock.txt
  comparison.json
  comparison_runtime.json
  experiment.json
  evaluation/
  runtime_evaluation/
  native_evaluation/
  export/fp32/model.onnx
  export/fp32/tokens.txt
  export/int8/model.int8.onnx
  export/int8/tokens.txt
  export/export.json
```

O encoder e o ramo RNNT foram comparados tensor a tensor com o original: apenas peso e bias CTC mudaram. Os três arquivos do tokenizador foram comparados por SHA-256 e permaneceram idênticos. Originais, manifests Windows e produção permanecem preservados.

## Reproduzir o ambiente e o fluxo

O Dockerfile principal já contém as versões compatíveis de Datasets/PyArrow/PEFT/Numba; a camada `Dockerfile.compat` foi usada para corrigir a imagem instalada na primeira execução. Checkpoint treinável fixado no repositório NVIDIA/revisão documentados pelo pipeline.

```powershell
docker build -f tools/stt_pipeline/Dockerfile.training -t arandu-stt-training:2.5.3-runtime .
```

O entrypoint `tools/run_rtx_experiment.py` é executado com GPU habilitada, checkout montado em `/workspace` como readonly e diretório do experimento em `/experiment` como read-write. Ele cria uma cópia Linux dos manifests, treina, avalia, exporta e mede os dois artefatos no runtime. Os manifests Windows não são reescritos. `environment.lock.txt` registra o ambiente que foi realmente usado.

Para uma nova execução, use um diretório de experimento novo. Para retomar treino interrompido, o módulo `tools.stt_pipeline.cached_training` aceita `--resume` e verifica identidade do checkpoint, dos manifests e da configuração. Não repita avaliações no mesmo diretório de resultado finalizado.

## Verificação da interface

Cinco testes do app passaram: IDs/caminhos de áudio, ausência de métricas fictícias, proteção contra ONNX parcialmente escrito, limites de upload e preservação dos manifests na conversão Windows/Linux. HTTP real verificou a página, status, 170 gravações, reprodução, inferência de ambos os modelos e upload com referência. Em 14/10/2026, upload WebM/Opus também retornou ambas as transcrições. JavaScript passou pelo parser Node e por simulação de cliques duplicados, encerramento/envio/comparação e permissão negada. A captura usa estado bloqueado durante a permissão/finalização, encerra as trilhas e compara automaticamente; erros de permissão aparecem com instruções. Não houve verificação visual automatizada nem captura de microfone real: nenhum navegador controlável estava disponível nesta sessão.
