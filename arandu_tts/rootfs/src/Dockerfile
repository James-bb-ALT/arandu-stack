FROM python:3.12-slim-bookworm
ENV PYTHONUNBUFFERED=1 PYTHONDONTWRITEBYTECODE=1 OMP_NUM_THREADS=2 OPENBLAS_NUM_THREADS=1 \
    HF_HOME=/data/hf-cache XDG_CACHE_HOME=/data/hf-cache
WORKDIR /opt/arandu
RUN apt-get update && apt-get install -y --no-install-recommends ffmpeg \
    && rm -rf /var/lib/apt/lists/* \
    && python -m pip install --no-cache-dir 'torch>=2.5,<3' --index-url https://download.pytorch.org/whl/cpu
COPY pyproject.toml ./
COPY arandu_tts ./arandu_tts
RUN python -m pip install --no-cache-dir '.[pocket]' \
    && mkdir -p /data/voices /data/hf-cache
EXPOSE 10212/tcp 8099/tcp
CMD ["arandu-tts"]
