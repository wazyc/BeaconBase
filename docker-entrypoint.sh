#!/bin/sh
# BeaconBase コンテナ起動時に /data を初期化し、常駐プロセスを起動する。
set -eu

DATA_DIR="${BEACONBASE_DATA:-/data}"
CONFIG="${BEACONBASE_CONFIG:-$DATA_DIR/config.yaml}"
SAMPLES="${BEACONBASE_SAMPLES:-/opt/beaconbase/samples}"

mkdir -p "$DATA_DIR/output" "$DATA_DIR/config.d"

if [ ! -f "$CONFIG" ]; then
  echo "初期設定を $CONFIG にコピーします"
  cp "$SAMPLES/config.yaml" "$CONFIG"
fi

# config.d が空ならサンプル断片を入れる
if [ -z "$(ls -A "$DATA_DIR/config.d" 2>/dev/null || true)" ]; then
  echo "初期断片設定を $DATA_DIR/config.d にコピーします"
  cp -a "$SAMPLES/config.d/." "$DATA_DIR/config.d/"
fi

export BEACONBASE_CONFIG="$CONFIG"
cd /app
exec python serve.py "$@"
