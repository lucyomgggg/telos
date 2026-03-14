FROM python:3.11-slim

# Install system dependencies
RUN apt-get update && apt-get install -y --no-install-recommends \
    build-essential \
    git \
    curl \
    && rm -rf /var/lib/apt/lists/*

# Set up the workspace
WORKDIR /workspace

# Keep the container running
CMD ["tail", "-f", "/dev/null"]
