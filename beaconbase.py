"""
BeaconBase - インフラ統合監視システム

このモジュールは、サーバー、ネットワーク機器、コンテナ、Webサービスの統合監視機能を提供します。

主な機能:
    - サーバーログの自動収集
    - ネットワーク機器のPing監視
    - Dockerコンテナの状態監視
    - Web APIの健全性チェック
    - Webページのヘルスチェック

監視結果は指定されたoutputフォルダにJSON形式で保存され、
サマリーレポートが自動生成されます。

Classes:
    MonitoringError: 監視システムの基本例外クラス
    RetryableError: リトライ可能なエラーを示す例外クラス
    MonitoringSystem: 監視システムの中核クラス

Typical usage example:
    with MonitoringSystem("config.yaml") as monitor:
        monitor.run_all_checks()
"""

import yaml
import subprocess
import docker
import requests
from datetime import datetime
import os
import logging
import json
import time
import paramiko
from typing import Dict, List, Any, Optional, Tuple
from concurrent.futures import ThreadPoolExecutor, as_completed
from functools import partial
import socket
import ping3
from dataclasses import dataclass
from enum import Enum, auto


class MonitoringError(Exception):
    """監視システムの基本例外クラス"""
    pass


class RetryableError(MonitoringError):
    """リトライ可能なエラーを示す例外クラス"""
    pass


class CheckStatus(Enum):
    """監視チェックの状態を表す列挙型"""
    OK = auto()
    WARNING = auto()
    ERROR = auto()
    NOT_FOUND = auto()


@dataclass
class CheckResult:
    """監視チェック結果を格納するデータクラス
    
    Attributes:
        name: チェック対象の名前
        status: チェックの状態
        timestamp: チェック実行時刻
        details: 詳細情報を含む辞書
    """
    name: str
    status: CheckStatus
    timestamp: str
    details: Dict[str, Any]


