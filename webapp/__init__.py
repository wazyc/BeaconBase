"""BeaconBase WEB アプリケーション（状況確認・設定編集）。"""

from __future__ import annotations

import json
import logging
import os
from datetime import datetime
from typing import Any, Dict, List, Optional

from flask import Flask, jsonify, redirect, render_template, request, url_for

from beaconbase import (
    CHECK_CATEGORIES,
    CheckResult,
    CheckStatus,
    MonitoringError,
    MonitoringSystem,
    is_monitoring_failure,
)
from config_manager import (
    ENTRY_FILE_ID,
    create_fragment,
    delete_fragment,
    dump_merged_yaml,
    list_config_files,
    read_config_text,
    write_config_text,
)
from dashboard import CATEGORY_LABELS, _detail_text, _status_class
from status_store import StatusStore
from alerts import format_duration

logger = logging.getLogger("BeaconBase.Web")


def _results_from_latest(output_folder: str) -> Dict[str, List[CheckResult]]:
    """latest.json から CheckResult 辞書を復元する。"""
    path = os.path.join(output_folder, "latest.json")
    if not os.path.isfile(path):
        return {}
    try:
        with open(path, "r", encoding="utf-8") as f:
            payload = json.load(f)
    except (OSError, json.JSONDecodeError):
        return {}
    results: Dict[str, List[CheckResult]] = {}
    raw_results = payload.get("results") or {}
    if not isinstance(raw_results, dict):
        return {}
    for category, items in raw_results.items():
        if not isinstance(items, list):
            continue
        parsed: List[CheckResult] = []
        for item in items:
            if not isinstance(item, dict):
                continue
            status_name = item.get("status") or "ERROR"
            try:
                status = CheckStatus[status_name]
            except KeyError:
                status = CheckStatus.ERROR
            parsed.append(
                CheckResult(
                    name=str(item.get("name") or ""),
                    status=status,
                    timestamp=str(item.get("timestamp") or ""),
                    details=item.get("details") if isinstance(item.get("details"), dict) else {},
                )
            )
        results[category] = parsed
    return results


def _output_folder_for(config_path: str) -> Optional[str]:
    try:
        with MonitoringSystem(config_path) as monitor:
            return monitor.config["storage"]["output_folder"]
    except Exception:
        return None


