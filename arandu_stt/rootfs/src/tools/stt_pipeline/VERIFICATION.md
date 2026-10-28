# Verificação da entrega — 13/10/2026

**Registro da preparação inicial.** Posteriormente, a pedido do usuário, foi executado treino CUDA e criada a interface web. O resultado final e suas limitações estão em [comparação local e treinamento RTX](../comparison_app/README.md). A avaliação final não demonstrou ganho de WER no runtime Windows; produção continua preservada.

## Arquivos implementados

```text
tools/prepare_dataset.py
tools/review_recordings.py
tools/stt_pipeline/
  __init__.py
  __main__.py
  audio.py
  cli.py
  common.py
  dataset.py
  demo.py
  evaluation.py
  exporting.py
  models.py
  scripts.py
  split.py
  training.py
  requirements.txt
  requirements-training.txt
  requirements-export.txt
  mapping.example.json
  training.example.json
  README.md
  VERIFICATION.md
  tests/test_pipeline.py
```

O README da raiz recebeu o link das ferramentas; `.gitignore` recebeu diretórios de dados, experimentos e ambiente virtual. Nenhum componente de produção foi alterado. Não houve commit/push nem substituição de pesos.

## Provas executadas

- 35 testes novos passaram, incluindo conversões reais OGG/Opus e Vorbis, MP4/AAC, vídeo com áudio, `01.m4a.mp4`, fluxo múltiplo, extensão incorreta, scripts numerados e 80 frases sem numeração, omissões, duplicatas de arquivo e PCM entre WAV/FLAC, silêncio/corrupção, cache, preservação de originais, métricas conhecidas, divisão sem vazamento e bloqueios de exportação.
- 46 testes existentes do Arandu passaram, incluindo transporte/contratos Wyoming. Total: **81 testes**, sem skips nos testes novos de mídia.
- Compilação Python e `git diff --check` passaram.
- Demonstração sintética: sete participantes, 48 exemplos válidos e quatro rejeitados intencionalmente. Não serve para treinamento.
- Decodificação real de **todos os 171 arquivos de entrada** funcionou na auditoria. FFprobe identificou 156 OGG/Opus e 15 MP4/AAC.
- Dataset final: **170 exemplos**, **7 participantes**, **550,887375 segundos**, WAV PCM16 mono/16 kHz. Um áudio fora do roteiro foi excluído pelo mapeamento.
- Divisão final: **136 train / 17 validation / 17 test**, seed **57**. Todos os sete participantes aparecem em todos os conjuntos. A seed foi selecionada pelas contagens de participantes, não por WER/CER. Validação completa passou; nenhuma frase ou PCM atravessa conjuntos.
- Reexecução reaproveitou todos os 170 WAVs por cache. Os SHA-256 dos 171 áudios originais permaneceram inalterados.

## Mapeamento dos dados reais

Resultados privados, ignorados pelo Git:

```text
inspection/real/inspection.json
inspection/real/mapping.proposed.json
inspection/real/mapping_review.csv
inspection/real/mapping.reviewed.json
inspection/real/association_review.json
inspection/real/association_review.csv
inspection/real/acoustic/review.json
inspection/real/acoustic/review.csv
dataset/metadata.jsonl
dataset/manifests/train.jsonl
dataset/manifests/validation.jsonl
dataset/manifests/test.jsonl
dataset/reports/statistics.json
dataset/reports/release.json
evaluations/original_seed57/comparison.json
evaluations/original_seed57/baseline_details.json
```

Arquivos numerados foram associados pelo identificador. Arquivos com datas do WhatsApp receberam mapeamento explícito após comparação das hipóteses do modelo CTC original com as frases existentes. Uma pasta com horários repetidos não seguia a ordem dos nomes: o mapeamento registra cada associação pelo conteúdo. Um áudio extra anterior ao roteiro foi excluído, preservando os outros 15 identificadores. Cinco linhas finais sem pontuação do roteiro de 80 frases foram explicitamente incluídas, sem acrescentar pontuação ao texto.

Em uma frase desse roteiro, a menor WER contra as hipóteses ASR apontava a frase errada. Revisão semântica identificou a frase sobre saída do escritório/monitores; o mapeamento registra a decisão e a evidência. O usuário confirmou em 13/10/2026 que os áudios WhatsApp foram enviados na ordem do roteiro. A sequência também foi corroborada por 79 dos 80 matches de conteúdo nesse roteiro. A confirmação não substitui o mapeamento explícito dos nomes com horários repetidos nem a exclusão do áudio extra. Hipóteses ASR não foram utilizadas como transcrições de treinamento.

Essa auditoria verifica correspondências prováveis, mas não equivale a transcrição humana por escuta. Há erros ASR e possíveis variações na leitura dos roteiros. Conferência auditiva do CSV de associações é recomendada antes do treino, especialmente na decisão que diverge do ranking por WER. Os rótulos continuam provenientes dos TXT originais.

## Avaliação acústica e limites

O modelo e tokenizador originais INT8 foram verificados pelos hashes registrados no Arandu. A ferramenta de avaliação executou inferência real duas vezes com o mesmo modelo original no teste final, comprovando o contrato de comparação e resultados determinísticos:

| Métrica | Original INT8, 17 exemplos de teste |
|---|---:|
| WER normalizada | 0,20 (27 erros / 135 palavras) |
| CER normalizada | 0,0658436214 (48 erros / 729 caracteres) |
| Participantes avaliados | 7 |
| Delta entre duas execuções do mesmo modelo | 0 em WER e CER |
| Melhoria de modelo | Não demonstrada |

O relatório inclui resultados por participante, palavras incorretas, latência e RSS. O teste é pequeno; os resultados são uma linha de base preliminar relativa aos textos dos roteiros. A auditoria de correspondências de todos os áudios não foi reportada como uma avaliação de generalização. Não foi treinado outro modelo e a comparação original/original não representa ganho de qualidade.

Treinamento NeMo e exportação de checkpoint treinado estão implementados, mas **não executados**. NeMo está ausente no ambiente Windows de implementação. Prepare ambiente Linux/WSL2 separado com as dependências fixadas; a execução real GPU/NeMo e a instalação desse ambiente ainda precisam de validação. O dataset contém somente aproximadamente nove minutos de áudio, e o treino de um FastConformer de 115M parâmetros pode sofrer overfitting. Não foi feita quantização de pesos treinados, implantação no HAOS ou alteração do modelo de produção.

## Reprodução da preparação final

Na raiz do checkout:

```powershell
python tools/prepare_dataset.py prepare --input "D:\Audios" --output ./dataset --mapping ./inspection/real/mapping.reviewed.json --seed 57
python tools/prepare_dataset.py validate --dataset ./dataset
python -m unittest discover -s tools/stt_pipeline/tests -v
```

O JSON privado de mapeamento e os originais são necessários para reproduzir exatamente o dataset local. Para novas gravações, use os comandos de inspeção e as regras de revisão do README. Os caminhos absolutos nos manifests devem ser gerados novamente ao mudar de Windows para Linux/WSL2.
