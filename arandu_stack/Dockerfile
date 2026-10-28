FROM python:3.13-slim-bookworm
RUN pip install --no-cache-dir aiohttp==3.13.3
COPY app /opt/arandu-stack
WORKDIR /opt/arandu-stack
CMD ["python", "server.py"]
