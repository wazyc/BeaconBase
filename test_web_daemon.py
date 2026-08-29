"""常駐スケジューラ・設定編集・WEB のテスト。"""

from __future__ import annotations

import os
import time
from datetime import datetime
from unittest.mock import patch

import pytest
import yaml

from beaconbase import CheckResult, CheckStatus, MonitoringError
from config_manager import (
    ENTRY_FILE_ID,
    create_fragment,
    delete_fragment,
    list_config_files,
    read_config_text,
    write_config_text,
)
from scheduler import MonitoringScheduler, resolve_check_interval
from webapp import create_app


def _write_split_config(tmp_path):
    config_d = tmp_path / "config.d"
    config_d.mkdir()
    (config_d / "00-storage.yaml").write_text(
        yaml.dump({"storage": {"output_folder": str(tmp_path / "out")}}),
        encoding="utf-8",
    )
    (config_d / "05-settings.yaml").write_text(
        yaml.dump({"settings": {"check_interval": 60}}),
        encoding="utf-8",
    )
    (config_d / "40-web.yaml").write_text(
        yaml.dump(
            {
                "web_health_checks": {
                    "targets": [
                        {"name": "ex", "url": "https://example.com", "timeout": 5}
                    ]
                }
            }
        ),
        encoding="utf-8",
    )
    entry = tmp_path / "config.yaml"
    entry.write_text("includes_dir: config.d\n", encoding="utf-8")
    return str(entry)


class TestResolveInterval:
    def test_default_and_override(self):
        assert resolve_check_interval({}) == 300
        assert resolve_check_interval({"settings": {"check_interval": 120}}) == 120
        assert resolve_check_interval({"settings": {"check_interval": 1}}, 10) == 10
        assert resolve_check_interval({"settings": {"check_interval": 1}}) == 5


class TestConfigManager:
    def test_list_read_write(self, tmp_path):
        entry = _write_split_config(tmp_path)
        files = list_config_files(entry)
        ids = [f["id"] for f in files]
        assert ENTRY_FILE_ID in ids
        assert any(i.startswith("fragment:") for i in ids)

        text = read_config_text(entry, "fragment:40-web.yaml")
        assert "example.com" in text

        write_config_text(
            entry,
            "fragment:40-web.yaml",
            yaml.dump(
                {
                    "web_health_checks": {
                        "targets": [
                            {"name": "ex2", "url": "https://example.org", "timeout": 5}
                        ]
                    }
                }
            ),
        )
        assert "example.org" in read_config_text(entry, "fragment:40-web.yaml")

    def test_create_and_delete_fragment(self, tmp_path):
        entry = _write_split_config(tmp_path)
        fid = create_fragment(entry, "60-extra.yaml", "ping_targets: []\n")
        assert fid == "fragment:60-extra.yaml"
        assert any(f["id"] == fid for f in list_config_files(entry))
        delete_fragment(entry, fid)
        assert all(f["id"] != fid for f in list_config_files(entry))

    def test_reject_path_traversal(self, tmp_path):
        entry = _write_split_config(tmp_path)
        with pytest.raises(MonitoringError):
            read_config_text(entry, "fragment:../config.yaml")


class TestScheduler:
    def test_run_once(self, tmp_path):
        entry = _write_split_config(tmp_path)
        sched = MonitoringScheduler(entry, interval=60)
        fake = {
            "web_health": [
                CheckResult(
                    name="ex",
                    status=CheckStatus.OK,
                    timestamp=datetime.now().isoformat(),
                    details={},
                )
            ]
        }
        with patch(
            "scheduler.MonitoringSystem.run_all_checks", return_value=fake
        ), patch(
            "scheduler.MonitoringSystem.validate_config", return_value=None
        ):
            # MonitoringSystem.__init__ は本物を使う
            out = sched.run_once()
        assert "web_health" in out["categories"]
        assert sched.get_status()["run_count"] == 1

    def test_request_run_wakes_loop(self, tmp_path):
        entry = _write_split_config(tmp_path)
        sched = MonitoringScheduler(entry, interval=3600)
        calls = {"n": 0}

        def fake_run_once(categories=None):
            calls["n"] += 1
            return {"counts": {}, "categories": []}

        with patch.object(sched, "run_once", side_effect=fake_run_once):
            sched.start(run_immediately=False)
            time.sleep(0.2)
            assert calls["n"] == 0
            assert sched.request_run() is True
            deadline = time.time() + 2
            while calls["n"] < 1 and time.time() < deadline:
                time.sleep(0.05)
            sched.stop()
        assert calls["n"] >= 1


class TestWebApp:
    def test_health_and_status_pages(self, tmp_path):
        entry = _write_split_config(tmp_path)
        app = create_app(entry, scheduler=None)
        client = app.test_client()
        assert client.get("/healthz").status_code == 200
        status = client.get("/")
        assert status.status_code == 200
        assert "監視状況".encode("utf-8") in status.data
        cfg = client.get("/config")
        assert cfg.status_code == 200
        assert b"config.yaml" in cfg.data or "config.yaml".encode() in cfg.data

    def test_config_save_api(self, tmp_path):
        entry = _write_split_config(tmp_path)
        app = create_app(entry, scheduler=None)
        client = app.test_client()
        resp = client.put(
            "/api/config/files/fragment:05-settings.yaml",
            json={
                "content": yaml.dump(
                    {"settings": {"check_interval": 90, "max_workers": 3}}
                )
            },
        )
        assert resp.status_code == 200
        assert resp.get_json()["ok"] is True
        text = read_config_text(entry, "fragment:05-settings.yaml")
        assert "90" in text
