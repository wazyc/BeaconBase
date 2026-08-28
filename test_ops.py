"""実運用向け機能（ポート・ディスク・状態・通知・ダッシュボード）のテスト。"""

from __future__ import annotations

import json
import os
from datetime import datetime, timedelta
from unittest.mock import Mock, patch

import yaml

from alerts import AlertDispatcher, format_event_line
from beaconbase import CheckResult, CheckStatus, MonitoringSystem
from dashboard import render_dashboard_html
from status_store import AlertEvent, StatusStore


def _write_config(temp_dir, extra=None):
    data = {"storage": {"output_folder": temp_dir}}
    if extra:
        data.update(extra)
    path = os.path.join(temp_dir, "config.yaml")
    with open(path, "w", encoding="utf-8") as f:
        yaml.dump(data, f)
    return path


def _result(name, status, **details):
    return CheckResult(
        name=name,
        status=status,
        timestamp=datetime.now().isoformat(),
        details=details,
    )


class TestPortAndDiskChecks:
    def test_parse_df_p_skips_tmpfs(self):
        text = """\
Filesystem     1024-blocks      Used Available Capacity Mounted on
/dev/sda1         1000000    900000    100000      90% /
tmpfs               65536         0     65536       0% /run
overlay           2000000    100000   1900000       5% /var/lib/docker
"""
        rows = MonitoringSystem.parse_df_p(text)
        mounts = {row["mount"] for row in rows}
        assert "/" in mounts
        assert "/run" not in mounts
        assert "/var/lib/docker" not in mounts
        assert rows[0]["use_percent"] == 90

    def test_port_check_success(self, tmp_path):
        config = _write_config(
            str(tmp_path),
            {
                "port_checks": {
                    "targets": [
                        {"name": "ssh", "host": "127.0.0.1", "port": 22, "group": "net"}
                    ]
                }
            },
        )
        system = MonitoringSystem(config)
        with patch.object(system, "_tcp_connect", return_value=(True, 0.01, None)):
            results = system.check_ports()
        assert results[0].status == CheckStatus.OK
        assert results[0].details["port"] == 22
        assert results[0].details["group"] == "net"

    def test_port_check_failure(self, tmp_path):
        config = _write_config(
            str(tmp_path),
            {
                "port_checks": {
                    "targets": [{"name": "smb", "host": "192.0.2.1", "port": 445}]
                }
            },
        )
        system = MonitoringSystem(config)
        with patch.object(
            system, "_tcp_connect", return_value=(False, 0.2, "timed out")
        ):
            results = system.check_ports()
        assert results[0].status == CheckStatus.ERROR
        assert "timed out" in results[0].details["error"]

    def test_disk_thresholds(self, tmp_path):
        config = _write_config(
            str(tmp_path),
            {
                "default_ssh": {"username": "u", "key_path": "/tmp/k"},
                "disk_checks": {
                    "servers": [
                        {
                            "name": "nas",
                            "host": "192.168.1.50",
                            "warn_percent": 80,
                            "error_percent": 90,
                        }
                    ]
                },
            },
        )
        system = MonitoringSystem(config)
        df = """\
Filesystem     1024-blocks Used Available Capacity Mounted on
/dev/sda1            1000  850      150      85% /
"""
        mock_ssh = Mock()
        stdout = Mock()
        stdout.read.return_value = df.encode()
        stderr = Mock()
        stderr.read.return_value = b""
        mock_ssh.exec_command.return_value = (None, stdout, stderr)
        with patch.object(system, "_connect_ssh", return_value=mock_ssh):
            result = system._check_disk_server(
                system.config["disk_checks"]["servers"][0]
            )
        assert result.status == CheckStatus.WARNING
        assert result.details["use_percent"] == 85


