#!/usr/bin/env python3
"""
BeaconBase - インフラ統合監視システム

使用方法:
    python monitor.py -c config.yaml
    python monitor.py -c config.yaml --only ping,ports
    python monitor.py -c config.yaml --validate

終了コード:
    0: 正常終了
    1: エラー発生（設定エラーなど）
    2: 監視エラー（一部の監視が失敗）
    130: ユーザーによる中断
"""

from __future__ import annotations

import argparse
import logging
import sys

from beaconbase import (
    CHECK_CATEGORIES,
    CheckStatus,
    MonitoringError,
    MonitoringSystem,
    __version__,
    is_monitoring_failure,
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
  python monitor.py -c config.yaml --validate

カテゴリ: logs, ping, ports, disk, docker, web_health

定期実行する場合は cron やタスク スケジューラから、このコマンドを都度起動する。
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

    def run(self) -> int:
        self._set_log_level()
        self.logger.info("BeaconBase を開始します")

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
                    print(f"ダッシュボード: {dash}/index.html （監視実行後）")
                    return self.EXIT_SUCCESS

                self.logger.info("監視を実行します...")
                results = monitor.run_all_checks(categories=categories)

                summary = monitor.format_results_summary(results)
                if not self.args.quiet:
                    print(summary)
                else:
                    self.logger.info(summary)

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

        except KeyboardInterrupt:
            self.logger.info("ユーザーにより中断されました")
            return self.EXIT_KEYBOARD_INTERRUPT
        except MonitoringError as e:
            self.logger.error(f"監視システムのエラー: {e}")
            return self.EXIT_ERROR
        except Exception as e:
            self.logger.error(f"予期しないエラー: {e}", exc_info=True)
            return self.EXIT_ERROR


def main(argv=None) -> int:
    cli = MonitoringCLI(argv)
    return cli.run()


if __name__ == "__main__":
    sys.exit(main())
