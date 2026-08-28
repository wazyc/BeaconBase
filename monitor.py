#!/usr/bin/env python3
"""
BeaconBase - インフラ統合監視システム

使用方法:
    python monitor.py -c config.yaml
    python monitor.py -c config.yaml --only ping,ports
    python monitor.py -c config.yaml --interval 60 --serve 8088
    python monitor.py -c config.yaml --validate

終了コード:
    0: 正常終了
    1: エラー発生（設定エラーなど）
    2: 監視エラー（一部の監視が失敗）※定期実行中は終了せず継続する
    130: ユーザーによる中断
"""

from __future__ import annotations

import argparse
import logging
import socket
import sys
import threading
import time
from typing import Optional
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer

from beaconbase import (
    CHECK_CATEGORIES,
    CheckStatus,
    MonitoringError,
    MonitoringSystem,
    __version__,
    is_monitoring_failure,
)


class _DashboardHandler(SimpleHTTPRequestHandler):
    """output フォルダだけを公開する。ログは BeaconBase-HTTP へ。"""

    def log_message(self, format, *args):
        logging.getLogger("BeaconBase-HTTP").info(
            "%s - %s", self.address_string(), format % args
        )


class MonitoringCLI:
    """BeaconBaseのコマンドラインインターフェース"""

    EXIT_SUCCESS = 0
    EXIT_ERROR = 1
    EXIT_MONITORING_ERROR = 2
    EXIT_KEYBOARD_INTERRUPT = 130

    def __init__(self, argv=None):
        self.logger = self._setup_logger()
        self.args = self._parse_arguments(argv)

    def _setup_logger(self) -> logging.Logger:
        logging.basicConfig(
            format="%(asctime)s - %(name)s - %(levelname)s - %(message)s"
        )
        return logging.getLogger("BeaconBase-CLI")

    def _parse_arguments(self, argv=None) -> argparse.Namespace:
        parser = argparse.ArgumentParser(
            description="BeaconBase - ローカルネットワーク監視",
            formatter_class=argparse.RawDescriptionHelpFormatter,
            epilog="""
例:
  python monitor.py -c config.yaml
  python monitor.py -c config.yaml --only ping,ports
  python monitor.py -c config.yaml --interval 60 --serve 8088
  python monitor.py -c config.yaml --validate

カテゴリ: logs, ping, ports, disk, docker, web_health
            """,
        )
        parser.add_argument(
            "--config",
            "-c",
            default="config.yaml",
            help="監視設定のメインYAMLパス（デフォルト: config.yaml）",
        )
        parser.add_argument(
            "--verbose",
            "-v",
            action="store_true",
            help="詳細なログ出力を有効化",
        )
        parser.add_argument(
            "--quiet",
            "-q",
            action="store_true",
            help="警告以上のみ出力する（cron 向け）",
        )
        parser.add_argument(
            "--only",
            metavar="CATEGORIES",
            help=(
                "実行する監視カテゴリをカンマ区切りで指定する"
                f"（{', '.join(CHECK_CATEGORIES)}）"
            ),
        )
        parser.add_argument(
            "--validate",
            action="store_true",
            help="設定の検証のみ行い、監視は実行しない",
        )
        parser.add_argument(
            "--interval",
            nargs="?",
            const=-1,
            type=float,
            default=None,
            metavar="SECONDS",
            help="指定秒ごとに繰り返し実行する。数値省略時は 60 秒（settings.interval_seconds があればそれを使う）",
        )
        parser.add_argument(
            "--serve",
            nargs="?",
            const=8088,
            type=int,
            default=None,
            metavar="PORT",
            help="結果フォルダを HTTP で公開する（ポート省略時 8088）。LAN のブラウザでダッシュボードを見る",
        )
        parser.add_argument(
            "--bind",
            default="0.0.0.0",
            help="--serve の待ち受けアドレス（デフォルト: 0.0.0.0）",
        )
        parser.add_argument(
            "--version",
            action="version",
            version=f"BeaconBase {__version__}",
        )
        return parser.parse_args(argv)

    def _set_log_level(self):
        if self.args.quiet:
            log_level = logging.WARNING
        elif self.args.verbose:
            log_level = logging.DEBUG
        else:
            log_level = logging.INFO
        self.logger.setLevel(log_level)
        logging.getLogger("BeaconBase").setLevel(log_level)

    def _parse_only(self):
        if not self.args.only:
            return None
        selected = [part.strip() for part in self.args.only.split(",") if part.strip()]
        if not selected:
            raise MonitoringError("--only にカテゴリが指定されていません")
        unknown = [name for name in selected if name not in CHECK_CATEGORIES]
        if unknown:
            raise MonitoringError(
                f"不明なカテゴリです: {', '.join(unknown)}。"
                f"指定できる値: {', '.join(CHECK_CATEGORIES)}"
            )
        return selected

    def _interval_seconds(self, monitor: MonitoringSystem) -> Optional[float]:
        if self.args.interval is None:
            return None
        if self.args.interval < 0:
            settings = monitor.config.get("settings") or {}
            return float(settings.get("interval_seconds", 60))
        return float(self.args.interval)

    def _start_dashboard_server(self, directory: str):
        port = int(self.args.serve)
        bind = self.args.bind
        handler = partial(_DashboardHandler, directory=directory)
        httpd = ThreadingHTTPServer((bind, port), handler)
        thread = threading.Thread(target=httpd.serve_forever, daemon=True)
        thread.start()
        display_host = bind
        if bind in ("0.0.0.0", "::"):
            display_host = _guess_lan_ip() or "127.0.0.1"
        url = f"http://{display_host}:{port}/"
        self.logger.info(f"ダッシュボード: {url}  （フォルダ: {directory}）")
        if not self.args.quiet:
            print(f"ダッシュボード: {url}")
        return httpd

    def _interruptible_sleep(self, seconds: float) -> None:
        end = time.time() + max(0.0, seconds)
        while time.time() < end:
            time.sleep(min(0.5, end - time.time()))

    def _print_summary(self, monitor: MonitoringSystem, results) -> None:
        summary = monitor.format_results_summary(results)
        if not self.args.quiet:
            print(summary)
        else:
            self.logger.info(summary)

    def _exit_from_results(self, results) -> int:
        has_failures = any(
            is_monitoring_failure(category, result)
            for category, data in results.items()
            for result in data
        )
        has_warnings = any(
            result.status == CheckStatus.WARNING
            for data in results.values()
            for result in data
        )
        if has_failures:
            self.logger.warning("一部の監視が失敗しました")
            return self.EXIT_MONITORING_ERROR
        if has_warnings:
            self.logger.warning("警告付きで監視が完了しました")
        else:
            self.logger.info("すべての監視が正常に完了しました")
        return self.EXIT_SUCCESS

    def run(self) -> int:
        self._set_log_level()
        self.logger.info("BeaconBase を開始します")
        httpd = None
        try:
            categories = self._parse_only()
            with MonitoringSystem(self.args.config) as monitor:
                validation_error = monitor.validate_config()
                if validation_error:
                    self.logger.error(f"設定エラー: {validation_error}")
                    return self.EXIT_ERROR

                if self.args.validate:
                    enabled = monitor.enabled_categories()
                    self.logger.info("設定は問題ありません")
                    if enabled:
                        self.logger.info("有効な監視: " + ", ".join(enabled))
                    else:
                        self.logger.warning(
                            "有効な監視対象がありません（storage 以外が空です）"
                        )
                    print("設定は問題ありません。")
                    if enabled:
                        print("有効な監視: " + ", ".join(enabled))
                    dash = monitor.config["storage"]["output_folder"]
                    print(f"ダッシュボードファイル: {dash}/index.html （監視実行後）")
                    return self.EXIT_SUCCESS

                interval = self._interval_seconds(monitor)
                if self.args.serve is not None:
                    httpd = self._start_dashboard_server(
                        monitor.config["storage"]["output_folder"]
                    )

            cycle = 0
            last_code = self.EXIT_SUCCESS
            while True:
                cycle += 1
                try:
                    with MonitoringSystem(self.args.config) as monitor:
                        validation_error = monitor.validate_config()
                        if validation_error:
                            self.logger.error(f"設定エラー: {validation_error}")
                            if interval is None:
                                return self.EXIT_ERROR
                            self._interruptible_sleep(interval)
                            continue
                        selected = monitor.resolve_categories(
                            categories, interval_mode=interval is not None
                        )
                        if interval:
                            self.logger.info(f"監視サイクル {cycle} を実行します")
                        else:
                            self.logger.info("監視を実行します...")
                        results = monitor.run_all_checks(categories=selected)
                        self._print_summary(monitor, results)
                        last_code = self._exit_from_results(results)
                except MonitoringError as e:
                    self.logger.error(f"監視システムのエラー: {e}")
                    if interval is None:
                        return self.EXIT_ERROR
                    last_code = self.EXIT_ERROR
                except Exception as e:
                    self.logger.error(f"予期しないエラー: {e}", exc_info=True)
                    if interval is None:
                        return self.EXIT_ERROR
                    last_code = self.EXIT_ERROR

                if interval is None:
                    if self.args.serve is not None:
                        self.logger.info("ダッシュボードを公開したまま待機します（Ctrl+C で終了）")
                        while True:
                            time.sleep(3600)
                    return last_code

                self._interruptible_sleep(interval)

        except KeyboardInterrupt:
            self.logger.info("ユーザーにより中断されました")
            return self.EXIT_KEYBOARD_INTERRUPT
        except MonitoringError as e:
            self.logger.error(f"監視システムのエラー: {e}")
            return self.EXIT_ERROR
        except Exception as e:
            self.logger.error(f"予期しないエラー: {e}", exc_info=True)
            return self.EXIT_ERROR
        finally:
            if httpd is not None:
                httpd.shutdown()


def _guess_lan_ip() -> str:
    """表示用に LAN IP を推定する。失敗したら空文字。"""
    try:
        sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        try:
            sock.connect(("192.0.2.1", 80))
            return sock.getsockname()[0]
        finally:
            sock.close()
    except OSError:
        return ""


def main(argv=None) -> int:
    cli = MonitoringCLI(argv)
    return cli.run()


if __name__ == "__main__":
    sys.exit(main())
