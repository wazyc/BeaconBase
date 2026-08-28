# BeaconBase

ローカルネットワーク向けの簡易監視。Ping・TCPポート・ディスク・Docker・Web を設定した対象だけ実行し、ブラウザで状態を見る。

## 概要

- サーバーログの収集
- Ping と TCP ポートの死活
- SSH 経由のディスク使用率
- Docker コンテナの状態
- Web / API のヘルスチェック
- 状態変化の通知（ファイル / Webhook / メール / コマンド）
- 結果フォルダの HTML ダッシュボード

使わない機能のセクションは省略してよい。必須は `storage.output_folder` のみ。

## セットアップ

必要条件: Python 3.8 以上。SSH を使う監視は鍵認証。Ping は管理者権限がなくても OS の `ping` にフォールバックする。

```bash
git clone [repository-url]
cd beaconbase
python -m venv venv
source venv/bin/activate  # Linux/Mac
# venv\Scripts\activate   # Windows
pip install -r requirements.txt
cp config_sample.yaml config.yaml
```

```bash
python monitor.py -c config.yaml --validate
```

## 使い方

```bash
# 1回だけ実行
python monitor.py -c config.yaml

# 死活だけ（Ping とポート）
python monitor.py -c config.yaml --only ping,ports

# 1分ごとに実行し、LAN へダッシュボードを公開
python monitor.py -c config.yaml --interval 60 --serve 8088

# 設定確認のみ
python monitor.py -c config.yaml --validate
```

ブラウザで `http://<このマシンのIP>:8088/` を開く。`output/index.html` を直接開いてもよい。

運用手順（systemd / タスクスケジューラ / 通知）は [docs/operations.md](docs/operations.md)。

カテゴリ: `logs` / `ping` / `ports` / `disk` / `docker` / `web_health`

Python から:

```python
from beaconbase import MonitoringSystem

with MonitoringSystem("config.yaml") as monitor:
    results = monitor.run_all_checks()
    print(monitor.format_results_summary(results))
```

### 終了コード

| コード | 意味 |
|--------|------|
| 0 | 正常（WARNING のみの場合も含む） |
| 1 | 設定エラーなど、監視を実行できなかった |
| 2 | 監視失敗（ERROR。Docker 等の NOT_FOUND も含む。ログファイル欠落は含めない）。`--interval` 中は終了せず次周期へ進む |
| 130 | 中断（Ctrl+C） |

## 結果ファイル

保存先は `storage.output_folder`。

- `index.html`: ダッシュボード（自動更新）
- `latest.json`: 今回の全結果
- `runtime_state.json`: 連続失敗と障害開始時刻
- `alerts.log`: 障害・回復の履歴
- `check_summary.json` / `error_summary.json`
- カテゴリ別の日次 JSON（`settings.retain_days` で掃除）

## 設定の要点

| セクション | 内容 |
|------------|------|
| `storage` | 結果の保存先。`~` はホームディレクトリに展開する |
| `settings` | リトライ・並列数・保持日数・定期実行の既定秒など |
| `default_ssh` | サーバー個別指定が無いときの SSH 既定値 |
| `ping_targets` | Ping。`group` はダッシュボード用 |
| `port_checks` | TCP ポート |
| `disk_checks` | SSH で `df -P`。使用率の警告 / 異常閾値 |
| `log_collection` | ログ収集。定期実行では既定でスキップする |
| `docker_monitoring` | SSH 経由のコンテナ。`health_check_url` は Docker ホスト上で curl する |
| `web_health_checks` | 監視ホストからの HTTP。`expected_status` で 200 以外も許可できる |
| `alerts` | 連続失敗後に通知。Webhook / コマンド / メール |

分割設定は [docs/configuration.md](docs/configuration.md)。サンプルは [config_sample.yaml](config_sample.yaml)。

## 制限事項

- 同時実行数の既定は 5（`settings.max_workers`）
- 障害判定の連続失敗回数の既定は 2（`alerts.fail_count`）
- Ping タイムアウトの既定は 5 秒
- ダッシュボードの HTTP 公開に認証は無い。`--bind 127.0.0.1` で閉じられる

## ライセンス

MIT
