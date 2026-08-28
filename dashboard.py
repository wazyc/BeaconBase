"""出力フォルダに置く単体 HTML ダッシュボード。

LAN 内のブラウザで開けるよう、外部 CDN は使わない。
"""

from __future__ import annotations

import html
from datetime import datetime
from typing import Any, Dict, List, Optional

from beaconbase import CHECK_CATEGORIES, CheckResult, CheckStatus, is_monitoring_failure
from status_store import StatusStore
from alerts import format_duration


CATEGORY_LABELS = {
    "logs": "ログ収集",
    "ping": "Ping",
    "ports": "ポート",
    "disk": "ディスク",
    "docker": "Docker",
    "web_health": "Web",
}


def _status_class(status: str) -> str:
    mapping = {
        "OK": "ok",
        "WARNING": "warn",
        "ERROR": "error",
        "NOT_FOUND": "error",
    }
    return mapping.get(status, "unknown")


def _esc(value: Any) -> str:
    return html.escape("" if value is None else str(value), quote=True)


def _detail_text(result: CheckResult) -> str:
    if result.status == CheckStatus.OK:
        if "response_time" in result.details:
            try:
                return f"{float(result.details['response_time']) * 1000:.1f} ms"
            except (TypeError, ValueError):
                pass
        if "use_percent" in result.details:
            return f"使用率 {result.details['use_percent']}%"
        if result.details.get("host"):
            return str(result.details.get("host"))
        return "正常"
    return str(
        result.details.get("error")
        or result.details.get("message")
        or result.status.name
    )


