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
    python monitor.py -c config.yaml [-v]

オプション:
    -c, --config    設定ファイルのパス（デフォルト: config.yaml）
    -v, --verbose   詳細なログ出力を有効化

終了コード:
    0: 正常終了
    1: エラー発生（設定エラーなど）
    2: 監視エラー（一部の監視が失敗）
    130: ユーザーによる中断
"""

import argparse
import sys
import os
import logging
from typing import Optional
from beaconbase import MonitoringSystem, MonitoringError


class MonitoringCLI:
    """BeaconBaseのコマンドラインインターフェース
    
    このクラスは、コマンドライン引数の解析と
    MonitoringSystemの実行を管理します。
    """

    EXIT_SUCCESS = 0
    EXIT_ERROR = 1
    EXIT_MONITORING_ERROR = 2
    EXIT_KEYBOARD_INTERRUPT = 130

    def __init__(self):
        """CLIの初期化"""
        self.logger = self._setup_logger()
        self.args = self._parse_arguments()

    def _setup_logger(self) -> logging.Logger:
        """ロギングの設定
        
        Returns:
            設定済みのLoggerインスタンス
        """
        logging.basicConfig(
            format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
        )
        logger = logging.getLogger('BeaconBase-CLI')
        return logger

    def _parse_arguments(self) -> argparse.Namespace:
        """コマンドライン引数の解析
        
        Returns:
            解析済みの引数
        """
        parser = argparse.ArgumentParser(
            description='BeaconBase - インフラ統合監視システム',
            formatter_class=argparse.RawDescriptionHelpFormatter,
            epilog="""
例:
    # 通常実行
    python monitor.py -c config.yaml
    
    # 詳細ログ付きで実行
    python monitor.py -c config.yaml -v
            """
        )
        parser.add_argument(
            '--config', '-c',
            default="config.yaml",
            help='監視設定を含むYAMLファイルのパス（デフォルト: config.yaml）'
        )
        parser.add_argument(
            '--verbose', '-v',
            action='store_true',
            help='詳細なログ出力を有効化'
        )
        return parser.parse_args()

    def _set_log_level(self):
        """ログレベルの設定"""
        log_level = logging.DEBUG if self.args.verbose else logging.INFO
        self.logger.setLevel(log_level)
        logging.getLogger('BeaconBase').setLevel(log_level)

    def run(self) -> int:
        """監視の実行
        
        Returns:
            int: プロセスの終了コード
        """
        self._set_log_level()
        self.logger.info("Starting BeaconBase monitoring system...")

        try:
            config_file_path = self.args.config if self.args.config else os.path.join(os.path.dirname(os.path.abspath(self.args.config)), str(self.args.config))
            with MonitoringSystem(config_file_path) as monitor:
                # 設定ファイルの検証
                validation_error = monitor.validate_config()
                if validation_error:
                    self.logger.error(f"Configuration error: {validation_error}")
                    return self.EXIT_ERROR

                # 監視の実行
                self.logger.info("Running monitoring checks...")
                results = monitor.run_all_checks()

                # 結果の確認
                has_errors = any(
                    result.status == 'ERROR'
                    for category in results.values()
                    for result in category
                )

                if has_errors:
                    self.logger.warning("Some monitoring checks failed")
                    return self.EXIT_MONITORING_ERROR
                
                self.logger.info("All monitoring checks completed successfully")
                return self.EXIT_SUCCESS

        except KeyboardInterrupt:
            self.logger.info("Monitoring interrupted by user")
            return self.EXIT_KEYBOARD_INTERRUPT
        except MonitoringError as e:
            self.logger.error(f"Monitoring system error: {e}")
            return self.EXIT_ERROR
        except Exception as e:
            self.logger.error(f"Unexpected error: {e}", exc_info=True)
            return self.EXIT_ERROR


def main() -> int:
    """メインエントリーポイント
    
    Returns:
        int: プロセスの終了コード
    """
    cli = MonitoringCLI()
    return cli.run()


if __name__ == '__main__':
    sys.exit(main())
