# BeaconBase

インフラ、サーバー、ネットワーク機器、Dockerコンテナ、WEBの簡易な監視

Simple monitoring for infrastructure, servers, network devices, Docker containers, and web services

## 概要 / Overview

このシステムは以下の監視機能を提供します：

This system provides the following monitoring features:

- サーバーログの収集 / Server log collection
- ネットワーク機器のPing監視 / Ping monitoring for network devices
- Dockerコンテナの状態監視 / Docker container status monitoring
- Web APIの健全性チェック / Web API health checks
- Webページのヘルスチェック / Web page health checks

すべての監視結果は指定されたoutputフォルダにJSON形式で保存され、サマリーレポートが自動生成されます。

All monitoring results are saved in JSON format to the specified output folder, and summary reports are automatically generated.

## 主な機能

### 1. ログ収集
- 複数サーバーからの並行ログ収集
- SSH経由でのファイル転送
- 収集後の自動クリーンアップ
- ログサマリーの生成

### 2. Ping監視
- 複数ターゲットの同時監視
- タイムアウト設定による信頼性の確保
- 結果の自動集計とサマリー作成

### 3. Dockerコンテナ監視
- コンテナの状態確認
- Webコンテナのヘルスチェック
- 詳細な状態情報の収集
- 監視結果の自動集計

### 4. Webページヘルスチェック
- 指定されたURLのヘルスチェック
- タイムアウトとSSL証明書の検証

## セットアップ

### 必要条件
- Python 3.8以上
- SSH接続が可能な環境
- Docker（コンテナ監視用）

### インストール手順

1. リポジトリのクローン
```bash
git clone [repository-url]
cd beaconbase
```

2. 仮想環境の作成と有効化
```bash
python -m venv venv
source venv/bin/activate  # Linux/Mac
venv\\Scripts\\activate   # Windows
```

3. 依存パッケージのインストール
```bash
pip install -r requirements.txt
```

4. 設定ファイルの準備
```bash
cp config_sample.yaml config.yaml
```

5. config.yamlの編集
- 監視対象サーバーの情報を設定
- SSH接続情報の設定
- 保存先フォルダの設定

## 設定ファイル

### 概要
`config.yaml`は監視システムの動作を制御する中心的な設定ファイルです。以下のセクションで構成されています：

### 1. ストレージ設定 (storage)
```yaml
storage:
  output_folder: "path/to/storage"  # 監視結果の保存先ディレクトリ
```
- `output_folder`: 監視結果やログファイルの保存先ディレクトリパス（絶対パスを推奨）

### 2. ログ収集設定 (log_collection)
```yaml
log_collection:
  delete_after_collection: true    # ログ収集後にサーバーからログを削除するかどうか
  servers:
    - name: "server1"              # サーバーの識別名
      host: "192.168.1.1"          # サーバーのIPアドレスまたはホスト名
      ssh_username: "admin"        # SSH接続ユーザー名（オプション）
      ssh_key_path: "~/.ssh/id_rsa"  # SSH秘密鍵のパス（オプション）
      ssh_port: 22                 # SSHポート番号（オプション、デフォルト: 22）
      log_paths:                   # 収集対象のログファイルパス（複数指定可）
        - "/var/log/app.log"
        - "/var/log/error.log"
```

### 3. Ping監視設定 (ping_targets)
```yaml
ping_targets:
  - name: "router1"               # 監視対象の識別名
    host: "192.168.1.254"        # 監視対象のIPアドレスまたはホスト名
  - name: "switch1"
    host: "192.168.1.253"
```

### 4. Dockerコンテナ監視設定 (docker_monitoring)
```yaml
docker_monitoring:
  servers:
    - host: "192.168.1.1"         # Dockerホストのアドレス
      ssh_username: "docker_user"  # SSH接続ユーザー名（オプション）
      ssh_key_path: "~/.ssh/docker_key"  # SSH秘密鍵のパス（オプション）
      containers:
        - name: "web-app"         # コンテナ名
          type: "web"             # コンテナタイプ（web/application/database）
          health_check_url: "http://localhost:8080/health"  # ヘルスチェックURL（webタイプの場合必須）
        - name: "redis"
          type: "database"        # Webタイプ以外の場合はhealth_check_urlは不要
```

typeは以下のいずれかを指定します。
- web
- application
- database

### 5. デフォルトSSH設定 (default_ssh)
```yaml
default_ssh:
  username: "default_user"        # デフォルトのSSHユーザー名
  key_path: "~/.ssh/id_rsa"      # デフォルトのSSH秘密鍵パス
  port: 22                        # デフォルトのSSHポート
```

