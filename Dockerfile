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
RUN curl -fsSL https://deno.land/install.sh | sh
ENV DENO_INSTALL="/root/.deno"
ENV PATH="${DENO_INSTALL}/bin:${PATH}"

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
