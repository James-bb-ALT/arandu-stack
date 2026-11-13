FROM docker.io/library/rust:1.98.0-alpine3.22@sha256:2e452153b2dc6bed8ef123c4801cfd3f637e7c092846f974ecdce5e64af3120c AS nlu_builder
WORKDIR /build
COPY rootfs/modules/arandu_nlu/rootfs/src/addon/ /build/
RUN cargo build --manifest-path engine/Cargo.toml --locked --offline --release

FROM python:3.13-slim-bookworm AS llama
RUN apt-get update && apt-get install -y --no-install-recommends git cmake build-essential ca-certificates
RUN git init /llama && cd /llama && git remote add origin https://github.com/ggml-org/llama.cpp.git && git fetch --depth 1 origin f1a7dea99aae1623bad10c4ce31fe220033ab7f6 && git checkout FETCH_HEAD
RUN cmake -S /llama -B /llama/build -DGGML_NATIVE=OFF -DGGML_OPENMP=OFF -DLLAMA_CURL=OFF -DBUILD_SHARED_LIBS=OFF && cmake --build /llama/build --target llama-server -j 2

FROM python:3.12-slim-bookworm AS transcribe_builder
# Source only, immutable runtime revision; model weights are installed separately.
RUN apt-get update && apt-get install -y --no-install-recommends git cmake build-essential ca-certificates
RUN git init /transcribe && cd /transcribe && git remote add origin https://github.com/handy-computer/transcribe.cpp.git && git fetch --depth 1 origin 5700f127e38dd53c4735ea23ef743b4b538eacd4 && git checkout FETCH_HEAD
RUN cmake -S /transcribe -B /transcribe/build -DCMAKE_BUILD_TYPE=Release -DTRANSCRIBE_BUILD_SHARED=ON -DTRANSCRIBE_BUILD_TESTS=OFF -DTRANSCRIBE_BUILD_EXAMPLES=OFF -DTRANSCRIBE_X86_CONSERVATIVE=ON -DTRANSCRIBE_USE_SYSTEM_BLAS=OFF -DTRANSCRIBE_METAL=OFF -DGGML_NATIVE=OFF && cmake --build /transcribe/build --target transcribe -j 2
RUN mkdir -p /runtime/lib /runtime/wheels && find /transcribe/build -name '*.so*' -exec cp -L {} /runtime/lib/ \; && cp /transcribe/LICENSE /transcribe/bindings/python/LICENSE && pip wheel --no-deps --wheel-dir /runtime/wheels /transcribe/bindings/python

FROM python:3.12-slim-bookworm
ENV PYTHONUNBUFFERED=1 PYTHONDONTWRITEBYTECODE=1 OMP_NUM_THREADS=2 OPENBLAS_NUM_THREADS=2
RUN apt-get update && apt-get install -y --no-install-recommends ffmpeg libstdc++6 ca-certificates ocl-icd-libopencl1 intel-opencl-icd clinfo && rm -rf /var/lib/apt/lists/*
RUN pip install --no-cache-dir aiohttp==3.13.3 && pip install --no-cache-dir torch==2.9.1 --index-url https://download.pytorch.org/whl/cpu
COPY --from=nlu_builder /build/engine/target/release/local-nlu /usr/bin/local-nlu
COPY --from=llama /llama/build/bin/llama-server /usr/local/bin/llama-server
COPY --from=transcribe_builder /runtime/lib/ /usr/local/lib/
COPY --from=transcribe_builder /runtime/wheels/ /opt/transcribe-wheels/
RUN ldconfig
ENV TRANSCRIBE_LIBRARY=/usr/local/lib/libtranscribe.so
COPY rootfs/modules /opt/modules
RUN python -m venv --system-site-packages /opt/venvs/decision && /opt/venvs/decision/bin/pip install --no-cache-dir '/opt/modules/arandu_decision/rootfs/src[gpu]' && /opt/venvs/decision/bin/pip check
RUN python -m venv --system-site-packages /opt/venvs/llm && /opt/venvs/llm/bin/pip install --no-cache-dir /opt/modules/arandu_llm/rootfs/src && /opt/venvs/llm/bin/pip check
RUN python -m venv /opt/venvs/stt && /opt/venvs/stt/bin/pip install --no-cache-dir -r /opt/modules/arandu_stt/rootfs/src/arandu_stt/requirements.txt && /opt/venvs/stt/bin/pip check && /opt/venvs/stt/bin/pip install --no-deps /opt/transcribe-wheels/*.whl
RUN python -m venv --system-site-packages /opt/venvs/phonon && /opt/venvs/phonon/bin/pip install --no-cache-dir -r /opt/modules/arandu_stt/rootfs/src/arandu_stt/requirements-phonon.txt && /opt/venvs/phonon/bin/pip check
RUN python -m venv --system-site-packages /opt/venvs/tts && /opt/venvs/tts/bin/pip install --no-cache-dir '/opt/modules/arandu_tts/rootfs/src[pocket]' && /opt/venvs/tts/bin/pip check
RUN python -m venv --system-site-packages /opt/venvs/wake && /opt/venvs/wake/bin/pip install --no-cache-dir -r /opt/modules/arandu_wake/rootfs/src/requirements-detector.txt /opt/modules/arandu_wake/rootfs/src && /opt/venvs/wake/bin/pip check
RUN pip install --no-cache-dir psutil==7.2.2 && pip check
RUN /opt/venvs/stt/bin/python -c "import numpy, sherpa_onnx, faster_whisper, ctranslate2, onnx_asr, onnxruntime, pywebrtc_audio, transcribe_cpp, wyoming, huggingface_hub, aiohttp, psutil, filelock" \
 && /opt/venvs/phonon/bin/python -c "import fermion, torch; assert hasattr(fermion, 'load_speech')" \
 && /opt/venvs/decision/bin/python -c "import aiohttp, sentence_transformers, torch, transformers, huggingface_hub, openvino, onnx" \
 && /opt/venvs/llm/bin/python -c "import aiohttp, huggingface_hub, psutil" \
 && /opt/venvs/tts/bin/python -c "import piper, pocket_tts, wyoming, aiohttp" \
 && /opt/venvs/wake/bin/python -c "import wyoming, websocket"
COPY app /opt/assistant
COPY model_registry /opt/model_registry
RUN python -c "from pathlib import Path; assert Path('/opt/model_registry/models.json').is_file(), 'model_registry/models.json missing'; p=Path('/opt/modules/arandu_stt/rootfs/src/arandu_stt/datasets'); files=['functional_ptbr.txt','supplemental_ptbr.txt','lexicon/arandu_lexico_generico_palavras_ptbr.csv','household/arandu_household_entities_ptbr.csv','names/arandu_nomes_comuns_brasil.csv','commands/arandu_comandos_comuns_ptbr_v2.csv']; missing=[f for f in files if not (p/f).is_file() or not (p/f).stat().st_size]; assert not missing, 'STT datasets missing: '+str(missing)"
COPY assets /opt/assistant-assets
WORKDIR /opt/assistant
EXPOSE 8099 11555 10350 10212 10400
CMD ["python", "server.py"]