class TestStatusStoreAndAlerts:
    def test_alert_after_fail_count(self, tmp_path):
        store = StatusStore(str(tmp_path))
        down = {"ping": [_result("gw", CheckStatus.ERROR, error="unreachable")]}
        events1 = store.update(down, fail_count=2)
        assert events1 == []
        events2 = store.update(down, fail_count=2)
        assert len(events2) == 1
        assert events2[0].kind == "down"
        assert events2[0].consecutive_fail == 2

    def test_recover_only_after_alerted(self, tmp_path):
        store = StatusStore(str(tmp_path))
        down = {"ping": [_result("gw", CheckStatus.ERROR, error="unreachable")]}
        ok = {"ping": [_result("gw", CheckStatus.OK, host="1.1.1.1")]}
        store.update(down, fail_count=1)
        events = store.update(ok, fail_count=1)
        assert [e.kind for e in events] == ["recover"]

    def test_no_recover_if_never_alerted(self, tmp_path):
        store = StatusStore(str(tmp_path))
        down = {"ping": [_result("gw", CheckStatus.ERROR, error="x")]}
        ok = {"ping": [_result("gw", CheckStatus.OK)]}
        store.update(down, fail_count=3)
        events = store.update(ok, fail_count=3)
        assert events == []

    def test_remind_after_interval(self, tmp_path):
        store = StatusStore(str(tmp_path))
        down = {"ping": [_result("gw", CheckStatus.ERROR, error="x")]}
        t0 = datetime(2026, 1, 1, 0, 0, 0)
        store.update(down, fail_count=1, remind_seconds=60, now=t0)
        events = store.update(
            down, fail_count=1, remind_seconds=60, now=t0 + timedelta(seconds=61)
        )
        assert [e.kind for e in events] == ["remind"]

    def test_webhook_on_down(self, tmp_path):
        dispatcher = AlertDispatcher(
            {
                "alerts": {
                    "enabled": True,
                    "webhook": {"url": "https://example.invalid/hook", "format": "generic"},
                }
            },
            str(tmp_path),
        )
        event = AlertEvent(
            kind="down",
            category="ping",
            name="gw",
            status="ERROR",
            previous_status="OK",
            message="unreachable",
            consecutive_fail=2,
        )
        with patch("alerts.requests.post") as mock_post:
            mock_post.return_value = Mock(raise_for_status=lambda: None)
            dispatcher.notify([event])
        mock_post.assert_called_once()
        payload = mock_post.call_args.kwargs["json"]
        assert "BeaconBase" in payload["text"]
        log_path = os.path.join(tmp_path, "alerts.log")
        assert os.path.isfile(log_path)
        assert "障害" in open(log_path, encoding="utf-8").read()

    def test_format_event_line(self):
        event = AlertEvent(
            kind="down",
            category="ports",
            name="nas-smb",
            status="ERROR",
            previous_status="OK",
            message="timed out",
            consecutive_fail=2,
            duration_seconds=90,
        )
        line = format_event_line(event)
        assert "障害" in line
        assert "nas-smb" in line


class TestDashboardAndRetention:
    def test_dashboard_contains_problem(self, tmp_path):
        results = {
            "ping": [
                _result("gw", CheckStatus.ERROR, error="unreachable", group="net"),
                _result("nas", CheckStatus.OK, host="192.168.1.10", response_time=0.01),
            ]
        }
        html = render_dashboard_html(results, refresh_seconds=15)
        assert "gw" in html
        assert "unreachable" in html
        assert "content=\"15\"" in html
        assert "<script>" not in html or "&lt;" in html  # 生の script を名前に出さない前提

    def test_dashboard_escapes_name(self):
        results = {
            "ping": [_result("<script>x</script>", CheckStatus.OK, host="1.1.1.1")]
        }
        html = render_dashboard_html(results)
        assert "<script>x</script>" not in html
        assert "&lt;script&gt;x&lt;/script&gt;" in html

    def test_write_dashboard_and_finalize(self, tmp_path):
        config = _write_config(
            str(tmp_path),
            {
                "web_health_checks": {
                    "targets": [{"name": "site", "url": "https://example.com"}]
                },
                "alerts": {"enabled": True, "fail_count": 1},
            },
        )
        system = MonitoringSystem(config)
        with patch("requests.get") as mock_get:
            mock_response = Mock()
            mock_response.status_code = 503
            mock_response.elapsed.total_seconds.return_value = 0.1
            mock_get.return_value = mock_response
            system.run_all_checks()
        assert os.path.isfile(os.path.join(tmp_path, "index.html"))
        assert os.path.isfile(os.path.join(tmp_path, "runtime_state.json"))
        assert os.path.isfile(os.path.join(tmp_path, "latest.json"))
        assert os.path.isfile(os.path.join(tmp_path, "alerts.log"))

    def test_retain_old_json(self, tmp_path):
        config = _write_config(str(tmp_path), {"settings": {"retain_days": 1}})
        system = MonitoringSystem(config)
        ping_dir = os.path.join(tmp_path, "ping")
        os.makedirs(ping_dir)
        old = os.path.join(ping_dir, "ping_20200101.json")
        new = os.path.join(ping_dir, "ping_" + datetime.now().strftime("%Y%m%d") + ".json")
        for path in (old, new):
            with open(path, "w") as f:
                f.write("[]")
        system._retain_old_files()
        assert not os.path.exists(old)
        assert os.path.exists(new)


class TestOpsCLI:
    def test_validate_port_checks_missing_port(self, tmp_path):
        config = _write_config(
            str(tmp_path),
            {"port_checks": {"targets": [{"name": "x", "host": "1.1.1.1"}]}},
        )
        from monitor import MonitoringCLI

        with patch("sys.argv", ["monitor.py", "-c", config, "--validate"]):
            code = MonitoringCLI().run()
        assert code == 1
