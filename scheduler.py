"""常駐監視スケジューラ。

設定の settings.check_interval（秒）ごとに MonitoringSystem を起動し、
結果・ダッシュボード・通知まで既存パイプラインに任せる。
"""

from __future__ import annotations

import logging
import threading
import time
from datetime import datetime
from typing import Any, Dict, List, Optional, Sequence

from beaconbase import CHECK_CATEGORIES, MonitoringError, MonitoringSystem
from config_loader import load_merged_yaml_config

logger = logging.getLogger("BeaconBase.Scheduler")

DEFAULT_CHECK_INTERVAL = 300


def resolve_check_interval(config: Dict[str, Any], override: Optional[float] = None) -> float:
    """監視間隔（秒）を決める。override があればそれを優先する。"""
    if override is not None:
        return max(5.0, float(override))
    settings = config.get("settings") if isinstance(config.get("settings"), dict) else {}
    raw = settings.get("check_interval", DEFAULT_CHECK_INTERVAL)
    try:
        return max(5.0, float(raw))
    except (TypeError, ValueError):
        return float(DEFAULT_CHECK_INTERVAL)


class MonitoringScheduler:
    """バックグラウンドで定期監視を回す。"""

    def __init__(
        self,
        config_path: str,
        interval: Optional[float] = None,
        categories: Optional[Sequence[str]] = None,
    ):
        self.config_path = config_path
        self.interval_override = interval
        self.categories = list(categories) if categories else None
        self._stop = threading.Event()
        self._wake = threading.Event()
        self._lock = threading.RLock()
        self._thread: Optional[threading.Thread] = None
        self._running_check = False
        self._last_started_at: Optional[str] = None
        self._last_finished_at: Optional[str] = None
        self._last_error: Optional[str] = None
        self._last_counts: Dict[str, Any] = {}
        self._next_run_at: Optional[float] = None
        self._run_count = 0
        self._run_immediately = True

    @property
    def is_alive(self) -> bool:
        return self._thread is not None and self._thread.is_alive()

    def start(self, run_immediately: bool = True) -> None:
        """スケジューラを開始する。二重起動はしない。"""
        with self._lock:
            if self.is_alive:
                return
            self._stop.clear()
            self._run_immediately = run_immediately
            if not run_immediately:
                self._next_run_at = time.time() + self._current_interval_unlocked()
            self._thread = threading.Thread(
                target=self._loop,
                name="beaconbase-scheduler",
                daemon=True,
            )
            self._thread.start()
            logger.info("監視スケジューラを開始しました")

    def stop(self, timeout: float = 10.0) -> None:
        """スケジューラを停止する。"""
        self._stop.set()
        self._wake.set()
        thread = self._thread
        if thread and thread.is_alive():
            thread.join(timeout=timeout)
        self._thread = None
        logger.info("監視スケジューラを停止しました")

    def request_run(self) -> bool:
        """すぐに1回実行するよう起こす。すでに実行中なら False。"""
        with self._lock:
            if self._running_check:
                return False
        self._wake.set()
        return True

    def run_once(self, categories: Optional[Sequence[str]] = None) -> Dict[str, Any]:
        """同期的に1回監視する。"""
        with self._lock:
            if self._running_check:
                raise MonitoringError("監視はすでに実行中です")
            self._running_check = True
            self._last_started_at = datetime.now().isoformat()
            self._last_error = None
        try:
            selected = list(categories) if categories is not None else self.categories
            if selected:
                unknown = [c for c in selected if c not in CHECK_CATEGORIES]
                if unknown:
                    raise MonitoringError(
                        f"不明な監視カテゴリです: {', '.join(unknown)}"
                    )
            with MonitoringSystem(self.config_path) as monitor:
                validation_error = monitor.validate_config()
                if validation_error:
                    raise MonitoringError(validation_error)
                results = monitor.run_all_checks(categories=selected)
                counts = monitor.count_statuses(results)
            with self._lock:
                self._last_counts = counts
                self._run_count += 1
                self._last_finished_at = datetime.now().isoformat()
            return {"counts": counts, "categories": list(results.keys())}
        except Exception as e:
            with self._lock:
                self._last_error = str(e)
                self._last_finished_at = datetime.now().isoformat()
            raise
        finally:
            with self._lock:
                self._running_check = False

    def get_status(self) -> Dict[str, Any]:
        """UI / API 向けの状態辞書を返す。"""
        with self._lock:
            interval = self._current_interval_unlocked()
            next_in = None
            if self._next_run_at is not None:
                next_in = max(0.0, self._next_run_at - time.time())
            return {
                "alive": self.is_alive,
                "running": self._running_check,
                "config_path": self.config_path,
                "interval_seconds": interval,
                "categories": self.categories,
                "run_count": self._run_count,
                "last_started_at": self._last_started_at,
                "last_finished_at": self._last_finished_at,
                "last_error": self._last_error,
                "last_counts": self._last_counts,
                "next_run_in_seconds": next_in,
            }

    def _current_interval(self) -> float:
        with self._lock:
            return self._current_interval_unlocked()

    def _current_interval_unlocked(self) -> float:
        try:
            config = load_merged_yaml_config(self.config_path)
        except Exception:
            return resolve_check_interval({}, self.interval_override)
        return resolve_check_interval(config, self.interval_override)

    def _loop(self) -> None:
        skip_first = not self._run_immediately
        while not self._stop.is_set():
            if skip_first:
                skip_first = False
            else:
                try:
                    self.run_once()
                except Exception as e:
                    logger.error(f"定期監視に失敗しました: {e}", exc_info=True)

            interval = self._current_interval()
            deadline = time.time() + interval
            with self._lock:
                self._next_run_at = deadline

            while not self._stop.is_set():
                remaining = deadline - time.time()
                if remaining <= 0:
                    break
                triggered = self._wake.wait(timeout=min(1.0, remaining))
                if triggered:
                    self._wake.clear()
                    if self._stop.is_set():
                        return
                    # 手動実行要求: 待ちを打ち切ってすぐループ先頭へ
                    break
