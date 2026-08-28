# 運用

ローカルネットワークで常時監視するための手順である。死活は Ping と TCP ポート、容量は SSH のディスク、アプリは Web / Docker を組み合わせる。

## まず動かす

```bash
python monitor.py -c config.yaml --validate
python monitor.py -c config.yaml --only ping,ports
```

問題が無ければ定期実行とダッシュボードを同時に出す。

```bash
python monitor.py -c config.yaml --interval 60 --serve 8088
```

ブラウザで `http://<このマシンのLAN IP>:8088/` を開く。`index.html` は結果フォルダにも書かれるので、HTTP を出さずにファイルを直接開いてもよい。

定期実行ではログ収集は走らない（SSH でファイルを毎回取るのは重いため）。必要なら `settings.interval_include_logs: true` か `--only logs` を使う。

## systemd（Linux）

1. リポジトリを `/opt/beaconbase` などに置く
2. `contrib/beaconbase.service` をコピーし、パスを直す
3. `sudo systemctl enable --now beaconbase`

```bash
sudo cp contrib/beaconbase.service /etc/systemd/system/beaconbase.service
sudo systemctl daemon-reload
sudo systemctl enable --now beaconbase
sudo journalctl -u beaconbase -f
```

## Windows タスク スケジューラ

管理者 PowerShell 例（5 分ごとワンショット。常駐させるなら `--interval` のスタートアップタスクでもよい）:

```powershell
schtasks /Create /SC MINUTE /MO 5 /TN BeaconBase /TR "C:\beaconbase\venv\Scripts\python.exe C:\beaconbase\monitor.py -c C:\beaconbase\config.yaml -q"
```

## 通知

`alerts.log` は常に残る。外部へ出す場合:

- `alerts.webhook.url` … Slack / Discord / 汎用 JSON POST。`format: auto` で URL から判別する
- `alerts.command` … 通知文を標準入力に渡して実行する
- `alerts.email` … SMTP。パスワードは `BEACONBASE_SMTP_PASSWORD` を推奨

`fail_count`（既定 2）で連続失敗してから障害とみなす。瞬断の誤報を減らす。回復したら回復通知を出す。`remind_seconds` で継続中の再通知間隔を決める。

## 結果ファイル

| ファイル | 内容 |
|----------|------|
| `index.html` | ダッシュボード |
| `latest.json` | 今回の全結果 |
| `runtime_state.json` | 連続失敗・障害開始時刻 |
| `alerts.log` | 障害 / 回復の履歴 |
| `check_summary.json` | 今回のサマリー |
| `error_summary.json` | 今回の異常のみ（無ければ削除） |

日次 JSON は `settings.retain_days`（既定 14）より古ければ削除する。

## 設定のコツ

- 使わない監視セクションは書かない
- `group` を付けるとダッシュボードで役割が分かる
- Ping だけでなく、NAS なら 445、ルータなら 53 などポートも見る
- ディスクは SSH 鍵が届くサーバだけでよい
- `--bind 127.0.0.1` にすればダッシュボードをそのマシンだけに閉じる