def render_dashboard_html(
    results: Dict[str, List[CheckResult]],
    store: Optional[StatusStore] = None,
    refresh_seconds: int = 30,
    generated_at: Optional[datetime] = None,
) -> str:
    """監視結果から完結した HTML 文字列を作る。"""
    generated_at = generated_at or datetime.now()
    refresh_seconds = max(0, int(refresh_seconds))
    problems: List[tuple] = []
    warnings: List[tuple] = []
    total = 0
    for category, data in results.items():
        for result in data:
            total += 1
            if is_monitoring_failure(category, result):
                problems.append((category, result))
            elif result.status == CheckStatus.WARNING:
                warnings.append((category, result))

    if problems:
        overall = "障害"
        overall_class = "error"
    elif warnings:
        overall = "警告"
        overall_class = "warn"
    elif total:
        overall = "正常"
        overall_class = "ok"
    else:
        overall = "対象なし"
        overall_class = "unknown"

    meta_refresh = (
        f'<meta http-equiv="refresh" content="{refresh_seconds}">' if refresh_seconds else ""
    )

    rows = []
    for category in CHECK_CATEGORIES:
        data = results.get(category) or []
        if not data:
            continue
        label = CATEGORY_LABELS.get(category, category)
        rows.append(
            f'<h2 id="{_esc(category)}">{_esc(label)} <small>{len(data)} 件</small></h2>'
        )
        rows.append("<table><thead><tr>")
        rows.append(
            "<th>状態</th><th>名前</th><th>グループ</th><th>詳細</th><th>継続</th>"
        )
        rows.append("</tr></thead><tbody>")
        for result in data:
            item = store.get_item(category, result.name) if store else {}
            duration = ""
            if is_monitoring_failure(category, result):
                started = item.get("first_failure_at")
                if started:
                    try:
                        started_dt = datetime.fromisoformat(started)
                        duration = format_duration(
                            (generated_at - started_dt).total_seconds()
                        )
                    except ValueError:
                        duration = ""
            group = result.details.get("group") or item.get("group") or ""
            st = result.status.name
            rows.append(
                "<tr class='{cls}'><td><span class='pill {cls}'>{st}</span></td>"
                "<td>{name}</td><td>{group}</td><td>{detail}</td><td>{dur}</td></tr>".format(
                    cls=_status_class(st),
                    st=_esc(st),
                    name=_esc(result.name),
                    group=_esc(group),
                    detail=_esc(_detail_text(result)),
                    dur=_esc(duration),
                )
            )
        rows.append("</tbody></table>")

    if not rows:
        rows.append("<p class='empty'>この周期で実行した監視はありません。</p>")

    problem_html = ""
    if problems or warnings:
        problem_html = "<section class='problems'><h2>今見ている問題</h2><ul>"
        for category, result in problems + warnings:
            problem_html += (
                f"<li><span class='pill {_status_class(result.status.name)}'>"
                f"{_esc(result.status.name)}</span> "
                f"{_esc(category)} / {_esc(result.name)} — {_esc(_detail_text(result))}</li>"
            )
        problem_html += "</ul></section>"

    return f"""<!DOCTYPE html>
<html lang="ja">
<head>
  <meta charset="utf-8">
  {meta_refresh}
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>BeaconBase ダッシュボード</title>
  <style>
    :root {{
      --bg: #0f1419;
      --card: #1a2330;
      --text: #e8eef7;
      --muted: #93a0b5;
      --ok: #3dd68c;
      --warn: #f5c542;
      --error: #ff6b6b;
      --line: #2a3550;
    }}
    * {{ box-sizing: border-box; }}
    body {{
      margin: 0; font-family: "Segoe UI", "Hiragino Sans", sans-serif;
      background: var(--bg); color: var(--text); line-height: 1.5;
    }}
    header {{
      padding: 1.25rem 1.5rem; border-bottom: 1px solid var(--line);
      display: flex; flex-wrap: wrap; gap: 1rem; align-items: baseline;
    }}
    h1 {{ margin: 0; font-size: 1.4rem; }}
    h2 {{ margin: 1.5rem 0 0.6rem; font-size: 1.1rem; }}
    h2 small {{ color: var(--muted); font-weight: normal; }}
    main {{ padding: 1rem 1.5rem 3rem; max-width: 1100px; }}
    .meta {{ color: var(--muted); }}
    .pill {{
      display: inline-block; min-width: 4.5rem; text-align: center;
      border-radius: 999px; padding: 0.1rem 0.6rem; font-size: 0.85rem;
      font-weight: 600;
    }}
    .pill.ok {{ background: #163526; color: var(--ok); }}
    .pill.warn {{ background: #3a3214; color: var(--warn); }}
    .pill.error {{ background: #3a1518; color: var(--error); }}
    .pill.unknown {{ background: #2a2f3a; color: var(--muted); }}
    .stats {{ display: flex; gap: 1rem; flex-wrap: wrap; }}
    .stat {{ background: var(--card); padding: 0.8rem 1rem; border-radius: 10px; min-width: 7rem; }}
    .stat b {{ display: block; font-size: 1.4rem; }}
    table {{ width: 100%; border-collapse: collapse; background: var(--card); border-radius: 10px; overflow: hidden; }}
    th, td {{ padding: 0.55rem 0.75rem; text-align: left; border-bottom: 1px solid var(--line); }}
    th {{ color: var(--muted); font-weight: 600; font-size: 0.85rem; }}
    tr.error {{ background: #24141a; }}
    tr.warn {{ background: #241e10; }}
    .problems ul {{ padding-left: 1.1rem; }}
    .empty {{ color: var(--muted); }}
  </style>
</head>
<body>
  <header>
    <h1>BeaconBase</h1>
    <span class="pill {overall_class}">{_esc(overall)}</span>
    <span class="meta">更新 {_esc(generated_at.strftime("%Y-%m-%d %H:%M:%S"))}</span>
  </header>
  <main>
    <div class="stats">
      <div class="stat"><span>対象</span><b>{total}</b></div>
      <div class="stat"><span>障害</span><b>{len(problems)}</b></div>
      <div class="stat"><span>警告</span><b>{len(warnings)}</b></div>
    </div>
    {problem_html}
    {''.join(rows)}
  </main>
</body>
</html>
"""


def write_dashboard(
    output_folder: str,
    results: Dict[str, List[CheckResult]],
    store: Optional[StatusStore] = None,
    refresh_seconds: int = 30,
) -> str:
    """index.html を output フォルダに書き、パスを返す。"""
    import os

    path = os.path.join(output_folder, "index.html")
    html_text = render_dashboard_html(
        results, store=store, refresh_seconds=refresh_seconds
    )
    os.makedirs(output_folder, exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        f.write(html_text)
    return path
