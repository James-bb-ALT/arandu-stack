#!/usr/bin/env bash
# Rodar em Linux/WSL2 (RTX 3080) com piper1-gpl[train] instalado e compilado.
set -euo pipefail
DATASET="${DATASET:-$PWD/dataset}"
WORK="${WORK:-$PWD/training}"
VOICE_NAME="${VOICE_NAME:-arandu}"
BATCH_SIZE="${BATCH_SIZE:-8}"
EPOCHS="${EPOCHS:-100}"
mkdir -p "$WORK/cache" "$WORK/outputs"
ARGS=(
  --data.voice_name "$VOICE_NAME"
  --data.csv_path "$DATASET/metadata.csv"
  --data.audio_dir "$DATASET/audio"
  --model.sample_rate 22050
  --data.espeak_voice pt-br
  --data.cache_dir "$WORK/cache"
  --data.config_path "$WORK/outputs/pt_BR-${VOICE_NAME}-medium.onnx.json"
  --data.batch_size "$BATCH_SIZE"
  --trainer.max_epochs "$EPOCHS"
)
if [[ -n "${START_CHECKPOINT:-}" ]]; then
  ARGS+=(--ckpt_path "$START_CHECKPOINT")
else
  echo 'AVISO: sem START_CHECKPOINT, treinamento começa do zero. Prefira checkpoint medium compatível.' >&2
fi
python -m piper.train fit "${ARGS[@]}"
cat <<MSG
Treinamento finalizado. Exporte o checkpoint desejado com:
  python -m piper.train.export_onnx --checkpoint CAMINHO.ckpt --output-file $WORK/outputs/pt_BR-${VOICE_NAME}-medium.onnx
Depois copie .onnx e .onnx.json para voices/ no servidor Arandu.
MSG
