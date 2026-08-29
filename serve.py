#!/usr/bin/env python3
"""BeaconBase 常駐エントリ（定期監視 + WEB）。

環境変数:
  BEACONBASE_CONFIG   設定ファイルパス（既定: config.yaml）
  BEACONBASE_HOST     バインドアドレス（既定: 0.0.0.0）
  BEACONBASE_PORT     ポート（既定: 8080）
  BEACONBASE_INTERVAL 監視間隔秒（未設定なら settings.check_interval）
"""

from __future__ import annotations

import argparse
import logging
import os
import sys

from scheduler import MonitoringScheduler, resolve_check_interval
from webapp import create_app


def _setup_logging(verbose: bool = False) -> None:
    level = logging.DEBUG if verbose else logging.INFO
    logging.basicConfig(
        level=level,
        format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    )


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description="BeaconBase 常駐サーバー（監視 + WEB）")
    parser.add_argument(
        "-c",
        "--config",
        default=os.environ.get("BEACONBASE_CONFIG", "config.yaml"),
        help="設定ファイルパス",
    )
    parser.add_argument(
        "--host",
        default=os.environ.get("BEACONBASE_HOST", "0.0.0.0"),
        help="WEB のバインドアドレス",
    )
    parser.add_argument(
        "--port",
        type=int,
        default=int(os.environ.get("BEACONBASE_PORT", "8080")),
        help="WEB ポート",
    )
    parser.add_argument(
        "--interval",
        type=float,
        default=None,
        help="監視間隔（秒）。省略時は設定または環境変数 BEACONBASE_INTERVAL",
    )
    parser.add_argument("-v", "--verbose", action="store_true")
    parser.add_argument(
        "--no-initial-run",
        action="store_true",
        help="起動直後の監視をスキップする",
    )
    args = parser.parse_args(argv)
    _setup_logging(args.verbose)

    interval = args.interval
    if interval is None and os.environ.get("BEACONBASE_INTERVAL"):
        interval = float(os.environ["BEACONBASE_INTERVAL"])

    if not os.path.isfile(args.config):
        logging.error(f"設定ファイルが見つかりません: {args.config}")
        return 1

    scheduler = MonitoringScheduler(args.config, interval=interval)
    logging.info(
        "監視間隔: %s 秒",
        resolve_check_interval({}, interval)
        if interval is not None
        else "settings.check_interval または 300",
    )
    scheduler.start(run_immediately=not args.no_initial_run)

    app = create_app(args.config, scheduler=scheduler)
    logging.info("WEB を開始します: http://%s:%s", args.host, args.port)
    try:
        # 開発用サーバで十分（コンテナ常駐向け）。本番負荷は想定しない。
        app.run(host=args.host, port=args.port, threaded=True, use_reloader=False)
    finally:
        scheduler.stop()
    return 0


if __name__ == "__main__":
    sys.exit(main())
