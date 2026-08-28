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

Typical usage example:
    with MonitoringSystem("config.yaml") as monitor:
        monitor.run_all_checks()

メイン設定YAMLのルートに includes_dir（文字列）を書くと、
そのディレクトリ直下の YAML をマージして読み込みます。詳細は docs/configuration.md。
"""

from __future__ import annotations

import json
import logging
import os
import re
import shlex
import socket
import subprocess
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum, auto
from typing import Any, Callable, Dict, List, Optional, Sequence

import paramiko
import ping3
import requests
import urllib3

from exceptions import MonitoringError, RetryableError
from config_loader import (
    CONFIG_INCLUDES_DIR_KEY,
    load_merged_yaml_config,
)

urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)

__version__ = "1.2.0"

# 後方互換のための再エクスポート
__all__ = [
    "MonitoringError",
    "RetryableError",
    "CheckStatus",
    "CheckResult",
    "MonitoringSystem",
    "load_merged_yaml_config",
    "CONFIG_INCLUDES_DIR_KEY",
    "CHECK_CATEGORIES",
    "__version__",
]

# CLI / --only で指定できる監視カテゴリ
CHECK_CATEGORIES = ("logs", "ping", "ports", "disk", "docker", "web_health")


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
    details: Dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> Dict[str, Any]:
        """JSON 出力用の辞書に変換する。"""
        return {
            "name": self.name,
            "status": self.status.name,
            "timestamp": self.timestamp,
            "details": self.details,
        }


def is_monitoring_failure(category: str, result: CheckResult) -> bool:
    """終了コード判定用。ERROR は常に失敗。

    ログの NOT_FOUND はローテーション直後などであり得るため失敗にしない。
    それ以外のカテゴリの NOT_FOUND（コンテナ消失など）は失敗とみなす。
    """
    if result.status == CheckStatus.ERROR:
        return True
    if result.status == CheckStatus.NOT_FOUND and category != "logs":
        return True
    return False


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
    DEFAULT_PING_TIMEOUT = 5
    DEFAULT_SSH_TIMEOUT = 15
    DEFAULT_LOG_SUMMARY_MAX_LINES = 80
    DEFAULT_RETAIN_DAYS = 14
    DEFAULT_DASHBOARD_REFRESH = 30
    DEFAULT_FAIL_COUNT = 2
    DEFAULT_REMIND_SECONDS = 3600

    def __init__(self, config_path: str):
        """初期化

        Args:
            config_path: YAML形式の設定ファイルパス

        Raises:
            MonitoringError: 設定ファイルの読み込みに失敗した場合
        """
        try:
            self.config = load_merged_yaml_config(config_path)
        except MonitoringError:
            raise
        except Exception as e:
            raise MonitoringError(f"設定ファイルの読み込みに失敗しました: {e}") from e

        self._setup_logging()
        self._initialize_parameters()
        self._validate_and_create_directories()

    def _initialize_parameters(self):
        """パラメータの初期化（settings セクションがあれば上書きする）"""
        settings = self.config.get("settings") or {}
        if not isinstance(settings, dict):
            settings = {}
        self.retry_count = int(settings.get("retry_count", self.DEFAULT_RETRY_COUNT))
        self.retry_delay = float(settings.get("retry_delay", self.DEFAULT_RETRY_DELAY))
        self.timeout = float(settings.get("timeout", self.DEFAULT_TIMEOUT))
        self.max_workers = int(settings.get("max_workers", self.DEFAULT_MAX_WORKERS))
        self.ping_timeout = float(settings.get("ping_timeout", self.DEFAULT_PING_TIMEOUT))
        self.ssh_timeout = float(settings.get("ssh_timeout", self.DEFAULT_SSH_TIMEOUT))
        self.log_summary_max_lines = int(
            settings.get("log_summary_max_lines", self.DEFAULT_LOG_SUMMARY_MAX_LINES)
        )
        self.retain_days = int(settings.get("retain_days", self.DEFAULT_RETAIN_DAYS))
        self.dashboard_refresh_seconds = int(
            settings.get("dashboard_refresh_seconds", self.DEFAULT_DASHBOARD_REFRESH)
        )
        self.log_file = settings.get("log_file")
        if self.log_file:
            self._attach_file_logger(self._expand_path(str(self.log_file)))

    def _validate_and_create_directories(self):
        """出力ディレクトリの検証と作成"""
        if "storage" not in self.config:
            raise MonitoringError(
                "設定に 'storage' がありません。"
                "config.d/00-storage.yaml 等で storage.output_folder を定義するか、"
                "メインYAMLに storage セクションを書いてください。"
            )
        storage = self.config["storage"]
        if not isinstance(storage, dict) or "output_folder" not in storage:
            raise MonitoringError(
                "設定 storage.output_folder がありません。"
                "保存先ディレクトリのパスを指定してください。"
            )
        output_dir = self._expand_path(storage["output_folder"])
        # 以降の保存処理で相対パスに依存しないよう絶対パスへ正規化する
        self.config["storage"]["output_folder"] = output_dir
        try:
            os.makedirs(output_dir, exist_ok=True)
            os.makedirs(os.path.join(output_dir, "logs"), exist_ok=True)
        except OSError as e:
            raise MonitoringError(
                f"出力ディレクトリの作成に失敗しました: {output_dir}: {e}"
            ) from e

    def _setup_logging(self):
        """ロギングの設定"""
        logging.basicConfig(
            level=logging.INFO,
            format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
        )
        self.logger = logging.getLogger("BeaconBase")

    def _attach_file_logger(self, log_path: str) -> None:
        """同一ファイルへの FileHandler が無ければ追加する（定期実行で重複しない）。"""
        from logging.handlers import RotatingFileHandler

        os.makedirs(os.path.dirname(log_path) or ".", exist_ok=True)
        abs_path = os.path.abspath(log_path)
        for handler in self.logger.handlers:
            if isinstance(handler, RotatingFileHandler) and getattr(
                handler, "baseFilename", ""
            ) == abs_path:
                return
        handler = RotatingFileHandler(
            abs_path, maxBytes=2_000_000, backupCount=5, encoding="utf-8"
        )
        handler.setFormatter(
            logging.Formatter("%(asctime)s - %(name)s - %(levelname)s - %(message)s")
        )
        self.logger.addHandler(handler)

    @staticmethod
    def _expand_path(path: Optional[str]) -> str:
        """~ と環境変数を展開し、絶対パスにする。"""
        if not path:
            return ""
        expanded = os.path.expandvars(os.path.expanduser(str(path)))
        return os.path.abspath(expanded)

    @staticmethod
    def _now_iso() -> str:
        return datetime.now().isoformat()

    @staticmethod
    def _write_json(path: str, data: Any) -> None:
        """UTF-8 / 日本語そのまま / インデント付きで JSON を書く。"""
        os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
        with open(path, "w", encoding="utf-8") as f:
            json.dump(data, f, indent=2, ensure_ascii=False)

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
                    f"リトライ {attempt + 1}/{self.retry_count} 失敗: {e}"
                )
                if attempt < self.retry_count - 1:
                    time.sleep(self.retry_delay)
                continue
        raise last_error

    def enabled_categories(self) -> List[str]:
        """設定上、実行対象になるカテゴリを返す（空セクションは除く）。"""
        enabled = []
        if self._log_servers():
            enabled.append("logs")
        if self._ping_targets():
            enabled.append("ping")
        if self._port_targets():
            enabled.append("ports")
        if self._disk_servers():
            enabled.append("disk")
        if self._docker_servers():
            enabled.append("docker")
        if self._web_targets():
            enabled.append("web_health")
        return enabled

    def _log_servers(self) -> List[Dict[str, Any]]:
        section = self.config.get("log_collection") or {}
        servers = section.get("servers") if isinstance(section, dict) else None
        return list(servers) if isinstance(servers, list) else []

    def _ping_targets(self) -> List[Dict[str, Any]]:
        targets = self.config.get("ping_targets")
        return list(targets) if isinstance(targets, list) else []

    def _docker_servers(self) -> List[Dict[str, Any]]:
        section = self.config.get("docker_monitoring") or {}
        servers = section.get("servers") if isinstance(section, dict) else None
        return list(servers) if isinstance(servers, list) else []

    def _web_targets(self) -> List[Dict[str, Any]]:
        section = self.config.get("web_health_checks") or {}
        targets = section.get("targets") if isinstance(section, dict) else None
        return list(targets) if isinstance(targets, list) else []

    def _port_targets(self) -> List[Dict[str, Any]]:
        section = self.config.get("port_checks") or {}
        targets = section.get("targets") if isinstance(section, dict) else None
        if isinstance(section, list):
            return list(section)
        return list(targets) if isinstance(targets, list) else []

    def _disk_servers(self) -> List[Dict[str, Any]]:
        section = self.config.get("disk_checks") or {}
        servers = section.get("servers") if isinstance(section, dict) else None
        return list(servers) if isinstance(servers, list) else []

    @staticmethod
    def _with_group(details: Dict[str, Any], target: Dict[str, Any]) -> Dict[str, Any]:
        group = target.get("group")
        if group:
            details["group"] = str(group)
        return details

    def run_all_checks(
        self, categories: Optional[Sequence[str]] = None
    ) -> Dict[str, List[CheckResult]]:
        """監視チェックを並列実行する。

        Args:
            categories: 実行するカテゴリ。None なら設定のあるカテゴリすべて。

        Returns:
            カテゴリごとのチェック結果

        Raises:
            MonitoringError: 監視チェックの実行に失敗した場合
        """
        check_functions: Dict[str, Callable[[], List[CheckResult]]] = {
            "logs": self.collect_logs,
            "ping": self.check_ping,
            "ports": self.check_ports,
            "disk": self.check_disks,
            "docker": self.check_docker_containers,
            "web_health": self.check_web_health,
        }

        if categories is None:
            selected = [c for c in CHECK_CATEGORIES if c in set(self.enabled_categories())]
        else:
            unknown = [c for c in categories if c not in check_functions]
            if unknown:
                raise MonitoringError(
                    f"不明な監視カテゴリです: {', '.join(unknown)}。"
                    f"指定できる値: {', '.join(CHECK_CATEGORIES)}"
                )
            selected = list(categories)

        if not selected:
            self.logger.warning(
                "実行する監視がありません。"
                "設定に監視対象を書くか、--only の指定を確認してください。"
            )
            results: Dict[str, List[CheckResult]] = {}
            self._write_check_summary(results)
            self._write_error_summary(results)
            self._finalize_run(results)
            return results

        try:
            # カテゴリ全体に短い timeout を掛けない。
            # SSH ログ収集などは個別タイムアウトとリトライで制御する。
            workers = min(self.max_workers, len(selected)) or 1
            with ThreadPoolExecutor(max_workers=workers) as executor:
                future_to_check = {
                    executor.submit(check_functions[check_type]): check_type
                    for check_type in selected
                }

                results = {}
                for future in as_completed(future_to_check):
                    check_type = future_to_check[future]
                    try:
                        data = future.result()
                        self._save_results(data, check_type)
                        results[check_type] = data
                    except Exception as e:
                        error_msg = f"{check_type} の監視中にエラー: {e}"
                        self.logger.error(error_msg)
                        results[check_type] = [
                            CheckResult(
                                name=check_type,
                                status=CheckStatus.ERROR,
                                timestamp=self._now_iso(),
                                details={"error": str(e)},
                            )
                        ]

            self._write_check_summary(results)
            self._write_error_summary(results)
            self._finalize_run(results)
            return results

        except Exception as e:
            raise MonitoringError(f"監視の実行に失敗しました: {e}") from e

    def collect_logs(self) -> List[CheckResult]:
        """すべての対象サーバーからログを収集する。"""
        results: List[CheckResult] = []
        for server in self._log_servers():
            name = server.get("name", server.get("host", "unknown"))
            try:
                logs = self.retry_operation(self._collect_server_logs, server)
                results.extend(logs)
            except Exception as e:
                self.logger.error(f"{name} からのログ収集に失敗しました: {e}")
                results.append(
                    CheckResult(
                        name=name,
                        status=CheckStatus.ERROR,
                        timestamp=self._now_iso(),
                        details={"error": str(e), "server": name},
                    )
                )
        return results

    def _get_ssh_config(self, server: Dict[str, Any]) -> Dict[str, Any]:
        """サーバーのSSH設定を取得する。

        サーバー固有のSSH設定がない場合は、デフォルト設定を使用する。
        鍵パスの ~ は展開する。
        """
        default_ssh = self.config.get("default_ssh") or {}
        username = server.get("ssh_username", default_ssh.get("username"))
        key_path = server.get("ssh_key_path", default_ssh.get("key_path"))
        port = server.get("ssh_port", default_ssh.get("port", 22))
        try:
            port = int(port)
        except (TypeError, ValueError):
            port = 22
        expanded_key = self._expand_path(key_path) if key_path else ""
        return {
            "username": username,
            "key_path": expanded_key,
            "port": port,
        }

    def _connect_ssh(self, server: Dict[str, Any]) -> paramiko.SSHClient:
        """SSH 接続を開き、接続済みクライアントを返す。

        Raises:
            MonitoringError: 設定不足（リトライしても直らない）
            RetryableError: 接続失敗（リトライ対象）
        """
        label = server.get("name") or server.get("host") or "unknown"
        ssh_config = self._get_ssh_config(server)
        if not ssh_config["username"] or not ssh_config["key_path"]:
            raise MonitoringError(
                f"サーバー {label} の SSH 設定が不足しています"
                "（username / key_path）。default_ssh か個別設定を書いてください。"
            )

        ssh = paramiko.SSHClient()
        ssh.set_missing_host_key_policy(paramiko.AutoAddPolicy())
        try:
            ssh.connect(
                server["host"],
                username=ssh_config["username"],
                key_filename=ssh_config["key_path"],
                port=ssh_config["port"],
                timeout=self.ssh_timeout,
                allow_agent=True,
                look_for_keys=False,
            )
            return ssh
        except MonitoringError:
            ssh.close()
            raise
        except Exception as e:
            ssh.close()
            raise RetryableError(
                f"サーバー {label} ({server.get('host')}) への SSH 接続に失敗しました: {e}"
            ) from e

    def _collect_server_logs(self, server: Dict[str, Any]) -> List[CheckResult]:
        """個別サーバーからのログ収集"""
        collected_logs: List[CheckResult] = []
        missing_logs: List[CheckResult] = []
        ssh = self._connect_ssh(server)
        try:
            timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
            sftp = ssh.open_sftp()
            try:
                for log_path in server.get("log_paths") or []:
                    local_filename = f"{timestamp}_{os.path.basename(log_path)}"
                    local_path = os.path.join(
                        self.config["storage"]["output_folder"],
                        "logs",
                        server["name"],
                        local_filename,
                    )
                    os.makedirs(os.path.dirname(local_path), exist_ok=True)
                    try:
                        sftp.stat(log_path)
                        sftp.get(log_path, local_path)
                        delete_after = (self.config.get("log_collection") or {}).get(
                            "delete_after_collection", False
                        )
                        if delete_after:
                            sftp.remove(log_path)
                            delete_status = "deleted"
                        else:
                            delete_status = "preserved"
                        collected_logs.append(
                            CheckResult(
                                name=f"{server['name']}_{os.path.basename(log_path)}",
                                status=CheckStatus.OK,
                                timestamp=self._now_iso(),
                                details={
                                    "server": server["name"],
                                    "source_path": log_path,
                                    "local_path": local_path,
                                    "status": "collected",
                                    "server_file_status": delete_status,
                                },
                            )
                        )
                    except FileNotFoundError:
                        missing_logs.append(
                            CheckResult(
                                name=f"{server['name']}_{os.path.basename(log_path)}",
                                status=CheckStatus.NOT_FOUND,
                                timestamp=self._now_iso(),
                                details={
                                    "server": server["name"],
                                    "source_path": log_path,
                                    "status": "not_found",
                                    "message": "サーバー上にファイルがありません",
                                },
                            )
                        )
                        self.logger.warning(
                            f"ログファイルが見つかりません: {server['name']}:{log_path}"
                        )
                return collected_logs + missing_logs
            finally:
                sftp.close()
        finally:
            ssh.close()

    def check_ping(self) -> List[CheckResult]:
        """Ping 疎通確認を実行する（対象は並列）。"""
        targets = self._ping_targets()
        if not targets:
            return []

        results_by_index: Dict[int, CheckResult] = {}
        workers = min(self.max_workers, len(targets)) or 1
        with ThreadPoolExecutor(max_workers=workers) as executor:
            future_map = {
                executor.submit(self._ping_target, target): idx
                for idx, target in enumerate(targets)
            }
            for future in as_completed(future_map):
                results_by_index[future_map[future]] = future.result()
        return [results_by_index[i] for i in range(len(targets))]

    def _ping_target(self, target: Dict[str, Any]) -> CheckResult:
        host = target.get("host", "")
        name = target.get("name", host or "unknown")
        response_time = self._ping_host(host)
        if response_time is not None:
            return CheckResult(
                name=name,
                status=CheckStatus.OK,
                timestamp=self._now_iso(),
                details=self._with_group(
                    {"host": host, "response_time": response_time}, target
                ),
            )
        return CheckResult(
            name=name,
            status=CheckStatus.ERROR,
            timestamp=self._now_iso(),
            details=self._with_group({"host": host, "error": "Host unreachable"}, target),
        )

    def _ping_host(self, host: str) -> Optional[float]:
        """個別ホストへ Ping する。

        ping3（ICMP）を試し、権限不足などで失敗したら OS の ping コマンドにフォールバックする。
        """
        if not host:
            return None
        try:
            result = ping3.ping(host, timeout=self.ping_timeout)
            self.logger.debug(f"ping3 result for {host}: {result}")
            if result is not None and result is not False:
                return float(result)
            # False はタイムアウト（到達不能）。None は権限不足などのエラーなので OS ping へ。
            if result is False:
                return None
        except Exception as e:
            self.logger.debug(f"ping3 失敗 ({host}): {e}")

        return self._ping_via_system(host)

    def _ping_via_system(self, host: str) -> Optional[float]:
        """OS 標準の ping コマンドで疎通確認する。"""
        timeout = max(1, int(self.ping_timeout))
        if os.name == "nt":
            cmd = ["ping", "-n", "1", "-w", str(timeout * 1000), host]
        else:
            cmd = ["ping", "-c", "1", "-W", str(timeout), host]
        try:
            proc = subprocess.run(
                cmd,
                capture_output=True,
                text=True,
                timeout=timeout + 3,
            )
            if proc.returncode != 0:
                return None
            combined = (proc.stdout or "") + (proc.stderr or "")
            match = re.search(
                r"time[=<]\s*([\d.]+)\s*(ms|s)?", combined, re.IGNORECASE
            )
            if not match:
                return 0.0
            value = float(match.group(1))
            unit = (match.group(2) or "ms").lower()
            return value if unit == "s" else value / 1000.0
        except Exception as e:
            self.logger.error(f"{host} への ping に失敗しました: {e}")
            return None

    def check_ports(self) -> List[CheckResult]:
        """TCP ポートの待ち受け確認（LAN のサービス死活）。"""
        targets = self._port_targets()
        if not targets:
            return []
        results_by_index: Dict[int, CheckResult] = {}
        workers = min(self.max_workers, len(targets)) or 1
        with ThreadPoolExecutor(max_workers=workers) as executor:
            future_map = {
                executor.submit(self._check_port_target, target): idx
                for idx, target in enumerate(targets)
            }
            for future in as_completed(future_map):
                results_by_index[future_map[future]] = future.result()
        return [results_by_index[i] for i in range(len(targets))]

    def _check_port_target(self, target: Dict[str, Any]) -> CheckResult:
        host = target.get("host", "")
        name = target.get("name", host or "unknown")
        try:
            port = int(target.get("port"))
        except (TypeError, ValueError):
            return CheckResult(
                name=name,
                status=CheckStatus.ERROR,
                timestamp=self._now_iso(),
                details=self._with_group(
                    {"host": host, "error": "port が整数ではありません"}, target
                ),
            )
        timeout = float(target.get("timeout", 3))
        ok, elapsed, error = self._tcp_connect(host, port, timeout)
        details = {"host": host, "port": port, "response_time": elapsed}
        if ok:
            return CheckResult(
                name=name,
                status=CheckStatus.OK,
                timestamp=self._now_iso(),
                details=self._with_group(details, target),
            )
        details["error"] = error or f"{host}:{port} に接続できません"
        return CheckResult(
            name=name,
            status=CheckStatus.ERROR,
            timestamp=self._now_iso(),
            details=self._with_group(details, target),
        )

    @staticmethod
    def _tcp_connect(host: str, port: int, timeout: float) -> tuple:
        """TCP 接続を試し、(成功, 秒, エラーメッセージ) を返す。"""
        start = time.time()
        try:
            with socket.create_connection((host, port), timeout=timeout):
                elapsed = time.time() - start
                return True, elapsed, None
        except Exception as e:
            return False, time.time() - start, str(e)

    def check_disks(self) -> List[CheckResult]:
        """SSH 経由で df -P を取り、ディスク使用率を判定する。"""
        results: List[CheckResult] = []
        for server in self._disk_servers():
            name = server.get("name") or server.get("host") or "unknown"
            try:
                results.append(
                    self.retry_operation(self._check_disk_server, server)
                )
            except Exception as e:
                self.logger.error(f"{name} のディスク監視に失敗しました: {e}")
                results.append(
                    CheckResult(
                        name=name,
                        status=CheckStatus.ERROR,
                        timestamp=self._now_iso(),
                        details=self._with_group(
                            {
                                "host": server.get("host"),
                                "error": str(e),
                            },
                            server,
                        ),
                    )
                )
        return results

    def _check_disk_server(self, server: Dict[str, Any]) -> CheckResult:
        name = server.get("name") or server.get("host") or "unknown"
        host = server.get("host", "")
        warn_percent = float(server.get("warn_percent", 85))
        error_percent = float(server.get("error_percent", 95))
        ssh = self._connect_ssh(server)
        try:
            _stdin, stdout, stderr = ssh.exec_command("df -P", timeout=self.timeout)
            out = stdout.read().decode("utf-8", errors="replace")
            err = stderr.read().decode("utf-8", errors="replace")
            filesystems = self.parse_df_p(out)
            if not filesystems:
                raise MonitoringError(
                    f"df -P の結果を解析できませんでした: {err or out[:200]}"
                )
            worst = max(filesystems, key=lambda row: row["use_percent"])
            use_percent = worst["use_percent"]
            if use_percent >= error_percent:
                status = CheckStatus.ERROR
            elif use_percent >= warn_percent:
                status = CheckStatus.WARNING
            else:
                status = CheckStatus.OK
            details = self._with_group(
                {
                    "host": host,
                    "use_percent": use_percent,
                    "worst_mount": worst["mount"],
                    "filesystems": filesystems,
                    "warn_percent": warn_percent,
                    "error_percent": error_percent,
                },
                server,
            )
            if status != CheckStatus.OK:
                details["error"] = (
                    f"{worst['mount']} の使用率 {use_percent:.0f}%"
                    f"（警告 {warn_percent:.0f}% / 異常 {error_percent:.0f}%）"
                )
            return CheckResult(
                name=name,
                status=status,
                timestamp=self._now_iso(),
                details=details,
            )
        finally:
            ssh.close()

    _SKIP_FS_TYPES = {
        "tmpfs",
        "devtmpfs",
        "overlay",
        "squashfs",
        "proc",
        "sysfs",
        "cgroup",
        "cgroup2",
        "devpts",
        "efivarfs",
    }

    @classmethod
    def parse_df_p(cls, text: str) -> List[Dict[str, Any]]:
        """POSIX df -P の出力から実ファイルシステムを抜き出す。"""
        rows: List[Dict[str, Any]] = []
        for line in (text or "").splitlines()[1:]:
            parts = line.split()
            if len(parts) < 6:
                continue
            filesystem, _blocks, used, available, capacity, mount = (
                parts[0],
                parts[1],
                parts[2],
                parts[3],
                parts[4],
                parts[5],
            )
            fs_l = filesystem.lower()
            if filesystem in cls._SKIP_FS_TYPES or fs_l in cls._SKIP_FS_TYPES:
                continue
            if mount.startswith(("/dev", "/run", "/sys", "/proc")):
                continue
            match = re.search(r"(\d+)", capacity)
            if not match:
                continue
            try:
                used_i = int(used)
                available_i = int(available)
            except ValueError:
                used_i = 0
                available_i = 0
            rows.append(
                {
                    "filesystem": filesystem,
                    "mount": mount,
                    "use_percent": int(match.group(1)),
                    "used": used_i,
                    "available": available_i,
                }
            )
        return rows

    @staticmethod
    def _status_from_container_status(container_status: Dict[str, Any]) -> CheckStatus:
        """docker ps / inspect の結果から CheckStatus を判定する。

        Health 情報があればそれを優先し、なければ Status 文字列から判定する。
        HTTP ヘルスチェックが FAIL の場合は ERROR にする。
        """
        if container_status.get("status") == "NOT_FOUND":
            return CheckStatus.NOT_FOUND

        health_check = container_status.get("health_check") or {}
        if health_check.get("status") == "FAIL":
            return CheckStatus.ERROR

        state = container_status.get("state", {}) or {}
        health = state.get("Health", {}) or {}
        health_status = health.get("Status", "")

        if health_status:
            if health_status == "unhealthy":
                return CheckStatus.ERROR
            if health_status == "starting":
                return CheckStatus.WARNING
            if health_status == "healthy":
                return CheckStatus.OK
            return CheckStatus.ERROR

        status_str = container_status.get("status", "")
        if "(unhealthy)" in status_str:
            return CheckStatus.ERROR
        if "(health: starting)" in status_str or "(starting)" in status_str:
            return CheckStatus.WARNING
        if status_str.startswith("Up"):
            return CheckStatus.OK
        return CheckStatus.ERROR

    def check_docker_containers(self) -> List[CheckResult]:
        """Dockerコンテナの状態を確認する。"""
        results: List[CheckResult] = []
        for server in self._docker_servers():
            host = server.get("host", "unknown")
            try:
                server_results = self.retry_operation(self._check_docker_server, server)
                results.extend(server_results)
            except Exception as e:
                self.logger.error(f"Docker ホスト {host} の監視に失敗しました: {e}")
                results.append(
                    CheckResult(
                        name=f"server_{host}",
                        status=CheckStatus.ERROR,
                        timestamp=self._now_iso(),
                        details={"error": str(e), "host": host},
                    )
                )
        return results

    def _check_docker_server(self, server: Dict[str, Any]) -> List[CheckResult]:
        """1台の Docker ホスト上のコンテナを確認する。"""
        results: List[CheckResult] = []
        ssh = self._connect_ssh(server)
        try:
            for container in server.get("containers") or []:
                container_status = self._check_container_via_ssh(ssh, container)
                container_status["host"] = server.get("host")
                self._with_group(container_status, server)
                status = self._status_from_container_status(container_status)
                name = container.get("name", "unknown")
                host = server.get("host", "")
                results.append(
                    CheckResult(
                        name=f"{name}@{host}" if host else name,
                        status=status,
                        timestamp=self._now_iso(),
                        details=container_status,
                    )
                )
            return results
        finally:
            ssh.close()

    def _check_container_via_ssh(self, ssh, container: Dict[str, Any]) -> Dict[str, Any]:
        """SSH 経由で個別コンテナの状態を確認する。"""
        name = container.get("name", "")
        try:
            filter_pattern = f"^/{name}$"
            cmd = (
                f"docker ps --filter name={shlex.quote(filter_pattern)} "
                f"--format '{{{{.Status}}}}'"
            )
            _stdin, stdout, _stderr = ssh.exec_command(cmd, timeout=self.timeout)
            status = stdout.read().decode().strip()

            result: Dict[str, Any] = {
                "name": name,
                "status": status if status else "NOT_FOUND",
            }

            if status:
                inspect_cmd = f"docker inspect {shlex.quote(name)}"
                _stdin, stdout, _stderr = ssh.exec_command(
                    inspect_cmd, timeout=self.timeout
                )
                raw = stdout.read().decode()
                inspect_data = json.loads(raw)[0]
                result.update(
                    {
                        "created": inspect_data.get("Created"),
                        "state": inspect_data.get("State"),
                    }
                )

            health_url = container.get("health_check_url")
            if health_url:
                # 監視ホストではなく Docker ホスト上から到達確認する
                timeout = int(container.get("health_check_timeout", 5))
                result["health_check"] = self._http_health_via_ssh(
                    ssh, health_url, timeout=timeout
                )

            return result
        except Exception as e:
            return {
                "name": name,
                "status": "ERROR",
                "error": str(e),
            }

    def _http_health_via_ssh(
        self, ssh, url: str, timeout: int = 5
    ) -> Dict[str, Any]:
        """Docker ホスト上で curl による HTTP ヘルスチェックを行う。"""
        quoted = shlex.quote(url)
        # http_code と time_total を空白区切りで取得する
        cmd = (
            f"curl -sS -o /dev/null -w '%{{http_code}} %{{time_total}}' "
            f"--max-time {int(timeout)} {quoted}"
        )
        try:
            _stdin, stdout, stderr = ssh.exec_command(
                cmd, timeout=timeout + 5
            )
            out = stdout.read().decode().strip()
            err = stderr.read().decode().strip()
            parts = out.split()
            if len(parts) >= 1 and parts[0].isdigit():
                code = int(parts[0])
                elapsed = float(parts[1]) if len(parts) > 1 else None
                return {
                    "status": "OK" if code == 200 else "FAIL",
                    "response_code": code,
                    "response_time": elapsed,
                    "url": url,
                }
            return {
                "status": "FAIL",
                "error": err or out or "curl の結果を解析できませんでした",
                "url": url,
            }
        except Exception as e:
            return {"status": "FAIL", "error": str(e), "url": url}

    def _check_web_health(self, url: str) -> Dict[str, Any]:
        """監視ホストからの Web 健全性確認（Docker 以外・後方互換）。"""
        try:
            start_time = time.time()
            response = requests.get(url, timeout=5, verify=False)
            response_time = time.time() - start_time
            return {
                "status": "OK" if response.status_code == 200 else "FAIL",
                "response_code": response.status_code,
                "response_time": round(response_time, 3),
            }
        except requests.RequestException as e:
            return {"status": "FAIL", "error": str(e)}

    @staticmethod
    def _parse_expected_status(raw: Any) -> List[int]:
        """expected_status を整数リストにする。未指定なら [200]。"""
        if raw is None:
            return [200]
        if isinstance(raw, int):
            return [raw]
        if isinstance(raw, str) and raw.strip().isdigit():
            return [int(raw.strip())]
        if isinstance(raw, list):
            codes = []
            for item in raw:
                try:
                    codes.append(int(item))
                except (TypeError, ValueError):
                    continue
            return codes or [200]
        return [200]

    def check_web_health(self) -> List[CheckResult]:
        """Web ページのヘルスチェックを実行する（対象は並列）。"""
        targets = self._web_targets()
        if not targets:
            return []

        results_by_index: Dict[int, CheckResult] = {}
        workers = min(self.max_workers, len(targets)) or 1
        with ThreadPoolExecutor(max_workers=workers) as executor:
            future_map = {
                executor.submit(self._check_web_target, target): idx
                for idx, target in enumerate(targets)
            }
            for future in as_completed(future_map):
                results_by_index[future_map[future]] = future.result()
        return [results_by_index[i] for i in range(len(targets))]

    def _check_web_target(self, target: Dict[str, Any]) -> CheckResult:
        name = target.get("name", target.get("url", "unknown"))
        url = target.get("url", "")
        timeout = float(target.get("timeout", 30))
        verify_ssl = bool(target.get("verify_ssl", True))
        expected = self._parse_expected_status(target.get("expected_status"))
        try:
            response = requests.get(url, timeout=timeout, verify=verify_ssl)
            response_time = response.elapsed.total_seconds()
            ok = response.status_code in expected
            details: Dict[str, Any] = {
                "url": url,
                "response_code": response.status_code,
                "response_time": response_time,
                "expected_status": expected,
            }
            self._with_group(details, target)
            if not ok:
                details["error"] = (
                    f"HTTP {response.status_code} "
                    f"（期待値: {', '.join(str(c) for c in expected)}）"
                )
            return CheckResult(
                name=name,
                status=CheckStatus.OK if ok else CheckStatus.ERROR,
                timestamp=self._now_iso(),
                details=details,
            )
        except requests.RequestException as e:
            self.logger.error(f"{name} のヘルスチェックに失敗しました: {e}")
            return CheckResult(
                name=name,
                status=CheckStatus.ERROR,
                timestamp=self._now_iso(),
                details=self._with_group(
                    {"url": url, "error": str(e), "expected_status": expected}, target
                ),
            )

    def _save_results(self, data: List[CheckResult], category: str) -> None:
        """監視結果を指定カテゴリの JSON ファイルに保存する。"""
        timestamp = datetime.now().strftime("%Y%m%d")
        category_dir = os.path.join(
            self.config["storage"]["output_folder"], category
        )
        filename = f"{category}_{timestamp}.json"
        path = os.path.join(category_dir, filename)

        os.makedirs(category_dir, exist_ok=True)

        existing_data: List[Any] = []
        if os.path.exists(path):
            try:
                with open(path, "r", encoding="utf-8") as f:
                    existing_data = json.load(f)
            except json.JSONDecodeError:
                self.logger.warning(f"既存データを読めませんでした: {path}")

        existing_data.extend([result.to_dict() for result in data])
        self._write_json(path, existing_data)

        if category in ["ping", "docker", "web_health", "ports", "disk"]:
            self._update_summary(category, data)
        elif category == "logs":
            self._update_log_summary(data)

    def _update_summary(self, category: str, data: List[CheckResult]) -> None:
        """監視結果のサマリー（monitoring_summary.json）を更新する。"""
        summary_path = os.path.join(
            self.config["storage"]["output_folder"],
            "monitoring_summary.json",
        )

        summary: Dict[str, Any] = {}
        if os.path.exists(summary_path):
            try:
                with open(summary_path, "r", encoding="utf-8") as f:
                    summary = json.load(f)
            except (json.JSONDecodeError, FileNotFoundError):
                self.logger.warning("既存の monitoring_summary.json を読めませんでした")

        if category == "ping":
            ping_targets = {
                target.get("name"): target.get("host")
                for target in self._ping_targets()
            }
            summary["ping"] = [
                {
                    "name": result.name,
                    "ip": ping_targets.get(result.name, result.details.get("host", "unknown")),
                    "status": result.status.name,
                    "details": result.details,
                }
                for result in data
            ]
        elif category == "docker":
            summary["docker"] = [
                {
                    "name": result.name,
                    "status": result.status.name,
                    "details": result.details,
                }
                for result in data
            ]
        elif category in ("web_health", "ports", "disk"):
            summary[category] = [
                {
                    "name": result.name,
                    "status": result.status.name,
                    "details": result.details,
                }
                for result in data
            ]

        summary["last_updated"] = self._now_iso()
        self._write_json(summary_path, summary)

    def _update_log_summary(self, data: List[CheckResult]) -> None:
        """ログ収集結果のサマリーを更新する。"""
        summary_path = os.path.join(
            self.config["storage"]["output_folder"],
            "log_summary.log",
        )
        timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        summary_lines = [f"=== Log Collection ({timestamp}) ===\n"]

        servers: Dict[str, List[CheckResult]] = {}
        for result in data:
            server_name = result.details.get("server") or result.name
            servers.setdefault(server_name, []).append(result)

        max_lines = self.log_summary_max_lines
        for server_name, logs in servers.items():
            summary_lines.append(f"\nServer: {server_name}")
            for log in logs:
                if log.status == CheckStatus.OK:
                    try:
                        with open(
                            log.details["local_path"], "r", encoding="utf-8", errors="replace"
                        ) as f:
                            content = f.read().strip()
                        lines = content.split("\n") if content else []
                        line_count = len(lines)
                        if line_count > 0:
                            truncated = False
                            shown = lines
                            if max_lines > 0 and line_count > max_lines:
                                shown = lines[-max_lines:]
                                truncated = True
                            summary_lines.extend(
                                [
                                    f"  Source: {log.details['source_path']}",
                                    f"  Status: Successfully collected",
                                    f"  Lines: {line_count}"
                                    + (
                                        f" (末尾 {len(shown)} 行のみ表示)"
                                        if truncated
                                        else ""
                                    ),
                                    "  Content:",
                                    "    " + "\n    ".join(shown),
                                ]
                            )
                        else:
                            summary_lines.extend(
                                [
                                    f"  Source: {log.details['source_path']}",
                                    f"  Status: Successfully collected",
                                    "  Lines: 0",
                                    "  Content: (empty file)",
                                ]
                            )
                    except Exception as e:
                        summary_lines.extend(
                            [
                                f"  Source: {log.details['source_path']}",
                                "  Status: File collected but failed to read",
                                f"  Error: {str(e)}",
                            ]
                        )
                else:
                    summary_lines.extend(
                        [
                            f"  Source: {log.details.get('source_path', 'Unknown')}",
                            f"  Status: {log.status.name}",
                            f"  Message: {log.details.get('message', log.details.get('error', 'No additional information'))}",
                        ]
                    )
                summary_lines.append("")
        summary_lines.append("-" * 80 + "\n")

        with open(summary_path, "a", encoding="utf-8") as f:
            f.write("\n".join(summary_lines))

    def count_statuses(
        self, results: Dict[str, List[CheckResult]]
    ) -> Dict[str, Dict[str, int]]:
        """カテゴリごとのステータス件数を集計する。"""
        counts: Dict[str, Dict[str, int]] = {}
        for category, data in results.items():
            tally = {status.name: 0 for status in CheckStatus}
            tally["total"] = len(data)
            for result in data:
                tally[result.status.name] = tally.get(result.status.name, 0) + 1
            counts[category] = tally
        return counts

    def format_results_summary(
        self, results: Dict[str, List[CheckResult]]
    ) -> str:
        """ターミナル表示用の監視結果サマリー文字列を作る。"""
        output_folder = self.config["storage"]["output_folder"]
        lines = ["=== BeaconBase 監視結果 ===", f"出力先: {output_folder}", ""]
        counts = self.count_statuses(results)
        if not results:
            lines.append("実行した監視はありません。")
            return "\n".join(lines)

        for category in CHECK_CATEGORIES:
            if category not in counts:
                continue
            tally = counts[category]
            parts = [f"  {category:<12} {tally['total']} 件"]
            for status in CheckStatus:
                n = tally.get(status.name, 0)
                if n:
                    parts.append(f"{status.name}:{n}")
            lines.append("  ".join(parts))

        failures = [
            (category, result)
            for category, data in results.items()
            for result in data
            if is_monitoring_failure(category, result) or result.status == CheckStatus.WARNING
        ]
        if failures:
            lines.append("")
            lines.append("問題のある項目:")
            for category, result in failures:
                detail = result.details.get("error") or result.details.get("message") or ""
                suffix = f" — {detail}" if detail else ""
                lines.append(f"  [{category}] {result.name}: {result.status.name}{suffix}")
        else:
            lines.append("")
            lines.append("問題は検出されませんでした。")
        return "\n".join(lines)

    def _write_check_summary(self, results: Dict[str, List[CheckResult]]) -> None:
        """監視結果のサマリーを check_summary.json に出力する。"""
        summary_path = os.path.join(
            self.config["storage"]["output_folder"],
            "check_summary.json",
        )
        summary = {
            "timestamp": self._now_iso(),
            "counts": self.count_statuses(results),
            "results": {},
        }
        for category, data in results.items():
            summary["results"][category] = [result.to_dict() for result in data]
        self._write_json(summary_path, summary)

    def _write_error_summary(self, results: Dict[str, List[CheckResult]]) -> None:
        """エラーのみの監視結果を error_summary.json に出力する。

        今回エラーが無ければ既存ファイルを削除する（前回の失敗が残らないようにする）。
        """
        error_summary_path = os.path.join(
            self.config["storage"]["output_folder"],
            "error_summary.json",
        )
        error_summary = {
            "timestamp": self._now_iso(),
            "results": {},
        }
        for category, data in results.items():
            error_results = [
                result.to_dict()
                for result in data
                if result.status != CheckStatus.OK
            ]
            if error_results:
                error_summary["results"][category] = error_results

        if error_summary["results"]:
            self._write_json(error_summary_path, error_summary)
        elif os.path.exists(error_summary_path):
            try:
                os.remove(error_summary_path)
            except OSError as e:
                self.logger.warning(
                    f"前回の error_summary.json を削除できませんでした: {e}"
                )

    def _finalize_run(self, results: Dict[str, List[CheckResult]]) -> None:
        """状態保存・通知・ダッシュボード・古い結果の掃除。"""
        from alerts import AlertDispatcher
        from dashboard import write_dashboard
        from status_store import StatusStore

        output_folder = self.config["storage"]["output_folder"]
        alerts_cfg = self.config.get("alerts") if isinstance(self.config.get("alerts"), dict) else {}
        fail_count = int(alerts_cfg.get("fail_count", self.DEFAULT_FAIL_COUNT))
        remind_seconds = float(alerts_cfg.get("remind_seconds", self.DEFAULT_REMIND_SECONDS))
        store = StatusStore(output_folder)
        events = store.update(
            results,
            fail_count=fail_count,
            remind_seconds=remind_seconds,
        )
        store.save()
        AlertDispatcher(self.config, output_folder).notify(events)
        write_dashboard(
            output_folder,
            results,
            store=store,
            refresh_seconds=self.dashboard_refresh_seconds,
        )
        self._retain_old_files()
        latest_path = os.path.join(output_folder, "latest.json")
        self._write_json(
            latest_path,
            {
                "timestamp": self._now_iso(),
                "counts": self.count_statuses(results),
                "results": {
                    category: [result.to_dict() for result in data]
                    for category, data in results.items()
                },
            },
        )
        if events:
            self.logger.info(f"通知 {len(events)} 件")

    def _retain_old_files(self) -> None:
        """日次 JSON を retain_days より古ければ削除する。0 以下は無制限。"""
        if self.retain_days <= 0:
            return
        cutoff = datetime.now().timestamp() - (self.retain_days * 86400)
        output_folder = self.config["storage"]["output_folder"]
        for category in CHECK_CATEGORIES:
            category_dir = os.path.join(output_folder, category)
            if not os.path.isdir(category_dir):
                continue
            for name in os.listdir(category_dir):
                match = re.match(
                    rf"{re.escape(category)}_(\d{{8}})\.json$", name
                )
                if not match:
                    continue
                try:
                    file_dt = datetime.strptime(match.group(1), "%Y%m%d")
                except ValueError:
                    continue
                path = os.path.join(category_dir, name)
                if file_dt.timestamp() < cutoff:
                    try:
                        os.remove(path)
                        self.logger.info(f"古い結果を削除しました: {path}")
                    except OSError as e:
                        self.logger.warning(f"削除できませんでした: {path}: {e}")

    def validate_config(self) -> Optional[str]:
        """設定ファイルの検証。

        storage 以外の監視セクションは省略可能。書かれている場合のみ中身を検証する。

        Returns:
            エラーメッセージ（エラーがある場合）または None（正常な場合）
        """
        try:
            if "storage" not in self.config:
                return "必須セクション 'storage' がありません"
            if "output_folder" not in self.config["storage"]:
                return "storage.output_folder がありません"
            output_folder = self.config["storage"]["output_folder"]
            if not os.path.isdir(output_folder):
                return f"出力ディレクトリが存在しません: {output_folder}"

            if "log_collection" in self.config:
                log_cfg = self.config["log_collection"]
                if not isinstance(log_cfg, dict):
                    return "log_collection はマッピングである必要があります"
                servers = log_cfg.get("servers", [])
                if servers is None:
                    servers = []
                if not isinstance(servers, list):
                    return "log_collection.servers はリストである必要があります"
                for server in servers:
                    missing = [f for f in ("name", "host", "log_paths") if f not in server]
                    if missing:
                        return (
                            f"log_collection のサーバー設定に必須項目がありません: {missing}"
                        )
                    if not isinstance(server["log_paths"], list):
                        return f"{server['name']} の log_paths はリストである必要があります"

            if "ping_targets" in self.config:
                if not isinstance(self.config["ping_targets"], list):
                    return "ping_targets はリストである必要があります"
                for target in self.config["ping_targets"]:
                    missing = [f for f in ("name", "host") if f not in target]
                    if missing:
                        return f"ping_targets の必須項目がありません: {missing}"

            if "docker_monitoring" in self.config:
                docker_config = self.config["docker_monitoring"]
                if not isinstance(docker_config, dict):
                    return "docker_monitoring はマッピングである必要があります"
                if "servers" not in docker_config:
                    return "docker_monitoring.servers がありません"
                if not isinstance(docker_config["servers"], list):
                    return "docker_monitoring.servers はリストである必要があります"
                for server in docker_config["servers"]:
                    missing = [f for f in ("host", "containers") if f not in server]
                    if missing:
                        return (
                            f"docker_monitoring のサーバー設定に必須項目がありません: {missing}"
                        )
                    if not isinstance(server["containers"], list):
                        return f"{server['host']} の containers はリストである必要があります"
                    for container in server["containers"]:
                        if "name" not in container:
                            return (
                                f"サーバー {server['host']} のコンテナに name がありません"
                            )

            if "default_ssh" in self.config:
                ssh_config = self.config["default_ssh"]
                if not isinstance(ssh_config, dict):
                    return "default_ssh はマッピングである必要があります"
                missing = [f for f in ("username", "key_path") if f not in ssh_config]
                if missing:
                    return f"default_ssh の必須項目がありません: {missing}"

            if "web_health_checks" in self.config:
                web_cfg = self.config["web_health_checks"]
                if not isinstance(web_cfg, dict):
                    return "web_health_checks はマッピングである必要があります"
                if "targets" not in web_cfg:
                    return "web_health_checks.targets がありません"
                if not isinstance(web_cfg["targets"], list):
                    return "web_health_checks.targets はリストである必要があります"
                for target in web_cfg["targets"]:
                    missing = [f for f in ("name", "url") if f not in target]
                    if missing:
                        return f"web_health_checks の必須項目がありません: {missing}"

            if "settings" in self.config and not isinstance(self.config["settings"], dict):
                return "settings はマッピングである必要があります"

            if "port_checks" in self.config:
                port_cfg = self.config["port_checks"]
                targets = None
                if isinstance(port_cfg, dict):
                    targets = port_cfg.get("targets")
                elif isinstance(port_cfg, list):
                    targets = port_cfg
                else:
                    return "port_checks はマッピングまたはリストである必要があります"
                if not isinstance(targets, list):
                    return "port_checks.targets はリストである必要があります"
                for target in targets:
                    missing = [f for f in ("name", "host", "port") if f not in target]
                    if missing:
                        return f"port_checks の必須項目がありません: {missing}"

            if "disk_checks" in self.config:
                disk_cfg = self.config["disk_checks"]
                if not isinstance(disk_cfg, dict):
                    return "disk_checks はマッピングである必要があります"
                servers = disk_cfg.get("servers", [])
                if not isinstance(servers, list):
                    return "disk_checks.servers はリストである必要があります"
                for server in servers:
                    missing = [f for f in ("name", "host") if f not in server]
                    if missing:
                        return f"disk_checks の必須項目がありません: {missing}"

            if "alerts" in self.config:
                alerts = self.config["alerts"]
                if not isinstance(alerts, dict):
                    return "alerts はマッピングである必要があります"
                webhook = alerts.get("webhook")
                if webhook is not None and not isinstance(webhook, dict):
                    return "alerts.webhook はマッピングである必要があります"
                email = alerts.get("email")
                if email is not None and not isinstance(email, dict):
                    return "alerts.email はマッピングである必要があります"

            return None
        except Exception as e:
            return f"設定の検証中にエラーが発生しました: {e}"

    def __enter__(self):
        """コンテキストマネージャー"""
        return self

    def __exit__(self, exc_type, exc_val, exc_tb):
        """クリーンアップ"""
        pass
