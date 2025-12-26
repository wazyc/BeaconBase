"""
BeaconBase テストモジュール

このモジュールでは、BeaconBaseの各機能をテストします。
モックを使用してネットワーク接続やファイルシステムの操作をシミュレートします。
"""

import pytest
import tempfile
import os
import json
from unittest.mock import Mock, patch, mock_open
from datetime import datetime
from beaconbase import (
    MonitoringSystem,
    MonitoringError,
    RetryableError,
    CheckStatus,
    CheckResult
)
import docker
import paramiko
import ping3
import requests
import yaml
from typing import Dict, Any
from unittest.mock import MagicMock


@pytest.fixture
def temp_dir():
    """一時ディレクトリを作成"""
    with tempfile.TemporaryDirectory() as temp_dir:
        yield temp_dir


@pytest.fixture
def config_data(temp_dir) -> Dict[str, Any]:
    """テスト用の設定データを生成"""
    return {
        'storage': {
            'output_folder': temp_dir
        },
        'log_collection': {
            'servers': [{
                'name': "test_server",
                'host': "127.0.0.1",
                'ssh_username': "test_user1",
                'ssh_key_path': "/tmp/test_key1",
                'log_paths': ["/var/log/test.log"]
            }]
        },
        'ping_targets': [{
            'name': "test-router",
            'host': "192.168.1.1"
        }],
        'docker_monitoring': {
            'servers': [{
                'host': "127.0.0.1",
                'ssh_username': "test_user2",
                'ssh_key_path': "/tmp/test_key2",
                'containers': [{
                    'name': "test_container",
                    'type': "web",
                    'health_check_url': "http://localhost:8080"
                }]
            }]
        },
        'default_ssh': {
            'username': "default_user",
            'key_path': "/tmp/default_key"
        },
        'web_health_checks': {
            'targets': [{
                'name': "test-website",
                'url': "https://example.com",
                'timeout': 5,
                'verify_ssl': True
            }]
        }
    }


@pytest.fixture
def config_file(config_data: Dict[str, Any], temp_dir: str) -> str:
    """設定ファイルのテストデータを生成"""
    config_path = os.path.join(temp_dir, 'config.yaml')
    with open(config_path, 'w') as f:
        yaml.dump(config_data, f)
    return config_path


@pytest.fixture
def monitoring_system(config_file: str) -> MonitoringSystem:
    """テスト用のMonitoringSystemインスタンスを作成"""
    with patch('os.path.exists') as mock_exists:
        mock_exists.return_value = True
        system = MonitoringSystem(config_file)
        yield system