### 6. Webページヘルスチェック設定 (web_health_checks)
```yaml
web_health_checks:
  targets:
    - name: "main-website"        # 監視対象の識別名
      url: "https://example.com"  # チェック対象のURL
      timeout: 30                 # タイムアウト秒数（オプション、デフォルト: 30）
      verify_ssl: true           # SSL証明書の検証（オプション、デフォルト: true）
    - name: "api-endpoint"
      url: "https://api.example.com/health"
      timeout: 10
      verify_ssl: false          # 自己署名証明書の場合はfalseに設定
```

### 設定ファイルの例
以下は完全な設定ファイルの例です。

```yaml
storage:
  output_folder: "/path/to/monitoring/results"

default_ssh:
  username: "monitor_user"
  key_path: "~/.ssh/monitor_key"
  port: 22

log_collection:
  delete_after_collection: true
  servers:
    - name: "web-server"
      host: "192.168.1.10"
      ssh_port: 22123
      log_paths:
        - "/var/log/nginx/access.log"
        - "/var/log/nginx/error.log"
    - name: "app-server"
      host: "192.168.1.11"
      ssh_username: "app_user"     # デフォルト設定を上書き
      ssh_key_path: "~/.ssh/app_key"
      log_paths:
        - "/var/log/application/app.log"

ping_targets:
  - name: "main-router"
    host: "192.168.1.1"
  - name: "backup-router"
    host: "192.168.1.2"
  - name: "core-switch"
    host: "192.168.1.3"

docker_monitoring:
  servers:
    - host: "192.168.1.20"
      containers:
        - name: "nginx-proxy"
          type: "web"
          health_check_url: "http://localhost:80/health"
        - name: "api-service"
          type: "web"
          health_check_url: "http://localhost:8080/api/health"
        - name: "redis-cache"
          type: "database"
    - host: "192.168.1.21"
      ssh_username: "docker_admin"
      ssh_key_path: "C:/Users/user/.ssh/work/sngfa-a/id_rsa_sngfa-a"
      ssh_port: 22222
      containers:
        - name: "mongodb"
          type: "database"

web_health_checks:
  targets:
    - name: "corporate-website"
      url: "https://corporate.example.com"
      timeout: 30
      verify_ssl: true
    - name: "customer-portal"
      url: "https://portal.example.com/health"
      timeout: 10
      verify_ssl: true
    - name: "internal-api"
      url: "https://api.internal.example.com"
      timeout: 5
      verify_ssl: false  # 内部APIは自己署名証明書を使用
```

### 注意事項
- すべてのパスは絶対パスを推奨
- 機密情報（パスワードなど）は直接設定ファイルに記述せず、環境変数などを使用
- SSH鍵のパーミッションは適切に設定（600）
- WebコンテナのヘルスチェックURLは、コンテナ内部からアクセス可能なURLを指定
- ホスト名の代わりにIPアドレスを使用する場合は、固定IPを推奨

## 使用方法

### ターミナルから実行

監視を一度だけ実行する場合は、以下のコマンドを使用します：

```bash
python monitor.py -c config.yaml
```

詳細なログ出力を有効にする場合：

```bash
python monitor.py -c config.yaml -v
```

### Pythonコードから実行

```python
from beaconbase import MonitoringSystem

with MonitoringSystem("config.yaml") as monitor:
    monitor.run_all_checks()
```

### 結果の確認

監視結果は指定されたoutputフォルダに以下のファイルが生成されます：

- `check_summary.json`: 全ての監視結果（ping、docker、web_health）
- `error_summary.json`: エラーのみの監視結果（正常ではないチェック結果のみ）
- `log_summary.log`: ログ収集のサマリー
- カテゴリ別のフォルダ（`logs/`, `ping/`, `docker/`, `web_health/`）: 詳細な監視結果

#### error_summary.jsonについて

`error_summary.json`は、監視結果の中でエラーまたは警告が発生したものだけを抽出したファイルです。
このファイルは以下の特徴があります：

- エラーが一つも発生しなかった場合、ファイルは生成されません
- 監視対象の状態が正常（OK）でない場合のみ記録されます
- 迅速な問題の特定と対応に役立ちます
- タイムスタンプ、対象名、エラー詳細が含まれます

## エラー処理

- 接続エラーは自動的にリトライ
- すべてのエラーはログに記録
- 重要なエラーは監視結果ファイルに記録

## セキュリティ

- SSH鍵認証の使用
- 機密情報の安全な管理
- SSL証明書の検証オプション

## 制限事項

- 同時監視数は設定により制限（デフォルト: 5）
- ログファイルサイズの制限なし
- リトライ回数: 3回
- タイムアウト: 

## ライセンス

MIT
