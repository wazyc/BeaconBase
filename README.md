# BeaconBase

インフラ、サーバー、ネットワーク機器、Dockerコンテナ、WEBの簡易な監視

## 概要

次の監視を、設定した対象だけ実行する。使わない機能のセクションは省略してよい。

- サーバーログの収集
- ネットワーク機器の Ping 監視
- Docker コンテナの状態監視
- Web / API のヘルスチェック

結果は output フォルダに JSON で保存し、実行直後にターミナルへサマリーを出す。

## セットアップ

必要条件: Python 3.8 以上。SSH でログ収集・Docker 監視をする場合は鍵認証できること。Ping は管理者権限がなくても OS の `ping` にフォールバックする。

```bash
git clone [repository-url]
cd beaconbase
python -m venv venv
source venv/bin/activate  # Linux/Mac
# venv\Scripts\activate   # Windows
pip install -r requirements.txt
cp config_sample.yaml config.yaml
```

`config.yaml` を環境に合わせて編集する。分割したい場合は [config_sample_split_entry.yaml](config_sample_split_entry.yaml) と [config_sample.d/](config_sample.d/) をコピーする。

設定だけ確認する:

```bash
python monitor.py -c config.yaml --validate
```

## 使い方

```bash
# 設定にある監視をすべて実行
python monitor.py -c config.yaml

# Ping と Web だけ
python monitor.py -c config.yaml --only ping,web_health

# 詳細ログ
python monitor.py -c config.yaml -v

# cron 向け（警告以上のみ）
python monitor.py -c config.yaml -q
```

カテゴリ: `logs` / `ping` / `docker` / `web_health`

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
| 2 | 監視失敗（ERROR。Docker 等の NOT_FOUND も含む。ログファイル欠落は含めない） |
| 130 | 中断（Ctrl+C） |

## 結果ファイル

保存先は `storage.output_folder`。実行のたびに次を更新する。

- `check_summary.json`: 今回の全結果と件数
- `error_summary.json`: OK 以外だけ。今回問題が無ければファイル自体を削除する（前回の失敗が残らない）
- `monitoring_summary.json`: ping / docker / web_health の最新サマリー
- `log_summary.log`: ログ収集の追記サマリー（本文は末尾 N 行。`settings.log_summary_max_lines`）
- カテゴリ別フォルダ（`logs/` `ping/` `docker/` `web_health/`）: 日次 JSON

収集したログは `logs/<サーバー名>/<日時>_<ファイル名>` に保存し、上書きしない。

## 設定の要点

必須は `storage.output_folder` のみ。それ以外は書いたセクションだけ検証・実行する。

| セクション | 内容 |
|------------|------|
| `storage` | 結果の保存先。`~` はホームディレクトリに展開する |
| `settings` | リトライ・並列数・タイムアウトなど（省略可） |
| `default_ssh` | サーバー個別指定が無いときの SSH 既定値 |
| `log_collection` | ログ収集。`delete_after_collection` で収集後削除 |
| `ping_targets` | Ping 監視 |
| `docker_monitoring` | SSH 経由のコンテナ状態。`health_check_url` は Docker ホスト上で curl する |
| `web_health_checks` | 監視ホストからの HTTP チェック。`expected_status` で 200 以外も許可できる |

パスは絶対パスを推奨。SSH は鍵認証のみ。鍵のパーミッションは 600 を推奨。

分割設定（`includes_dir`）の仕様は [docs/configuration.md](docs/configuration.md)。アーキテクチャは [docs/architecture.md](docs/architecture.md)。サンプル全体は [config_sample.yaml](config_sample.yaml)。

### Docker ヘルス判定

`docker inspect` の Health を優先し、無ければ `docker ps` の Status 文字列から判定する。`health_check_url` を書いた場合は Docker ホスト上で curl し、失敗なら ERROR にする。

| ヘルスチェック状態 | BeaconBase の判定 |
|--------------------|-------------------|
| `healthy` | OK |
| `unhealthy` | ERROR |
| `starting` | WARNING |
| ヘルスチェック未設定かつ Up | OK |
| NOT_FOUND | NOT_FOUND |
| HTTP ヘルスチェック FAIL | ERROR |

## 制限事項

- 同時実行数の既定は 5（`settings.max_workers`）
- SSH 失敗などのリトライ回数の既定は 3、間隔 5 秒
- Ping タイムアウトの既定は 5 秒。Linux で ICMP が使えない場合は OS の `ping` を使う
- ログファイルサイズの上限は設けない（サマリーへ載せる行数だけ制限する）

## ライセンス

MIT
