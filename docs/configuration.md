# 設定ファイル

BeaconBase は YAML で監視対象と保存先を定義する。エントリとなるメインファイル（既定は `config.yaml`）を `python monitor.py -c <path>` で指定する。

必須なのは `storage.output_folder` だけである。使わない監視セクションは省略してよい。

## 単一ファイル

すべてのセクションを1つの YAML に書く方法である。サンプルは [config_sample.yaml](../config_sample.yaml)。

主なセクション:

- `storage` — 結果の出力先（必須）
- `settings` — リトライ回数・並列数・タイムアウトなど（省略可）
- `default_ssh` — サーバー個別設定が無いときの SSH 既定値
- `log_collection` — ログ収集
- `ping_targets` — Ping 監視
- `docker_monitoring` — Docker コンテナ監視
- `web_health_checks` — Web / API ヘルスチェック

パスの `~` はホームディレクトリに展開する。

## 分割設定（`includes_dir`）

設定が大きくなったときは、メイン YAML のルートにディレクトリを1つ指定する。

```yaml
includes_dir: config.d

log_collection:
  # メイン側で追記・スカラー上書きするセクション（リストは断片と連結）
```

### 動作

1. `includes_dir` をメイン設定ファイルがあるディレクトリ基準で解決する
2. そのディレクトリ直下の `*.yaml` / `*.yml` をファイル名の辞書順（大文字小文字を区別しない）で読み、順にマージする
3. 続けてメイン YAML の本体（`includes_dir` 以外）をマージする
4. 返却する設定辞書に `includes_dir` キーは含めない

サブディレクトリは読まない。直下に置いた断片のみが対象である。断片ファイル側に `includes_dir` を書くとエラーになる。

### マージ規則

| 左右の型 | 結果 |
|----------|------|
| 両方 dict | キー単位で再帰マージ |
| 両方 list | 連結（例: 複数ファイルの `ping_targets`）。メインでリスト全体を置換する手段はない |
| それ以外 | 後から読んだ値を採用する（ただし右側が YAML の `null` の場合は左側を残す） |

### パスと安全制限

- 相対パスはメイン設定ファイルのディレクトリからの相対である
- 解決後のパスがメイン設定ディレクトリの外を指す場合は拒否する（`..` によるトラバーサル防止）
- `includes_dir` が存在しない、またはディレクトリ内に YAML がなく非YAMLファイルのみの場合はエラーである
- 空のディレクトリは許可し、メイン本体のみを採用する

### サンプル

- エントリ: [config_sample_split_entry.yaml](../config_sample_split_entry.yaml)
- 断片ディレクトリ: [config_sample.d/](../config_sample.d/)
  - `00-storage.yaml` / `01-default_ssh.yaml` / `05-settings.yaml`
  - `10-ping_targets.yaml` / `11-ping_external.yaml`（同名リストの連結例）
  - `20-log_collection.yaml` / `30-docker_monitoring.yaml` / `40-web_health_checks.yaml`

実行例:

```bash
python monitor.py -c config_sample_split_entry.yaml
python monitor.py -c config.yaml --validate
python monitor.py -c config.yaml --only ping,web_health
```

## セクション詳細

### settings（省略可）

| キー | 既定 | 内容 |
|------|------|------|
| `retry_count` | 3 | SSH 接続失敗などリトライ可能なエラーの試行回数 |
| `retry_delay` | 5 | リトライ間隔（秒） |
| `timeout` | 30 | SSH 上の docker / curl などの操作タイムアウト（秒） |
| `max_workers` | 5 | Ping / Web などカテゴリ内の並列数 |
| `ping_timeout` | 5 | Ping の待ち時間（秒） |
| `ssh_timeout` | 15 | SSH 接続タイムアウト（秒） |
| `log_summary_max_lines` | 80 | `log_summary.log` に載せる本文の最大行数。0 で制限なし |

### web_health_checks.targets

| キー | 必須 | 内容 |
|------|------|------|
| `name` | はい | 識別名 |
| `url` | はい | チェック対象 URL |
| `timeout` | いいえ | リクエストタイムアウト秒（既定 30） |
| `verify_ssl` | いいえ | SSL 証明書を検証するか（既定 true） |
| `expected_status` | いいえ | 正常とみなす HTTP ステータス。整数またはリスト（既定 200） |

### docker_monitoring の health_check_url

指定した場合、監視ホストからではなく Docker ホスト上で `curl` する。`localhost` はその Docker ホスト自身を指す。`health_check_timeout` で待ち時間を変えられる（既定 5 秒）。`type: web` でも URL は必須ではない（コンテナの Healthcheck だけで判定できる）。
