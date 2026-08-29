# BeaconBase

ローカルネットワーク向けの簡易監視。Docker コンテナとして常駐し、定期監視と WEB での状況確認・設定変更ができる。

## 概要

- サーバーログの収集
- Ping と TCP ポートの死活
- SSH 経由のディスク使用率
- Docker コンテナの状態
- Web / API のヘルスチェック
- 状態変化の通知（ファイル / Webhook / メール / コマンド）
- WEB UI での状況確認と設定編集

使わない機能のセクションは省略してよい。必須は `storage.output_folder` のみ。

## 起動（推奨: docker compose）

必要条件: Docker / Docker Compose。

```bash
git clone [repository-url]
cd beaconbase
mkdir -p data ssh
docker compose up -d --build
```

ブラウザで http://localhost:8080/ を開く。

- 状況: 直近の監視結果と「今すぐ監視」
- 設定: `config.yaml` と `includes_dir` 直下の YAML を編集

初回起動時、コンテナは `./data` にサンプル設定を展開する。SSH 鍵は `./ssh` に置き、設定の `key_path` を `/ssh/id_rsa` などにする。

停止:

```bash
docker compose down
```

## ローカルで常駐（docker なし）

```bash
python -m venv venv
source venv/bin/activate
pip install -r requirements.txt
cp config_sample_split_entry.yaml config.yaml
# または単一ファイル: cp config_sample.yaml config.yaml
python serve.py -c config.yaml
```

## 都度実行（CLI）

cron から1回だけ走らせる場合:

```bash
python monitor.py -c config.yaml
python monitor.py -c config.yaml --only ping,ports
python monitor.py -c config.yaml --validate
```

カテゴリ: `logs` / `ping` / `ports` / `disk` / `docker` / `web_health`

### 終了コード（CLI）

| コード | 意味 |
|--------|------|
| 0 | 正常（WARNING のみの場合も含む） |
| 1 | 設定エラーなど、監視を実行できなかった |
| 2 | 監視失敗（ERROR。Docker 等の NOT_FOUND も含む。ログファイル欠落は含めない） |
| 130 | 中断（Ctrl+C） |

## 結果ファイル

保存先は `storage.output_folder`（コンテナでは `/data/output` → ホストの `./data/output`）。

- WEB UI が最新結果を表示する
- `index.html`: ファイル単体でも見られるダッシュボード
- `latest.json`: 今回の全結果
- `runtime_state.json`: 連続失敗と障害開始時刻
- `alerts.log`: 障害・回復の履歴

## 設定の要点

| セクション | 内容 |
|------------|------|
| `storage` | 結果の保存先 |
| `settings` | リトライ・並列数・`check_interval`（常駐時の間隔秒）など |
| `default_ssh` | SSH 既定値 |
| `ping_targets` / `port_checks` / `disk_checks` | 死活・容量 |
| `log_collection` / `docker_monitoring` / `web_health_checks` | ログ・コンテナ・HTTP |
| `alerts` | 連続失敗後の通知 |

分割設定は [docs/configuration.md](docs/configuration.md)。常駐運用は [docs/operations.md](docs/operations.md)。

## 制限事項

- 同時実行数の既定は 5（`settings.max_workers`）
- 常駐間隔の既定は 300 秒（`settings.check_interval`）
- 障害判定の連続失敗回数の既定は 2（`alerts.fail_count`）

## ライセンス

MIT
