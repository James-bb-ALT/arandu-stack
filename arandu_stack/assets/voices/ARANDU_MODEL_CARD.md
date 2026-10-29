# Arandu — Piper Medium PT-BR

Voz treinada localmente para o Arandu e incluída como padrão do TTS.

- Arquivos: `pt_BR-arandu-medium.onnx` e `.onnx.json`.
- Idioma: português brasileiro; um locutor; 22.050 Hz.
- Base: checkpoint público Faber Medium do Piper.
- Checkpoint exportado: `last.ckpt`, época 145, passo 348356, o mais recente
  salvo pelo treinamento local. O treinamento terminou ao atingir 150 épocas;
  o arquivo de checkpoint disponível registra a época 145.
- Exportador: Piper `639388b6317fc4731e91d53da42aea68fd4166ff`, ONNX legado
  TorchScript, sem nova rodada de treinamento.
- Hashes dos dois arquivos: `manifest.json`.

Dados, gravações, transcrições, logs e checkpoints de treinamento não são
publicados. A exportação não inclui avaliação de qualidade, benchmark ou geração
de fala para teste. O usuário pode selecionar Faber Medium pela WebUI.

## Origem e licenças

O repositório de vozes Piper declara MIT; o MODEL_CARD de Faber identifica seu
dataset como CC0. Ambos os avisos originais foram preservados nos arquivos
`FABER_REPOSITORY_README.md` e `FABER_MODEL_CARD.md`. Esses são registros distintos
da licença do motor Piper, que permanece GPL-3.0-or-later.

Fonte: https://huggingface.co/rhasspy/piper-voices/tree/5041a7a49ae5b3a0f30e6837769eb03fdbb231bd/pt/pt_BR/faber/medium

O fine-tune Arandu é distribuído neste projeto sob Apache-2.0, preservando a
atribuição e a licença MIT da base. Consulte também a licença da raiz do projeto.
