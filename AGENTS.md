## Learned User Preferences

- 日本語でやり取りし、コードコメントとドキュメントも日本語で書く
- 設定分割はファイルリスト式 `includes` ではなく、ディレクトリ指定の `includes_dir` を使う
- リファクタは設定ロード／例外のモジュール分離程度の中規模を優先し、MonitoringSystem の機能別分割は明示依頼があるまで行わない
- 本番起動は docker-compose による常駐（定期監視 + WEB）を推奨する

## Learned Workspace Facts

- 分割設定はメイン YAML ルートの `includes_dir`（文字列・ディレクトリ1つ）。直下の `*.yaml` / `*.yml` をファイル名ソートで deep-merge し、続けてメイン本体をマージする（dict は再帰、list は連結）
- かつてのファイルリスト式 `includes` は廃止済み。現行仕様は `includes_dir` のみ
- `exceptions.py` に `MonitoringError` / `RetryableError`、`config_loader.py` に `load_merged_yaml_config`。`beaconbase` はこれらを再エクスポートする
- 断片 YAML に `includes_dir` を書いてはならない。`includes_dir` の解決パスはメイン設定ファイルのディレクトリ配下に限定する（サブディレクトリは読まない）
- 常駐は `serve.py`（`scheduler.py` + `webapp/`）。設定編集は `config_manager.py`。docker compose は `./data` と `./ssh` をマウントする
- `settings.check_interval`（既定 300 秒）が常駐時の監視間隔。CLI 都度実行は `monitor.py`
- 詳細ドキュメントは `docs/configuration.md` と `docs/architecture.md` と `docs/operations.md`（索引は `docs/README.md`）
- Windows では `py -m pytest` で実行する。`test_monitoring_system.py` / `test_ops.py` / `test_web_daemon.py` がカバーする
