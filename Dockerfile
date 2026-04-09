FROM python:3.12-slim

# System deps
RUN apt-get update && apt-get install -y --no-install-recommends \
    ffmpeg \
    curl \
    ca-certificates \
    unzip \
    && rm -rf /var/lib/apt/lists/*

# Install yt-dlp
RUN pip install --no-cache-dir yt-dlp

# Install deno (JS runtime for YouTube format support)
RUN ARCH=$(uname -m) && \
    if [ "$ARCH" = "x86_64" ]; then DENO_ARCH="x86_64"; \
    elif [ "$ARCH" = "aarch64" ]; then DENO_ARCH="aarch64"; \
    else DENO_ARCH="x86_64"; fi && \
    curl -fsSL "https://github.com/denoland/deno/releases/latest/download/deno-${DENO_ARCH}-unknown-linux-gnu.zip" -o /tmp/deno.zip && \
    unzip -o /tmp/deno.zip -d /usr/local/bin && \
    chmod +x /usr/local/bin/deno && \
    rm /tmp/deno.zip

# Work dir
WORKDIR /app

# Python deps
COPY web/requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# App code
COPY . .

# Data dirs
RUN mkdir -p /root/.kit/outputs /root/.kit/logs

ENV KIT_BASE_DIR=/root/.kit
ENV PYTHONUNBUFFERED=1

EXPOSE 7700

CMD ["uvicorn", "web.app:app", "--host", "0.0.0.0", "--port", "7700"]
