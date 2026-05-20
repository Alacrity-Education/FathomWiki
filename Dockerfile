FROM node:22-slim

RUN apt-get update && apt-get install -y --no-install-recommends \
    python3 \
    python3-venv \
    ca-certificates \
    git \
    openssh-client \
    && rm -rf /var/lib/apt/lists/*

RUN npm install -g @anthropic-ai/claude-code

ENV PYTHONUNBUFFERED=1

WORKDIR /app
COPY process_fathom.py .
COPY entrypoint.sh .
RUN chmod +x entrypoint.sh

RUN mkdir -p /output

ENTRYPOINT ["./entrypoint.sh"]
