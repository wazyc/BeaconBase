# 設定ファイル

BeaconBase は YAML で監視対象と保存先を定義します。エントリとなるメインファイル（既定は `config.yaml`）を `python monitor.py -c <path>` で指定します。

## 単一ファイル

すべてのセクションを1つの YAML に書く方法です。サンプルは [config_sample.yaml](../config_sample.yaml) です。

主なセクション:

- `storage` — 結果の出力先
- `default_ssh` — サーバー個別設定が無いときの SSH 既定値
- `log_collection` — ログ収集
- `ping_targets` — Ping 監視
- `docker_monitoring` — Docker コンテナ監視
- `web_health_checks` — Web / API ヘルスチェック

## 分割設定（`includes_dir`）

設定が大きくなったときは、メイン YAML のルートにディレクトリを1つ指定します。

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

サブディレクトリは読みません。直下に置いた断片のみが対象です。断片ファイル側に `includes_dir` を書くとエラーになります。

### マージ規則

| 左右の型 | 結果 |
|----------|------|
| 両方 dict | キー単位で再帰マージ |
| 両方 list | 連結（例: 複数ファイルの `ping_targets`）。メインでリスト全体を置換する手段はない |
| それ以外 | 後から読んだ値を採用（ただし右側が YAML の `null` の場合は左側を残す） |

### パスと安全制限

- 相対パスはメイン設定ファイルのディレクトリからの相対です
- 解決後のパスがメイン設定ディレクトリの外を指す場合は拒否されます（`..` によるトラバーサル防止）
- `includes_dir` が存在しない、またはディレクトリ内に YAML がなく非YAMLファイルのみの場合はエラーです
- 空のディレクトリは許可され、メイン本体のみが採用されます

### サンプル

- エントリ: [config_sample_split_entry.yaml](../config_sample_split_entry.yaml)
- 断片ディレクトリ: [config_sample.d/](../config_sample.d/)
  - `00-storage.yaml` / `01-default_ssh.yaml`
  - `10-ping_targets.yaml` / `11-ping_external.yaml`（同名リストの連結例）
  - `20-log_collection.yaml` / `30-docker_monitoring.yaml` / `40-web_health_checks.yaml`

実行例:

```bash
python monitor.py -c config_sample_split_entry.yaml
```

詳細な各セクションのキー説明は [README.md の設定ファイル節](../README.md#設定ファイル) も参照してください。