class TestMonitoringSystem:
    """MonitoringSystemクラスのテスト"""

    def test_config_validation(self, monitoring_system: MonitoringSystem):
        """設定ファイルの検証テスト"""
        with patch('os.path.exists') as mock_exists:
            mock_exists.return_value = True
            assert monitoring_system.validate_config() is None

    def test_ping_check_success(self, monitoring_system: MonitoringSystem):
        """Ping成功時のテスト"""
        with patch('ping3.ping') as mock_ping:
            mock_ping.return_value = 0.123  # 応答時間（秒）

            results = monitoring_system.check_ping()
            assert len(results) == 1
            assert results[0].status == CheckStatus.OK
            assert results[0].name == "test-router"
            assert isinstance(results[0].timestamp, str)
            assert 'response_time' in results[0].details
            assert results[0].details['response_time'] == 0.123

    def test_ping_check_failure(self, monitoring_system: MonitoringSystem):
        """Ping失敗時のテスト"""
        with patch('ping3.ping') as mock_ping:
            mock_ping.return_value = None  # 到達不可能

            results = monitoring_system.check_ping()
            assert len(results) == 1
            assert results[0].status == CheckStatus.ERROR
            assert results[0].name == "test-router"
            assert isinstance(results[0].timestamp, str)
            assert 'error' in results[0].details
            assert results[0].details['error'] == 'Host unreachable'

    def test_docker_container_check(self, monitoring_system: MonitoringSystem):
        """Dockerコンテナ確認機能のテスト（SSH経由）"""
        with patch('paramiko.SSHClient') as mock_ssh:
            mock_ssh_instance = Mock()
            mock_ssh.return_value = mock_ssh_instance

            # docker ps のモック
            mock_stdout_ps = Mock()
            mock_stdout_ps.read.return_value = b'Up 2 days'
            mock_stdout_ps.channel.recv_exit_status.return_value = 0

            # docker inspect のモック
            mock_stdout_inspect = Mock()
            inspect_data = {
                'Created': '2023-01-01T00:00:00Z',
                'State': {'Status': 'running', 'Running': True}
            }
            mock_stdout_inspect.read.return_value = json.dumps([inspect_data]).encode()
            mock_stdout_inspect.channel.recv_exit_status.return_value = 0

            mock_ssh_instance.exec_command.side_effect = [
                (None, mock_stdout_ps, None),
                (None, mock_stdout_inspect, None)
            ]

            results = monitoring_system.check_docker_containers()
            assert len(results) == 1
            assert results[0].status == CheckStatus.OK
            assert results[0].name == "test_container"
            assert isinstance(results[0].timestamp, str)
            assert results[0].details['status'] == 'Up 2 days'
            assert results[0].details['host'] == '127.0.0.1'
            assert results[0].details['state']['Status'] == 'running'

    def test_web_health_check(self, monitoring_system: MonitoringSystem):
        """Webヘルスチェック機能のテスト"""
        with patch('requests.get') as mock_get:
            mock_response = Mock()
            mock_response.status_code = 200
            mock_response.elapsed.total_seconds.return_value = 0.5
            mock_get.return_value = mock_response

            results = monitoring_system.check_web_health()
            assert len(results) == 1
            assert results[0].status == CheckStatus.OK
            assert results[0].name == "test-website"
            assert isinstance(results[0].timestamp, str)
            assert results[0].details['response_code'] == 200
            assert results[0].details['response_time'] == 0.5

    def test_web_health_check_error(self, monitoring_system: MonitoringSystem):
        """Webヘルスチェックのエラー処理テスト"""
        with patch('requests.get') as mock_get:
            mock_get.side_effect = requests.exceptions.Timeout("Connection timed out")

            results = monitoring_system.check_web_health()
            assert len(results) == 1
            assert results[0].status == CheckStatus.ERROR
            assert results[0].name == "test-website"
            assert isinstance(results[0].timestamp, str)
            assert 'error' in results[0].details
            assert 'Connection timed out' in results[0].details['error']

    def test_update_summary(self, monitoring_system: MonitoringSystem, temp_dir):
        """サマリー更新機能のテスト"""
        monitoring_system.config['storage']['output_folder'] = temp_dir
        
        # Webヘルスチェックのテストデータ
        test_data = [
            CheckResult(
                name='test-website',
                status=CheckStatus.OK,
                timestamp=datetime.now().isoformat(),
                details={
                    'url': 'https://example.com',
                    'response_code': 200,
                    'response_time': 0.5
                }
            )
        ]
        
        # サマリーを更新
        monitoring_system._update_summary('web_health', test_data)
        
        # サマリーファイルの確認
        summary_path = os.path.join(temp_dir, 'monitoring_summary.json')
        assert os.path.exists(summary_path)
        
        with open(summary_path) as f:
            content = json.load(f)
            assert 'web_health' in content
            assert len(content['web_health']) == 1
            assert content['web_health'][0]['name'] == 'test-website'
            assert content['web_health'][0]['status'] == 'OK'
            assert content['web_health'][0]['details']['response_code'] == 200
            assert content['web_health'][0]['details']['response_time'] == 0.5

    def test_validate_config_web_health(self, monitoring_system: MonitoringSystem):
        """Webヘルスチェック設定の検証テスト"""
        with patch('os.path.exists') as mock_exists:
            mock_exists.return_value = True
            
            # 正常な設定
            assert monitoring_system.validate_config() is None

            # targets が missing
            monitoring_system.config['web_health_checks'] = {}
            error = monitoring_system.validate_config()
            assert error is not None
            assert "Missing 'targets' in web_health_checks" in error

            # 必須フィールドが missing
            monitoring_system.config['web_health_checks'] = {
                'targets': [{'name': 'test'}]  # url が missing
            }
            error = monitoring_system.validate_config()
            assert error is not None
            assert "Missing required fields" in error

    def test_collect_logs_with_deletion(self):
        """ログ収集とファイル削除のテスト"""
        # テスト用の一時ディレクトリを作成
        with tempfile.TemporaryDirectory() as temp_dir:
            # テスト用の設定を作成
            config = {
                'storage': {'output_folder': temp_dir},
                'log_collection': {
                    'delete_after_collection': True,
                    'servers': [{
                        'name': 'test-server',
                        'host': 'localhost',
                        'log_paths': ['/var/log/test.log']
                    }]
                }
            }
            
            # SSHクライアントのモックを作成
            mock_ssh = MagicMock()
            mock_sftp = MagicMock()
            mock_ssh.open_sftp.return_value = mock_sftp
            
            # ファイルの存在確認と取得をモック
            mock_sftp.stat.return_value = True
            mock_sftp.get.return_value = None
            
            with patch('paramiko.SSHClient', return_value=mock_ssh):
                monitor = MonitoringSystem(config)
                results = monitor.collect_logs()
            
            # ファイルが削除されたことを確認
            mock_sftp.remove.assert_called_once_with('/var/log/test.log')
            
            # 結果の検証
            assert len(results) == 1
            assert results[0].status == CheckStatus.OK
            assert results[0].details['server_file_status'] == 'deleted'

    def test_collect_logs_without_deletion(self):
        """ログ収集（ファイル削除なし）のテスト"""
        # テスト用の一時ディレクトリを作成
        with tempfile.TemporaryDirectory() as temp_dir:
            # テスト用の設定を作成
            config = {
                'storage': {'output_folder': temp_dir},
                'log_collection': {
                    'delete_after_collection': False,
                    'servers': [{
                        'name': 'test-server',
                        'host': 'localhost',
                        'log_paths': ['/var/log/test.log']
                    }]
                }
            }
            
            # SSHクライアントのモックを作成
            mock_ssh = MagicMock()
            mock_sftp = MagicMock()
            mock_ssh.open_sftp.return_value = mock_sftp
            
            # ファイルの存在確認と取得をモック
            mock_sftp.stat.return_value = True
            mock_sftp.get.return_value = None
            
            with patch('paramiko.SSHClient', return_value=mock_ssh):
                monitor = MonitoringSystem(config)
                results = monitor.collect_logs()
            
            # ファイルが削除されていないことを確認
            mock_sftp.remove.assert_not_called()
            
            # 結果の検証
            assert len(results) == 1
            assert results[0].status == CheckStatus.OK
            assert results[0].details['server_file_status'] == 'preserved'
