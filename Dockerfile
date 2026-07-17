# SPDX-License-Identifier: GPL-2.0-only
FROM ubuntu:24.04

ENV DEBIAN_FRONTEND=noninteractive \
    PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1

RUN apt-get update && apt-get install -y --no-install-recommends \
    ca-certificates \
    git \
    libbz2-1.0 \
    libcurl4t64 \
    libicu74 \
    liblzma5 \
    openssh-client \
    python3 \
    python3-pip \
    python3-venv \
    zlib1g \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app
COPY . .
RUN python3 -m venv /opt/venv \
    && /opt/venv/bin/pip install --no-cache-dir --upgrade pip \
    && /opt/venv/bin/pip install --no-cache-dir .

ENV PATH="/opt/venv/bin:${PATH}"

# The pinned release is installed on first use. Private getbiblesword releases
# require GETBIBLESWORD_TOKEN at runtime; no credential is baked into the image.
ENTRYPOINT ["study-builder"]
CMD ["--help"]