class MonitoringSystem:
    """システム監視の中核クラス
    
    設定ファイルに基づいて、サーバー、ネットワーク機器、
    Dockerコンテナ、Webサービスの監視を行います。
    
    Attributes:
        config: 監視設定を含むdict
        logger: ロギングインスタンス
        retry_count: リトライ回数
        retry_delay: リトライ間隔（秒）
        timeout: 操作タイムアウト（秒）
        max_workers: 並列実行数
    """

    DEFAULT_RETRY_COUNT = 3
    DEFAULT_RETRY_DELAY = 5
    DEFAULT_TIMEOUT = 30
    DEFAULT_MAX_WORKERS = 5

    def __init__(self, config_path: str):
        """初期化
        
        Args:
            config_path: YAML形式の設定ファイルパス
            
        Raises:
            MonitoringError: 設定ファイルの読み込みに失敗した場合
        """
        try:
            with open(config_path, 'r', encoding="utf-8") as f:
                self.config = yaml.safe_load(f)
        except Exception as e:
            raise MonitoringError(f"Failed to load config file: {e}")

        self._setup_logging()
        self._initialize_parameters()
        self._validate_and_create_directories()

    def _initialize_parameters(self):
        """パラメータの初期化"""
        self.retry_count = self.DEFAULT_RETRY_COUNT
        self.retry_delay = self.DEFAULT_RETRY_DELAY
        self.timeout = self.DEFAULT_TIMEOUT
        self.max_workers = self.DEFAULT_MAX_WORKERS

    def _validate_and_create_directories(self):
        """出力ディレクトリの検証と作成"""
        try:
            output_dir = self.config['storage']['output_folder']
            os.makedirs(output_dir, exist_ok=True)
            os.makedirs(os.path.join(output_dir, 'logs'), exist_ok=True)
        except Exception as e:
            raise MonitoringError(f"Failed to create output directories: {e}")

    def _setup_logging(self):
        """ロギングの設定"""
        logging.basicConfig(
            level=logging.INFO,
            format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
        )
        self.logger = logging.getLogger('BeaconBase')

    def retry_operation(self, operation, *args, **kwargs) -> Any:
        """操作のリトライ処理
        
        Args:
            operation: リトライする関数
            *args: 関数の位置引数
            **kwargs: 関数のキーワード引数
            
        Returns:
            関数の実行結果
            
        Raises:
            RetryableError: すべてのリトライが失敗した場合
        """
        last_error = None
        for attempt in range(self.retry_count):
            try:
                return operation(*args, **kwargs)
            except RetryableError as e:
                last_error = e
                self.logger.warning(
                    f"Retry attempt {attempt + 1} of {self.retry_count} failed: {e}"
                )
                if attempt < self.retry_count - 1:
                    time.sleep(self.retry_delay)
                continue
        raise last_error

    def run_all_checks(self) -> Dict[str, List[CheckResult]]:
        """全ての監視チェックを並列実行
        
        Returns:
            Dict[str, List[CheckResult]]: カテゴリごとのチェック結果
            
        Raises:
            MonitoringError: 監視チェックの実行に失敗した場合
        """
        try:
            with ThreadPoolExecutor(max_workers=self.max_workers) as executor:
                check_functions = {
                    'logs': self.collect_logs,
                    'ping': self.check_ping,
                    'docker': self.check_docker_containers,
                    'web_health': self.check_web_health
                }
                
                future_to_check = {
                    executor.submit(func): check_type
                    for check_type, func in check_functions.items()
                }

                results = {}
                for future in as_completed(future_to_check):
                    check_type = future_to_check[future]
                    try:
                        data = future.result(timeout=self.timeout)
                        self._save_results(data, check_type)
                        results[check_type] = data
                    except Exception as e:
                        error_msg = f"Error in {check_type} check: {e}"
                        self.logger.error(error_msg)
                        results[check_type] = [
                            CheckResult(
                                name=check_type,
                                status=CheckStatus.ERROR,
                                timestamp=datetime.now().isoformat(),
                                details={'error': str(e)}
                            )
                        ]

            # 監視結果のサマリーを出力
            self._write_check_summary(results)
            # エラーのみのサマリーを出力
            self._write_error_summary(results)
            return results

        except Exception as e:
            raise MonitoringError(f"Error during monitoring checks: {e}")

    def collect_logs(self) -> List[CheckResult]:
        """すべての対象サーバーからログを収集
        
        設定ファイルで指定された各サーバーに接続し、
        指定されたログファイルを収集します。
        
        Returns:
            List[CheckResult]: 収集したログファイルの情報
        """
        results = []
        for server in self.config['log_collection']['servers']:
            try:
                logs = self.retry_operation(self._collect_server_logs, server)
                results.extend(logs)
            except Exception as e:
                self.logger.error(f"Failed to collect logs from {server['name']}: {e}")
                results.append(CheckResult(
                    name=server['name'],
                    status=CheckStatus.ERROR,
                    timestamp=datetime.now().isoformat(),
                    details={'error': str(e)}
                ))
        
        return results

    def _get_ssh_config(self, server: Dict[str, Any]) -> Dict[str, str]:
        """サーバーのSSH設定を取得
        
        サーバー固有のSSH設定がない場合は、デフォルト設定を使用します。
        
        Args:
            server: サーバー設定を含むdict
        
        Returns:
            SSH接続に必要なusernameとkey_pathを含むdict
        """
        default_ssh = self.config.get('default_ssh', {})
        return {
            'username': server.get('ssh_username', default_ssh.get('username')),
            'key_path': server.get('ssh_key_path', default_ssh.get('key_path')),
            'port': server.get('ssh_port', default_ssh.get('key_path'))
        }

    def _collect_server_logs(self, server: Dict[str, Any]) -> List[CheckResult]:
        """個別サーバーからのログ収集
        
        Args:
            server: サーバー設定を含むdict
            
        Returns:
            List[CheckResult]: 収集したログファイルの情報
        """
        collected_logs = []
        missing_logs = []
        ssh = paramiko.SSHClient()
        ssh.set_missing_host_key_policy(paramiko.AutoAddPolicy())
        
        try:
            ssh_config = self._get_ssh_config(server)
            if not ssh_config['username'] or not ssh_config['key_path']:
                raise RetryableError(f"Missing SSH configuration for server {server['name']}")
            
            ssh.connect(
                server['host'],
                username=ssh_config['username'],
                key_filename=ssh_config['key_path'],
                port=ssh_config['port'],
            )
            
            timestamp = datetime.now().strftime('%Y%m%d_%H%M%S')
            sftp = ssh.open_sftp()
            
            try:
                for log_path in server['log_paths']:
                    local_filename = f"{os.path.basename(log_path)}"
                    local_path = os.path.join(
                        self.config['storage']['output_folder'],
                        'logs',
                        server['name'],
                        local_filename
                    )
                    
                    os.makedirs(os.path.dirname(local_path), exist_ok=True)
                    
                    try:
                        # ファイルの存在確認
                        sftp.stat(log_path)
                        
                        # ファイルが存在する場合のみ取得を実行
                        sftp.get(log_path, local_path)
                        
                        # 設定に基づいてログ削除を実行
                        delete_after_collection = self.config['log_collection'].get('delete_after_collection', False)
                        if delete_after_collection:
                            sftp.remove(log_path)
                            delete_status = 'deleted'
                        else:
                            delete_status = 'preserved'
                        
                        collected_logs.append(CheckResult(
                            name=f"{server['name']}_{os.path.basename(log_path)}",
                            status=CheckStatus.OK,
                            timestamp=datetime.now().isoformat(),
                            details={
                                'server': server['name'],
                                'source_path': log_path,
                                'local_path': local_path,
                                'status': 'collected',
                                'server_file_status': delete_status
                            }
                        ))
                        
                    except FileNotFoundError:
                        missing_logs.append(CheckResult(
                            name=f"{server['name']}_{os.path.basename(log_path)}",
                            status=CheckStatus.NOT_FOUND,
                            timestamp=datetime.now().isoformat(),
                            details={
                                'server': server['name'],
                                'source_path': log_path,
                                'status': 'not_found',
                                'message': 'File not found on server'
                            }
                        ))
                        self.logger.warning(f"Log file not found on server: {log_path}")
                
                return collected_logs + missing_logs
                
            finally:
                sftp.close()
                
        except Exception as e:
            error_message = f"Failed to collect logs from {server['name']}: {e}"
            self.logger.error(error_message)
            return [CheckResult(
                name=server['name'],
                status=CheckStatus.ERROR,
                timestamp=datetime.now().isoformat(),
                details={
                    'server': server['name'],
                    'status': 'error',
                    'message': error_message
                }
            )]
        finally:
            ssh.close()

    def check_ping(self) -> List[CheckResult]:
        """Ping疎通確認を実行
        
        Returns:
            List[CheckResult]: 各ターゲットのPing結果
        """
        results = []
        for target in self.config['ping_targets']:
            response_time = self._ping_host(target['host'])
            
            if response_time is not None:
                status = CheckStatus.OK
                details = {'response_time': response_time}
            else:
                status = CheckStatus.ERROR
                details = {'error': 'Host unreachable'}
            
            results.append(CheckResult(
                name=target['name'],
                status=status,
                timestamp=datetime.now().isoformat(),
                details=details
            ))
        return results

    def _ping_host(self, host: str) -> Optional[float]:
        """個別のホストにPing実行
        
        ping3ライブラリを使用してPingを実行します。
        
        Args:
            host: 対象ホストのIPアドレスまたはホスト名
            
        Returns:
            Optional[float]: 応答時間（秒）、到達不可能な場合はNone
        """
        try:
            # タイムアウトを5秒に設定してping実行
            result = ping3.ping(host, timeout=5)

            # デバッグ用にping結果を記録
            self.logger.debug(f"Ping result for {host}: {result}")

            # ping3は到達可能な場合は応答時間（float）を返し、
            # 到達不可能な場合はNoneまたはFalseを返す
            if result is not None and result is not False:
                return float(result)
            return None

        except Exception as e:
            self.logger.error(f"Unexpected error during ping to {host}: {str(e)}")
            return None

    def check_docker_containers(self) -> List[CheckResult]:
        """Dockerコンテナの状態を確認
        
        SSH経由で各サーバーのDockerコンテナの状態を確認します。
        Webコンテナの場合は、HTTPヘルスチェックも実行します。
        
        Returns:
            List[CheckResult]: 各コンテナの状態情報
        """
        results = []
        for server in self.config['docker_monitoring']['servers']:
            try:
                # SSHクライアントの設定
                ssh = paramiko.SSHClient()
                ssh.set_missing_host_key_policy(paramiko.AutoAddPolicy())

                # SSH接続情報の取得と検証
                ssh_config = self._get_ssh_config(server)
                if not ssh_config['username'] or not ssh_config['key_path']:
                    raise Exception(f"Missing SSH configuration for server {server['host']}")

                # サーバーへの接続
                ssh.connect(
                    server['host'],
                    username=ssh_config['username'],
                    key_filename=ssh_config['key_path'],
                    port=ssh_config['port']
                )

                # 各コンテナの状態確認
                for container in server['containers']:
                    container_status = self._check_container_via_ssh(ssh, container)
                    container_status['host'] = server['host']
                    
                    if container_status.get('status') == 'NOT_FOUND':
                        status = CheckStatus.NOT_FOUND
                    elif container_status.get('status', '').startswith('Up'):
                        status = CheckStatus.OK
                    else:
                        status = CheckStatus.ERROR
                    
                    results.append(CheckResult(
                        name=container['name'],
                        status=status,
                        timestamp=datetime.now().isoformat(),
                        details=container_status
                    ))

            except Exception as e:
                self.logger.error(f"Failed to connect to server {server['host']}: {e}")
                results.append(CheckResult(
                    name=f"server_{server['host']}",
                    status=CheckStatus.ERROR,
                    timestamp=datetime.now().isoformat(),
                    details={'error': str(e), 'host': server['host']}
                ))
            finally:
                ssh.close()

        return results

    def _check_container_via_ssh(self, ssh, container):
        """SSHを使用して個別のコンテナ状態確認"""
        try:
            # docker ps でコンテナの状態を確認
            cmd = f"docker ps --filter name=^/{container['name']}$ --format '{{{{.Status}}}}'"
            stdin, stdout, stderr = ssh.exec_command(cmd)
            status = stdout.read().decode().strip()

            result = {
                'name': container['name'],
                'status': status if status else 'NOT_FOUND'
            }

            if status:
                # docker inspect でコンテナの詳細情報を取得
                cmd = f"docker inspect {container['name']}"
                stdin, stdout, stderr = ssh.exec_command(cmd)
                inspect_data = json.loads(stdout.read().decode())[0]

                result.update({
                    'created': inspect_data['Created'],
                    'state': inspect_data['State']
                })

            if container.get('type') == 'web' and container.get('health_check_url'):
                result['health_check'] = self._check_web_health(container['health_check_url'])

            return result

        except Exception as e:
            return {
                'name': container['name'],
                'status': 'ERROR',
                'error': str(e)
            }

    def _check_web_health(self, url):
        """Webアプリケーションの健全性確認（詳細情報付き）"""
        try:
            start_time = time.time()
            # SSL証明書の検証をスキップ
            response = requests.get(url, timeout=5, verify=False)
            response_time = time.time() - start_time

            # 警告メッセージを抑制
            import urllib3
            urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)

            return {
                'status': 'OK' if response.status_code == 200 else 'FAIL',
                'response_code': response.status_code,
                'response_time': round(response_time, 3)
            }
        except requests.RequestException as e:
            return {
                'status': 'FAIL',
                'error': str(e)
            }

    def check_web_health(self) -> List[CheckResult]:
        """Webページのヘルスチェックを実行
        
        設定ファイルで指定された各URLに対してHTTPリクエストを送信し、
        レスポンスコードが200であることを確認します。
        
        Returns:
            List[CheckResult]: 各URLのヘルスチェック結果
        """
        results = []
        if 'web_health_checks' not in self.config:
            return results

        for target in self.config['web_health_checks'].get('targets', []):
            try:
                response = requests.get(
                    target['url'],
                    timeout=target.get('timeout', 30),
                    verify=target.get('verify_ssl', True)
                )
                response_time = response.elapsed.total_seconds()

                details = {
                    'url': target['url'],
                    'response_code': response.status_code,
                    'response_time': response_time
                }

                results.append(CheckResult(
                    name=target['name'],
                    status=CheckStatus.OK if response.status_code == 200 else CheckStatus.ERROR,
                    timestamp=datetime.now().isoformat(),
                    details=details
                ))

            except requests.RequestException as e:
                self.logger.error(f"Failed to check {target['name']}: {e}")
                results.append(CheckResult(
                    name=target['name'],
                    status=CheckStatus.ERROR,
                    timestamp=datetime.now().isoformat(),
                    details={
                        'url': target['url'],
                        'error': str(e)
                    }
                ))

        return results

    def _save_results(self, data: List[CheckResult], category: str) -> None:
        """監視結果を指定されたカテゴリのJSONファイルに保存
        
        Args:
            data: 保存するCheckResultのリスト
            category: データのカテゴリ（logs/ping/docker/web_health）
        """
        timestamp = datetime.now().strftime('%Y%m%d')
        category_dir = os.path.join(self.config['storage']['output_folder'], category)
        filename = f"{category}_{timestamp}.json"
        path = os.path.join(category_dir, filename)

        os.makedirs(category_dir, exist_ok=True)

        # 既存のデータを読み込む
        existing_data = []
        if os.path.exists(path):
            try:
                with open(path, 'r') as f:
                    existing_data = json.load(f)
            except json.JSONDecodeError:
                self.logger.warning(f"Could not read existing data from {path}")

        # 新しいデータを追加
        new_data = [
            {
                'name': result.name,
                'status': result.status.name,
                'timestamp': result.timestamp,
                'details': result.details
            }
            for result in data
        ]
        existing_data.extend(new_data)

        # データを保存
        with open(path, 'w') as f:
            json.dump(existing_data, f, indent=2)

    def save_results(self, data: List[CheckResult], category: str) -> None:
        """監視結果をファイルに保存
        
        詳細な結果をカテゴリごとのJSONファイルに保存し、
        サマリーも作成します。
        
        Args:
            data: 保存するCheckResultのリスト
            category: データのカテゴリ（logs/ping/docker/web_health）
        """
        # 結果の保存
        timestamp = datetime.now().strftime('%Y%m%d')
        category_dir = os.path.join(self.config['storage']['output_folder'], category)
        filename = f"{category}_{timestamp}.json"
        path = os.path.join(category_dir, filename)

        os.makedirs(category_dir, exist_ok=True)

        # 新しいデータを保存用に変換
        new_data = [
            {
                'name': result.name,
                'status': result.status.name,
                'timestamp': result.timestamp,
                'details': result.details
            }
            for result in data
        ]

        # データを保存（上書き）
        with open(path, 'w') as f:
            json.dump(new_data, f, indent=2)

        # サマリーの作成と保存
        if category in ['ping', 'docker', 'web_health']:
            self._update_summary(category, data)
        elif category == 'logs':
            self._update_log_summary(data)

    def _update_summary(self, category: str, data: List[CheckResult]) -> None:
        """監視結果のサマリーを更新
        
        Args:
            category: データのカテゴリ（ping/docker/web_health）
            data: 新しい監視結果データ
        """
        summary_path = os.path.join(
            self.config['storage']['output_folder'],
            'monitoring_summary.json'
        )

        # 出力ディレクトリが存在しない場合は作成
        os.makedirs(os.path.dirname(summary_path), exist_ok=True)

        # 既存のサマリーを読み込む
        summary = {}
        if os.path.exists(summary_path):
            try:
                with open(summary_path, 'r') as f:
                    summary = json.load(f)
            except (json.JSONDecodeError, FileNotFoundError):
                self.logger.warning("Could not read existing summary")
        
        # カテゴリごとにサマリーを作成
        if category == 'ping':
            # ping_targetsから対象のホスト情報を取得
            ping_targets = {
                target['name']: target['host']
                for target in self.config['ping_targets']
            }

            summary['ping'] = [
                {
                    'name': result.name,
                    'ip': ping_targets.get(result.name, 'unknown'),
                    'status': result.status.name,
                    'details': result.details
                }
                for result in data
            ]
        elif category == 'docker':
            # docker_monitoringから対象のサーバー情報を取得
            server_info = {
                server['host']: {
                    'name': next(
                        (target['name'] for target in self.config['ping_targets']
                         if target['host'] == server['host']),
                        'unknown'
                    ),
                    'ip': server['host']
                }
                for server in self.config['docker_monitoring']['servers']
            }

            summary['docker'] = [
                {
                    'name': result.name,
                    'status': result.status.name,
                    'details': result.details
                }
                for result in data
            ]
        elif category == 'web_health':
            summary['web_health'] = [
                {
                    'name': result.name,
                    'status': result.status.name,
                    'details': result.details
                }
                for result in data
            ]

        # タイムスタンプを追加
        summary['last_updated'] = datetime.now().isoformat()

        # サマリーを保存
        with open(summary_path, 'w') as f:
            json.dump(summary, f, indent=2)

    def _update_log_summary(self, data: List[CheckResult]) -> None:
        """ログ収集結果のサマリーを更新
        
        Args:
            data: 収集したログファイルの情報
        """
        summary_path = os.path.join(
            self.config['storage']['output_folder'],
            'log_summary.log'
        )
        
        timestamp = datetime.now().strftime('%Y-%m-%d %H:%M:%S')
        
        # サマリー情報を作成
        summary_lines = [f"=== Log Collection ({timestamp}) ===\n"]
        
        # サーバーごとにグループ化
        servers = {}
        for result in data:
            server_name = result.details.get('server')
            if server_name not in servers:
                servers[server_name] = []
            servers[server_name].append(result)
        
        # サーバーごとのサマリーを作成
        for server_name, logs in servers.items():
            summary_lines.append(f"\nServer: {server_name}")
            for log in logs:
                if log.status == CheckStatus.OK:
                    # 正常に収集されたログの処理
                    try:
                        with open(log.details['local_path'], 'r', encoding='utf-8') as f:
                            content = f.read().strip()
                            lines = content.split('\n')
                            line_count = len(lines)
                            
                            if line_count > 0:
                                summary_lines.extend([
                                    f"  Source: {log.details['source_path']}",
                                    f"  Status: Successfully collected",
                                    f"  Lines: {line_count}",
                                    f"  Content:",
                                    "    " + "\n    ".join(lines)  # 全行を出力
                                ])
                            else:
                                summary_lines.extend([
                                    f"  Source: {log.details['source_path']}",
                                    f"  Status: Successfully collected",
                                    f"  Lines: 0",
                                    f"  Content: (empty file)"
                                ])
                    except Exception as e:
                        summary_lines.extend([
                            f"  Source: {log.details['source_path']}",
                            f"  Status: File collected but failed to read",
                            f"  Error: {str(e)}"
                        ])
                else:
                    # 収集に失敗したログの処理
                    summary_lines.extend([
                        f"  Source: {log.details.get('source_path', 'Unknown')}",
                        f"  Status: {log.status.name}",
                        f"  Message: {log.details.get('message', 'No additional information')}"
                    ])
                summary_lines.append("")  # 各ログエントリの間に空行を追加
        
        summary_lines.append("-" * 80 + "\n")
        
        # サマリーを追記モードで保存
        with open(summary_path, 'a', encoding='utf-8') as f:
            f.write('\n'.join(summary_lines))

    def _write_check_summary(self, results: Dict[str, List[CheckResult]]) -> None:
        """監視結果のサマリーをcheck_summary.jsonに出力
        
        Args:
            results: カテゴリごとの監視結果
        """
        # check_summary.jsonの出力
        summary_path = os.path.join(
            self.config['storage']['output_folder'],
            'check_summary.json'
        )
        
        # 出力ディレクトリが存在しない場合は作成
        os.makedirs(os.path.dirname(summary_path), exist_ok=True)
        
        summary = {
            'timestamp': datetime.now().isoformat(),
            'results': {}
        }
        
        # カテゴリごとにサマリーを作成
        for category, data in results.items():
            if category in ['docker', 'ping', 'web_health']:
                summary['results'][category] = [
                    {
                        'name': result.name,
                        'status': result.status.name,
                        'timestamp': result.timestamp,
                        'details': result.details
                    }
                    for result in data
                ]
        
        # check_summary.jsonを保存
        with open(summary_path, 'w') as f:
            json.dump(summary, f, indent=2, ensure_ascii=False)

        # ログのサマリーを作成（log_summary.log）
        if 'logs' in results:
            self._update_log_summary(results['logs'])

    def _write_error_summary(self, results: Dict[str, List[CheckResult]]) -> None:
        """エラーのみの監視結果をerror_summary.jsonに出力

        Args:
            results: カテゴリごとの監視結果
        """
        # error_summary.jsonの出力
        error_summary_path = os.path.join(
            self.config['storage']['output_folder'],
            'error_summary.json'
        )

        # 出力ディレクトリが存在しない場合は作成
        os.makedirs(os.path.dirname(error_summary_path), exist_ok=True)

        error_summary = {
            'timestamp': datetime.now().isoformat(),
            'results': {}
        }

        # カテゴリごとにエラーのみを抽出
        for category, data in results.items():
            if category in ['docker', 'ping', 'web_health']:
                error_results = [
                    {
                        'name': result.name,
                        'status': result.status.name,
                        'timestamp': result.timestamp,
                        'details': result.details
                    }
                    for result in data
                    if result.status != CheckStatus.OK
                ]
                # エラーが存在する場合のみ追加
                if error_results:
                    error_summary['results'][category] = error_results

        # エラーが一つもなければファイルを作成しない
        if error_summary['results']:
            with open(error_summary_path, 'w') as f:
                json.dump(error_summary, f, indent=2, ensure_ascii=False)

    def validate_config(self) -> Optional[str]:
        """設定ファイルの検証
        
        必須項目の存在確認と、各設定の整合性チェックを行います。
        
        Returns:
            エラーメッセージ（エラーがある場合）またはNone（正常な場合）
        """
        try:
            # 必須セクションの確認
            required_sections = ['log_collection', 'ping_targets', 'docker_monitoring', 'storage']
            for section in required_sections:
                if section not in self.config:
                    return f"Missing required section: {section}"

            # ストレージ設定の検証
            if 'output_folder' not in self.config['storage']:
                return "Missing 'output_folder' in storage configuration"
            if not os.path.exists(self.config['storage']['output_folder']):
                return f"Storage directory does not exist: {self.config['storage']['output_folder']}"

            # ログ収集設定の検証
            for server in self.config['log_collection'].get('servers', []):
                missing_fields = []
                for field in ['name', 'host', 'log_paths']:
                    if field not in server:
                        missing_fields.append(field)
                if missing_fields:
                    return f"Missing required fields {missing_fields} in log_collection server configuration"
                if not isinstance(server['log_paths'], list):
                    return f"'log_paths' must be a list for server {server['name']}"

            # Ping設定の検証
            if not isinstance(self.config['ping_targets'], list):
                return "'ping_targets' must be a list"
            for target in self.config['ping_targets']:
                missing_fields = []
                for field in ['name', 'host']:
                    if field not in target:
                        missing_fields.append(field)
                if missing_fields:
                    return f"Missing required fields {missing_fields} in ping_targets configuration"

            # Dockerコンテナ監視設定の検証
            docker_config = self.config['docker_monitoring']
            if 'servers' not in docker_config:
                return "Missing 'servers' in docker_monitoring configuration"
            
            for server in docker_config['servers']:
                # サーバー設定の検証
                missing_fields = []
                for field in ['host', 'containers']:
                    if field not in server:
                        missing_fields.append(field)
                if missing_fields:
                    return f"Missing required fields {missing_fields} in docker_monitoring server configuration"
                
                # コンテナ設定の検証
                if not isinstance(server['containers'], list):
                    return f"'containers' must be a list for server {server['host']}"
                
                for container in server['containers']:
                    if 'name' not in container:
                        return f"Missing 'name' in container configuration for server {server['host']}"
                    
                    # Webコンテナの場合、health_check_urlが必要
                    if container.get('type') == 'web' and 'health_check_url' not in container:
                        return f"Missing 'health_check_url' for web container {container['name']}"

            # SSH設定の検証（デフォルト設定がある場合）
            if 'default_ssh' in self.config:
                ssh_config = self.config['default_ssh']
                missing_fields = []
                for field in ['username', 'key_path']:
                    if field not in ssh_config:
                        missing_fields.append(field)
                if missing_fields:
                    return f"Missing required fields {missing_fields} in default_ssh configuration"

            # Webヘルスチェック設定の検証（オプショナル）
            if 'web_health_checks' in self.config:
                if 'targets' not in self.config['web_health_checks']:
                    return "Missing 'targets' in web_health_checks configuration"
                
                for target in self.config['web_health_checks']['targets']:
                    missing_fields = []
                    for field in ['name', 'url']:
                        if field not in target:
                            missing_fields.append(field)
                    if missing_fields:
                        return f"Missing required fields {missing_fields} in web_health_checks target configuration"

            return None
        except Exception as e:
            return f"Configuration validation error: {str(e)}"

    def __enter__(self):
        """コンテキストマネージャー"""
        return self

    def __exit__(self, exc_type, exc_val, exc_tb):
        """クリーンアップ"""
        # 必要なったらリソースのクリーンアップを追加する
        pass
