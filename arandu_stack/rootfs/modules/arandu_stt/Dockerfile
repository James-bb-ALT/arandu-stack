FROM python:3.12-slim-bookworm AS transcribe_builder
# Source only, immutable runtime revision; model weights are installed separately.
RUN apt-get update && apt-get install -y --no-install-recommends git cmake build-essential ca-certificates
RUN git init /transcribe && cd /transcribe && git remote add origin https://github.com/handy-computer/transcribe.cpp.git && git fetch --depth 1 origin 5700f127e38dd53c4735ea23ef743b4b538eacd4 && git checkout FETCH_HEAD
RUN cmake -S /transcribe -B /transcribe/build -DCMAKE_BUILD_TYPE=Release -DTRANSCRIBE_BUILD_SHARED=ON -DTRANSCRIBE_BUILD_TESTS=OFF -DTRANSCRIBE_BUILD_EXAMPLES=OFF -DTRANSCRIBE_X86_CONSERVATIVE=ON -DTRANSCRIBE_USE_SYSTEM_BLAS=OFF -DTRANSCRIBE_METAL=OFF -DGGML_NATIVE=OFF && cmake --build /transcribe/build --target transcribe -j 2
RUN mkdir -p /runtime/lib /runtime/wheels && find /transcribe/build -name '*.so*' -exec cp -L {} /runtime/lib/ \; && cp /transcribe/LICENSE /transcribe/bindings/python/LICENSE && pip wheel --no-deps --wheel-dir /runtime/wheels /transcribe/bindings/python

FROM python:3.12-slim-bookworm AS whisper_cpp_builder
# Source only, pinned immutable upstream release tag v1.9.5; model weights are downloaded on demand.
RUN apt-get update && apt-get install -y --no-install-recommends git cmake build-essential ca-certificates
RUN git init /whisper && cd /whisper && git remote add origin https://github.com/ggml-org/whisper.cpp.git && git fetch --depth 1 origin d1be6fde11ac6e0407606b4e42fe72d34add8037 && git checkout FETCH_HEAD
RUN cmake -S /whisper -B /whisper/build -DCMAKE_BUILD_TYPE=Release -DWHISPER_BUILD_IS_DEV=OFF -DBUILD_SHARED_LIBS=OFF -DGGML_NATIVE=OFF -DGGML_OPENMP=OFF -DGGML_AVX512=OFF -DWHISPER_BUILD_TESTS=OFF -DWHISPER_BUILD_EXAMPLES=ON -DWHISPER_BUILD_SERVER=ON && cmake --build /whisper/build --target whisper-server whisper-cli -j 2

FROM python:3.12-slim-bookworm

ARG BUILD_VERSION="4.0.1"
ARG BUILD_ARCH="amd64"
ARG ENABLE_NEMOTRON_RUNTIME=1

LABEL \
  io.hass.version="$BUILD_VERSION" \
  io.hass.type="app" \
  io.hass.arch="$BUILD_ARCH"

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    HF_HOME=/data/hub \
    ENABLE_NEMOTRON_RUNTIME=${ENABLE_NEMOTRON_RUNTIME}

COPY --from=transcribe_builder /runtime/lib/ /usr/local/lib/
COPY --from=transcribe_builder /runtime/wheels/ /opt/transcribe-wheels/
COPY --from=whisper_cpp_builder /whisper/build/bin/whisper-server /usr/local/bin/whisper-server
COPY --from=whisper_cpp_builder /whisper/build/bin/whisper-cli /usr/local/bin/whisper-cli
RUN ldconfig && pip install --no-deps /opt/transcribe-wheels/*.whl && test -x /usr/local/bin/whisper-server && test -x /usr/local/bin/whisper-cli

ENV TRANSCRIBE_LIBRARY=/usr/local/lib/libtranscribe.so
WORKDIR /app

RUN apt-get update \
    && apt-get install -y --no-install-recommends ca-certificates libsndfile1 ffmpeg \
    && rm -rf /var/lib/apt/lists/*

COPY rootfs/src/arandu_stt/requirements.txt /app/requirements.txt
COPY rootfs/src/arandu_stt/requirements-nemotron.txt /app/requirements-nemotron.txt
RUN pip install --no-cache-dir -r /app/requirements.txt
RUN if [ "$ENABLE_NEMOTRON_RUNTIME" = "1" ]; then \
      python -m venv /opt/venvs/nemotron \
      && /opt/venvs/nemotron/bin/pip install --no-cache-dir --extra-index-url https://download.pytorch.org/whl/cpu -r /app/requirements-nemotron.txt \
      && /opt/venvs/nemotron/bin/pip check \
      && /opt/venvs/nemotron/bin/python -c "import numpy, torch, soundfile, nemo.collections.asr as nemo_asr; assert hasattr(nemo_asr.models, 'ASRModel')"; \
    fi

COPY rootfs/src/arandu_stt /app
RUN chmod +x /app/run.sh

CMD ["/app/run.sh"]
