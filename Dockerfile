# BeaconBase 常駐コンテナ
FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    BEACONBASE_DATA=/data \
    BEACONBASE_CONFIG=/data/config.yaml \
    BEACONBASE_HOST=0.0.0.0 \
    BEACONBASE_PORT=8080

# Ping フォールバックと SSH クライアント用
RUN apt-get update \
    && apt-get install -y --no-install-recommends \
        iputils-ping \
        openssh-client \
        curl \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY . /app
# コンテナ初回用のサンプル設定
COPY docker/config.yaml /opt/beaconbase/samples/config.yaml
COPY docker/config.d /opt/beaconbase/samples/config.d

RUN chmod +x /app/docker-entrypoint.sh \
    && mkdir -p /data/output /data/config.d

VOLUME ["/data"]
EXPOSE 8080

ENTRYPOINT ["/app/docker-entrypoint.sh"]
