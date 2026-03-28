FROM python:3.11-slim

RUN apt-get update && apt-get install -y --no-install-recommends \
    build-essential git curl jq tree && \
    rm -rf /var/lib/apt/lists/*

RUN pip install --no-cache-dir \
    requests httpx beautifulsoup4 lxml \
    pandas numpy matplotlib seaborn \
    pyyaml toml click rich \
    pytest

WORKDIR /workspace

CMD ["tail", "-f", "/dev/null"]
