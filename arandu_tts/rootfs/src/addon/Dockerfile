FROM python:3.12-slim-bookworm
ENV PYTHONUNBUFFERED=1 PYTHONDONTWRITEBYTECODE=1 PYTHONPATH=/opt/arandu \
    OMP_NUM_THREADS=2 OPENBLAS_NUM_THREADS=1 \
    HF_HOME=/share/arandu_tts/hf-cache XDG_CACHE_HOME=/share/arandu_tts/cache
RUN apt-get update && apt-get install -y --no-install-recommends ffmpeg \
    && rm -rf /var/lib/apt/lists/* \
    && python -m pip install --no-cache-dir 'torch>=2.5,<3' --index-url https://download.pytorch.org/whl/cpu \
    && python -m pip install --no-cache-dir 'piper-tts==1.8.0' 'wyoming==1.10.2' \
       'aiohttp>=3.10,<4' 'pocket-tts==3.3.0'
COPY app/ /opt/arandu/
COPY run.sh /run.sh
RUN chmod 0555 /run.sh && mkdir -p /share/arandu_tts/voices /share/arandu_tts/hf-cache
EXPOSE 10212/tcp 8099/tcp
CMD ["/run.sh"]
