FROM python:3.13-slim-bookworm AS llama
RUN apt-get update && apt-get install -y --no-install-recommends git cmake build-essential ca-certificates
RUN git init /llama && cd /llama && git remote add origin https://github.com/ggml-org/llama.cpp.git && git fetch --depth 1 origin f1a7dea99aae1623bad10c4ce31fe220033ab7f6 && git checkout FETCH_HEAD
RUN cmake -S /llama -B /llama/build -DGGML_NATIVE=OFF -DGGML_OPENMP=OFF -DLLAMA_CURL=OFF -DBUILD_SHARED_LIBS=OFF && cmake --build /llama/build --target llama-server -j 2
FROM python:3.13-slim-bookworm
RUN apt-get update && apt-get install -y --no-install-recommends libstdc++6 && rm -rf /var/lib/apt/lists/*
ENV PYTHONUNBUFFERED=1 ARANDU_BIND_HOST=0.0.0.0 HF_HOME=/data/hub
COPY --from=llama /llama/build/bin/llama-server /usr/local/bin/llama-server
COPY rootfs/src /opt/arandu-llm
RUN pip install --no-cache-dir /opt/arandu-llm
COPY run.py /opt/run.py
CMD ["python", "/opt/run.py"]
