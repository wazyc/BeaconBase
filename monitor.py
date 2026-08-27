#!/usr/bin/env python3
"""
BeaconBase - インフラ統合監視システム

このスクリプトは、BeaconBaseの監視機能を実行するためのコマンドラインインターフェースを提供します。
設定ファイルに基づいて以下の監視を実行します：
- サーバーログの収集
- ネットワーク機器のPing監視
- Dockerコンテナの状態監視
- Web APIの健全性チェック
- Webページのヘルスチェック

監視結果は以下のファイルに出力されます：
- check_summary.json: 全ての監視結果
- error_summary.json: エラーのみの監視結果（正常ではないチェック結果のみ）
- log_summary.log: ログ収集のサマリー

使用方法:
    python monitor.py -c config.yaml
    python monitor.py -c config.yaml --only ping,web_health
    python monitor.py -c config.yaml --validate

オプション:
    -c, --config     設定ファイルのパス（デフォルト: config.yaml）
    -v, --verbose    詳細なログ出力を有効化
    -q, --quiet      警告以上のみ出力
    --only           実行するカテゴリ（カンマ区切り）
    --validate       設定の検証のみ行う
    --version        バージョンを表示

終了コード:
    0: 正常終了
    1: エラー発生（設定エラーなど）
    2: 監視エラー（一部の監視が失敗）
    130: ユーザーによる中断
"""

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
    """BeaconBaseのコマンドラインインターフェース

    このクラスは、コマンドライン引数の解析と
    MonitoringSystemの実行を管理します。
    """

    EXIT_SUCCESS = 0
    EXIT_ERROR = 1
    EXIT_MONITORING_ERROR = 2
    EXIT_KEYBOARD_INTERRUPT = 130

    def __init__(self, argv=None):
        """CLIの初期化

        Args:
            argv: 引数リスト。None なら sys.argv を使う（テスト用に差し替え可能）。
        """
        self.logger = self._setup_logger()
        self.args = self._parse_arguments(argv)

    def _setup_logger(self) -> logging.Logger:
        """ロギングの設定"""
        logging.basicConfig(
            format="%(asctime)s - %(name)s - %(levelname)s - %(message)s"
        )
        return logging.getLogger("BeaconBase-CLI")

    def _parse_arguments(self, argv=None) -> argparse.Namespace:
        """コマンドライン引数の解析"""
        parser = argparse.ArgumentParser(
            description="BeaconBase - インフラ統合監視システム",
            formatter_class=argparse.RawDescriptionHelpFormatter,
            epilog="""
例:
  # 設定どおりに全監視を実行
  python monitor.py -c config.yaml

  # Ping と Web だけ実行
  python monitor.py -c config.yaml --only ping,web_health

  # 設定の検証のみ（監視は走らせない）
  python monitor.py -c config.yaml --validate

  # 詳細ログ
  python monitor.py -c config.yaml -v

カテゴリ: logs, ping, docker, web_health
            """,
        )
        parser.add_argument(
            "--config",
            "-c",
            default="config.yaml",
            help="監視設定のメインYAMLパス（ルートの includes_dir で分割ディレクトリを読み込み可。デフォルト: config.yaml）",
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
        """ログレベルの設定"""
        if self.args.quiet:
            log_level = logging.WARNING
        elif self.args.verbose:
            log_level = logging.DEBUG
        else:
            log_level = logging.INFO
        self.logger.setLevel(log_level)
        logging.getLogger("BeaconBase").setLevel(log_level)

    def _parse_only(self):
        """--only の値をカテゴリのリストにする。未指定なら None。"""
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
        """監視の実行

        Returns:
            int: プロセスの終了コード
        """
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
    """メインエントリーポイント"""
    cli = MonitoringCLI(argv)
    return cli.run()


if __name__ == "__main__":
    sys.exit(main())
