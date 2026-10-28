FROM python:3.11-slim-bookworm
ENV PYTHONUNBUFFERED=1
COPY rootfs/src /opt/arandu-wake
RUN pip install --no-cache-dir -r /opt/arandu-wake/requirements-detector.txt /opt/arandu-wake
COPY run.py /opt/run.py
CMD ["python", "/opt/run.py"]
