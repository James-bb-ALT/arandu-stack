FROM docker.io/library/rust:1.98.0-alpine3.22@sha256:2e452153b2dc6bed8ef123c4801cfd3f637e7c092846f974ecdce5e64af3120c AS nlu_builder
WORKDIR /build
COPY rootfs/modules/arandu_nlu/rootfs/src/addon/ /build/
RUN cargo build --manifest-path engine/Cargo.toml --locked --offline --release

FROM python:3.13-slim-bookworm AS llama
RUN apt-get update && apt-get install -y --no-install-recommends git cmake build-essential ca-certificates
RUN git init /llama && cd /llama && git remote add origin https://github.com/ggml-org/llama.cpp.git && git fetch --depth 1 origin f1a7dea99aae1623bad10c4ce31fe220033ab7f6 && git checkout FETCH_HEAD
RUN cmake -S /llama -B /llama/build -DGGML_NATIVE=OFF -DGGML_OPENMP=OFF -DLLAMA_CURL=OFF -DBUILD_SHARED_LIBS=OFF && cmake --build /llama/build --target llama-server -j 2

FROM python:3.12-slim-bookworm
ENV PYTHONUNBUFFERED=1 PYTHONDONTWRITEBYTECODE=1 OMP_NUM_THREADS=2 OPENBLAS_NUM_THREADS=2
RUN apt-get update && apt-get install -y --no-install-recommends ffmpeg libstdc++6 ca-certificates && rm -rf /var/lib/apt/lists/*
RUN pip install --no-cache-dir aiohttp==3.13.3 && pip install --no-cache-dir torch==2.9.1 --index-url https://download.pytorch.org/whl/cpu
COPY --from=nlu_builder /build/engine/target/release/local-nlu /usr/bin/local-nlu
COPY --from=llama /llama/build/bin/llama-server /usr/local/bin/llama-server
COPY rootfs/modules /opt/modules
RUN python -m venv --system-site-packages /opt/venvs/decision && /opt/venvs/decision/bin/pip install --no-cache-dir /opt/modules/arandu_decision/rootfs/src
RUN python -m venv --system-site-packages /opt/venvs/llm && /opt/venvs/llm/bin/pip install --no-cache-dir /opt/modules/arandu_llm/rootfs/src
RUN python -m venv --system-site-packages /opt/venvs/stt && /opt/venvs/stt/bin/pip install --no-cache-dir -r /opt/modules/arandu_stt/rootfs/src/arandu_stt/requirements.txt
RUN python -m venv --system-site-packages /opt/venvs/tts && /opt/venvs/tts/bin/pip install --no-cache-dir '/opt/modules/arandu_tts/rootfs/src[pocket]'
RUN python -m venv --system-site-packages /opt/venvs/wake && /opt/venvs/wake/bin/pip install --no-cache-dir -r /opt/modules/arandu_wake/rootfs/src/requirements-detector.txt /opt/modules/arandu_wake/rootfs/src
COPY app /opt/assistant
WORKDIR /opt/assistant
EXPOSE 8099 11555 10350 10212 10400
CMD ["python", "server.py"]
