# The sysadmin's investigator (investigator.py). The Claude Agent SDK wheel carries its own Claude
# Code binary, so nothing else is fetched at run time. Bump the image tag in compose.yaml when this
# file or the pinned version changes, so the stacks play builds it again.
FROM python:3.12-slim
RUN apt-get update && apt-get install -y --no-install-recommends openssh-client ca-certificates \
    && rm -rf /var/lib/apt/lists/*
RUN pip install --no-cache-dir claude-agent-sdk==0.2.174
COPY investigator.py scriptcheck.py /app/
WORKDIR /app
ENV PYTHONUNBUFFERED=1 PYTHONDONTWRITEBYTECODE=1
CMD ["python", "-W", "ignore::UserWarning", "/app/investigator.py"]
