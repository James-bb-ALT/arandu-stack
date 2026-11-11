FROM python:3.12-slim-bookworm AS transcribe_builder
# Source only, immutable runtime revision; model weights are installed separately.
RUN apt-get update && apt-get install -y --no-install-recommends git cmake build-essential ca-certificates
RUN git init /transcribe && cd /transcribe && git remote add origin https://github.com/handy-computer/transcribe.cpp.git && git fetch --depth 1 origin 5700f127e38dd53c4735ea23ef743b4b538eacd4 && git checkout FETCH_HEAD
RUN cmake -S /transcribe -B /transcribe/build -DCMAKE_BUILD_TYPE=Release -DTRANSCRIBE_BUILD_SHARED=ON -DTRANSCRIBE_BUILD_TESTS=OFF -DTRANSCRIBE_BUILD_EXAMPLES=OFF -DTRANSCRIBE_X86_CONSERVATIVE=ON -DTRANSCRIBE_USE_SYSTEM_BLAS=OFF -DTRANSCRIBE_METAL=OFF -DGGML_NATIVE=OFF && cmake --build /transcribe/build --target transcribe -j 2
RUN mkdir -p /runtime/lib /runtime/wheels && find /transcribe/build -name '*.so*' -exec cp -L {} /runtime/lib/ \; && cp /transcribe/LICENSE /transcribe/bindings/python/LICENSE && pip wheel --no-deps --wheel-dir /runtime/wheels /transcribe/bindings/python

FROM python:3.12-slim-bookworm

ARG BUILD_VERSION="4.0.0"
ARG BUILD_ARCH="amd64"

LABEL \
  io.hass.version="$BUILD_VERSION" \
  io.hass.type="app" \
  io.hass.arch="$BUILD_ARCH"

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    HF_HOME=/data/hub

COPY --from=transcribe_builder /runtime/lib/ /usr/local/lib/
COPY --from=transcribe_builder /runtime/wheels/ /opt/transcribe-wheels/
RUN ldconfig && pip install --no-deps /opt/transcribe-wheels/*.whl

ENV TRANSCRIBE_LIBRARY=/usr/local/lib/libtranscribe.so
WORKDIR /app

RUN apt-get update \
    && apt-get install -y --no-install-recommends ca-certificates \
    && rm -rf /var/lib/apt/lists/*

COPY requirements.txt /app/requirements.txt
RUN pip install --no-cache-dir -r /app/requirements.txt

COPY . /app
RUN chmod +x /app/run.sh

CMD ["/app/run.sh"]
