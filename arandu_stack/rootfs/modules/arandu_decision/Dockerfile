FROM python:3.13-slim-bookworm
ENV PYTHONUNBUFFERED=1 ARANDU_BIND_HOST=0.0.0.0 HF_HOME=/data/hub
RUN pip install --no-cache-dir torch==2.9.1 --index-url https://download.pytorch.org/whl/cpu
COPY rootfs/src /opt/arandu-decision
RUN pip install --no-cache-dir /opt/arandu-decision
COPY run.py /opt/run.py
CMD ["python", "/opt/run.py"]
