# HTTP front for TrueNAS's WebSocket API (bridge.py). Bump the image tag in compose.yaml when this
# file changes, so the stacks play builds it again.
FROM python:3.12-alpine
RUN pip install --no-cache-dir websockets==15.0.1
COPY bridge.py /app/
ENV PYTHONUNBUFFERED=1 PYTHONDONTWRITEBYTECODE=1
CMD ["python", "/app/bridge.py"]