def create_app(config_path: str, scheduler=None) -> Flask:
    """Flask アプリを生成する。"""
    app = Flask(__name__)
    app.config["BEACONBASE_CONFIG"] = config_path
    app.config["SCHEDULER"] = scheduler

    @app.context_processor
    def inject_globals():
        sched = app.config.get("SCHEDULER")
        status = sched.get_status() if sched else None
        return {
            "app_title": "BeaconBase",
            "scheduler_status": status,
            "category_labels": CATEGORY_LABELS,
        }

    @app.get("/")
    def index():
        config_path = app.config["BEACONBASE_CONFIG"]
        output_folder = _output_folder_for(config_path)
        results = _results_from_latest(output_folder) if output_folder else {}
        store = StatusStore(output_folder) if output_folder else None
        now = datetime.now()

        problems = []
        warnings = []
        rows = []
        total = 0
        for category in CHECK_CATEGORIES:
            data = results.get(category) or []
            if not data:
                continue
            for result in data:
                total += 1
                if is_monitoring_failure(category, result):
                    problems.append((category, result))
                elif result.status == CheckStatus.WARNING:
                    warnings.append((category, result))
            label = CATEGORY_LABELS.get(category, category)
            items = []
            for result in data:
                item = store.get_item(category, result.name) if store else {}
                duration = ""
                if is_monitoring_failure(category, result):
                    started = item.get("first_failure_at")
                    if started:
                        try:
                            started_dt = datetime.fromisoformat(started)
                            duration = format_duration((now - started_dt).total_seconds())
                        except ValueError:
                            duration = ""
                items.append(
                    {
                        "name": result.name,
                        "status": result.status.name,
                        "status_class": _status_class(result.status.name),
                        "group": result.details.get("group") or item.get("group") or "",
                        "detail": _detail_text(result),
                        "duration": duration,
                    }
                )
            rows.append({"category": category, "label": label, "items": items})

        if problems:
            overall, overall_class = "障害", "error"
        elif warnings:
            overall, overall_class = "警告", "warn"
        elif total:
            overall, overall_class = "正常", "ok"
        else:
            overall, overall_class = "未実行", "unknown"

        latest_ts = None
        if output_folder:
            latest_path = os.path.join(output_folder, "latest.json")
            if os.path.isfile(latest_path):
                try:
                    with open(latest_path, "r", encoding="utf-8") as f:
                        latest_ts = json.load(f).get("timestamp")
                except (OSError, json.JSONDecodeError):
                    latest_ts = None

        return render_template(
            "status.html",
            overall=overall,
            overall_class=overall_class,
            total=total,
            problem_count=len(problems),
            warning_count=len(warnings),
            rows=rows,
            problems=problems + warnings,
            detail_text=_detail_text,
            status_class=_status_class,
            latest_timestamp=latest_ts,
            output_folder=output_folder,
        )

    @app.get("/config")
    def config_page():
        config_path = app.config["BEACONBASE_CONFIG"]
        try:
            files = list_config_files(config_path)
        except MonitoringError as e:
            return render_template(
                "config.html",
                files=[],
                selected_id=None,
                content="",
                error=str(e),
                merged_preview="",
                message=None,
            )
        selected = request.args.get("file") or ENTRY_FILE_ID
        ids = {f["id"] for f in files}
        if selected not in ids:
            selected = ENTRY_FILE_ID
        error = request.args.get("error")
        message = request.args.get("message")
        try:
            content = read_config_text(config_path, selected)
            merged_preview = dump_merged_yaml(config_path)
        except MonitoringError as e:
            content = ""
            merged_preview = ""
            error = str(e)
        return render_template(
            "config.html",
            files=files,
            selected_id=selected,
            content=content,
            error=error,
            message=message,
            merged_preview=merged_preview,
        )

    @app.post("/config/save")
    def config_save():
        config_path = app.config["BEACONBASE_CONFIG"]
        file_id = request.form.get("file_id") or ENTRY_FILE_ID
        content = request.form.get("content")
        if content is None:
            return redirect(url_for("config_page", file=file_id, error="本文が空です"))
        try:
            write_config_text(config_path, file_id, content)
            # 保存後に設定検証
            with MonitoringSystem(config_path) as monitor:
                verr = monitor.validate_config()
            if verr:
                return redirect(
                    url_for(
                        "config_page",
                        file=file_id,
                        error=f"保存しましたが検証エラー: {verr}",
                    )
                )
        except MonitoringError as e:
            return redirect(url_for("config_page", file=file_id, error=str(e)))
        return redirect(
            url_for("config_page", file=file_id, message="設定を保存しました")
        )

    @app.post("/config/create")
    def config_create():
        config_path = app.config["BEACONBASE_CONFIG"]
        filename = (request.form.get("filename") or "").strip()
        try:
            file_id = create_fragment(config_path, filename)
        except MonitoringError as e:
            return redirect(url_for("config_page", error=str(e)))
        return redirect(
            url_for("config_page", file=file_id, message=f"{filename} を作成しました")
        )

    @app.post("/config/delete")
    def config_delete():
        config_path = app.config["BEACONBASE_CONFIG"]
        file_id = request.form.get("file_id") or ""
        try:
            delete_fragment(config_path, file_id)
        except MonitoringError as e:
            return redirect(url_for("config_page", file=file_id, error=str(e)))
        return redirect(url_for("config_page", message="断片を削除しました"))

    @app.post("/run")
    def run_now():
        sched = app.config.get("SCHEDULER")
        want_json = (
            request.accept_mimetypes.best == "application/json"
            or request.headers.get("X-Requested-With") == "XMLHttpRequest"
            or request.args.get("format") == "json"
        )
        if not sched:
            if want_json:
                return jsonify({"ok": False, "error": "スケジューラがありません"}), 503
            return redirect(url_for("index"))
        ok = sched.request_run()
        if want_json:
            return jsonify({"ok": ok, "status": sched.get_status()})
        return redirect(url_for("index"))

    @app.get("/api/status")
    def api_status():
        config_path = app.config["BEACONBASE_CONFIG"]
        output_folder = _output_folder_for(config_path)
        latest = None
        if output_folder:
            path = os.path.join(output_folder, "latest.json")
            if os.path.isfile(path):
                try:
                    with open(path, "r", encoding="utf-8") as f:
                        latest = json.load(f)
                except (OSError, json.JSONDecodeError):
                    latest = None
        sched = app.config.get("SCHEDULER")
        return jsonify(
            {
                "scheduler": sched.get_status() if sched else None,
                "latest": latest,
                "output_folder": output_folder,
            }
        )

    @app.get("/api/config/files")
    def api_config_files():
        try:
            files = list_config_files(app.config["BEACONBASE_CONFIG"])
            return jsonify({"files": files})
        except MonitoringError as e:
            return jsonify({"error": str(e)}), 400

    @app.get("/api/config/files/<path:file_id>")
    def api_config_get(file_id: str):
        try:
            content = read_config_text(app.config["BEACONBASE_CONFIG"], file_id)
            return jsonify({"id": file_id, "content": content})
        except MonitoringError as e:
            return jsonify({"error": str(e)}), 400

    @app.put("/api/config/files/<path:file_id>")
    def api_config_put(file_id: str):
        payload = request.get_json(silent=True) or {}
        content = payload.get("content")
        if content is None:
            return jsonify({"error": "content が必要です"}), 400
        try:
            write_config_text(app.config["BEACONBASE_CONFIG"], file_id, content)
            with MonitoringSystem(app.config["BEACONBASE_CONFIG"]) as monitor:
                verr = monitor.validate_config()
            if verr:
                return jsonify({"ok": True, "warning": verr})
            return jsonify({"ok": True})
        except MonitoringError as e:
            return jsonify({"error": str(e)}), 400

    @app.get("/healthz")
    def healthz():
        return jsonify({"status": "ok"})

    return app
