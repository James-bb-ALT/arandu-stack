# Third-party notices

## NVIDIA FastConformer PT-BR checkpoint

Upstream model: `nvidia/stt_pt_fastconformer_hybrid_large_pc`.

The upstream checkpoint is licensed under **CC BY-NC 4.0** (https://creativecommons.org/licenses/by-nc/4.0/). Arandu 2.1 distributes a derivative FP32 ONNX model as a GitHub release asset under the same license. NVIDIA supplied the base model; Arandu adapted its CTC decoder and exported it on 2026-10-13. Source: `nvidia/stt_pt_fastconformer_hybrid_large_pc`, revision `036da25fa32e3f11c11105448482845950840685`. Model weights are separate from MIT-licensed code. See `arandu_stt/MODEL_2.1.md` for changes, evaluation and rollback.

## INT8 ONNX conversion

Runtime download source: `csukuangfj/sherpa-onnx-nemo-stt_pt_fastconformer_hybrid_large_pc-int8` on Hugging Face.

Users are responsible for reviewing the upstream model/conversion terms before deployment or redistribution.

## Runtime libraries

The App depends on `sherpa-onnx`, `wyoming`, `RapidFuzz`, NumPy, `websockets`, and `huggingface_hub`, each under its own license.
