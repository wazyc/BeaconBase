"""監視結果の状態追跡（連続失敗・障害継続時間・通知判定）。

output フォルダの runtime_state.json に項目ごとの状態を残し、
今回の結果と比較して通知イベントを返す。
"""

from __future__ import annotations

import json
import os
from dataclasses import dataclass
from datetime import datetime
from typing import Any, Dict, List, Optional

from beaconbase import CheckResult, CheckStatus, is_monitoring_failure


STATE_FILENAME = "runtime_state.json"


@dataclass
class AlertEvent:
    """通知1件。kind は down / recover / remind。"""

    kind: str
    category: str
    name: str
    status: str
    previous_status: str
    message: str
    consecutive_fail: int
    first_failure_at: Optional[str] = None
    duration_seconds: Optional[float] = None

    def to_dict(self) -> Dict[str, Any]:
        return {
            "kind": self.kind,
            "category": self.category,
            "name": self.name,
            "status": self.status,
            "previous_status": self.previous_status,
            "message": self.message,
            "consecutive_fail": self.consecutive_fail,
            "first_failure_at": self.first_failure_at,
            "duration_seconds": self.duration_seconds,
        }


def item_key(category: str, name: str) -> str:
    return f"{category}:{name}"


def _parse_iso(value: Optional[str]) -> Optional[datetime]:
    if not value:
        return None
    try:
        return datetime.fromisoformat(value)
    except ValueError:
        return None


def _duration_seconds(started_at: Optional[str], now: datetime) -> Optional[float]:
    started = _parse_iso(started_at)
    if started is None:
        return None
    return max(0.0, (now - started).total_seconds())


def _detail_message(result: CheckResult) -> str:
    return str(
        result.details.get("error")
        or result.details.get("message")
        or result.status.name
    )


class StatusStore:
    """runtime_state.json の読み書きと通知イベント生成。"""

    def __init__(self, output_folder: str):
        self.path = os.path.join(output_folder, STATE_FILENAME)
        self.data: Dict[str, Any] = {"items": {}}
        self.load()

    def load(self) -> None:
        if not os.path.isfile(self.path):
            self.data = {"items": {}}
            return
        try:
            with open(self.path, "r", encoding="utf-8") as f:
                raw = json.load(f)
            if isinstance(raw, dict) and isinstance(raw.get("items"), dict):
                self.data = raw
            else:
                self.data = {"items": {}}
        except (OSError, json.JSONDecodeError):
            self.data = {"items": {}}

    def save(self, now: Optional[datetime] = None) -> None:
        self.data["updated_at"] = (now or datetime.now()).isoformat()
        os.makedirs(os.path.dirname(self.path) or ".", exist_ok=True)
        with open(self.path, "w", encoding="utf-8") as f:
            json.dump(self.data, f, indent=2, ensure_ascii=False)

    def get_item(self, category: str, name: str) -> Dict[str, Any]:
        return dict(self.data.get("items", {}).get(item_key(category, name), {}))

    def update(
        self,
        results: Dict[str, List[CheckResult]],
        fail_count: int = 2,
        remind_seconds: float = 3600,
        now: Optional[datetime] = None,
    ) -> List[AlertEvent]:
        """今回の結果で状態を更新し、通知すべきイベントを返す。

        今回走っていないカテゴリの項目は触らない（--only で他監視を消さない）。
        """
        now = now or datetime.now()
        now_iso = now.isoformat()
        fail_count = max(1, int(fail_count))
        remind_seconds = max(0.0, float(remind_seconds))
        events: List[AlertEvent] = []
        items = self.data.setdefault("items", {})

        for category, data in results.items():
            for result in data:
                key = item_key(category, name := result.name)
                prev = items.get(key) or {}
                previous_status = prev.get("status") or "UNKNOWN"
                consecutive = int(prev.get("consecutive_fail") or 0)
                first_failure_at = prev.get("first_failure_at")
                last_alert_at = prev.get("last_alert_at")
                alerting = bool(prev.get("alerting"))
                failing = is_monitoring_failure(category, result)
                recovered = result.status == CheckStatus.OK

                if failing:
                    consecutive += 1
                    if not first_failure_at:
                        first_failure_at = now_iso
                    duration = _duration_seconds(first_failure_at, now)
                    message = _detail_message(result)
                    if not alerting and consecutive >= fail_count:
                        events.append(
                            AlertEvent(
                                kind="down",
                                category=category,
                                name=name,
                                status=result.status.name,
                                previous_status=previous_status,
                                message=message,
                                consecutive_fail=consecutive,
                                first_failure_at=first_failure_at,
                                duration_seconds=duration,
                            )
                        )
                        alerting = True
                        last_alert_at = now_iso
                    elif (
                        alerting
                        and remind_seconds > 0
                        and last_alert_at
                    ):
                        last_dt = _parse_iso(last_alert_at)
                        if last_dt and (now - last_dt).total_seconds() >= remind_seconds:
                            events.append(
                                AlertEvent(
                                    kind="remind",
                                    category=category,
                                    name=name,
                                    status=result.status.name,
                                    previous_status=previous_status,
                                    message=message,
                                    consecutive_fail=consecutive,
                                    first_failure_at=first_failure_at,
                                    duration_seconds=duration,
                                )
                            )
                            last_alert_at = now_iso
                elif recovered:
                    if alerting:
                        events.append(
                            AlertEvent(
                                kind="recover",
                                category=category,
                                name=name,
                                status=result.status.name,
                                previous_status=previous_status,
                                message="回復",
                                consecutive_fail=consecutive,
                                first_failure_at=first_failure_at,
                                duration_seconds=_duration_seconds(first_failure_at, now),
                            )
                        )
                    consecutive = 0
                    first_failure_at = None
                    alerting = False
                # WARNING などは連続失敗を維持もリセットもしない

                items[key] = {
                    "category": category,
                    "name": name,
                    "status": result.status.name,
                    "consecutive_fail": consecutive,
                    "first_failure_at": first_failure_at,
                    "last_ok_at": now_iso if recovered else prev.get("last_ok_at"),
                    "last_alert_at": last_alert_at,
                    "alerting": alerting,
                    "updated_at": now_iso,
                    "group": result.details.get("group") or prev.get("group") or "",
                    "message": _detail_message(result),
                }

        self.data["items"] = items
        return events
