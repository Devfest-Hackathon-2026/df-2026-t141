# デッドアワー

設定した時刻に合わせてゾンビが拠点（家）へ近づき、それまでに家を出ないとゲームオーバーになるWebアプリです。ゾンビは毎日同じ時刻に来ます。家を出ると追いかけてきます（ノーマルは1体が時速6km、ハードは3体が時速9km。毎回選べます）。シェルターまで逃げ切った距離と、道で拾う資材で拠点が豪華になり、捕まると振り出しに戻ります。毎日の時刻とは別に、拠点の「フリーラン」からいつでも走れます。外に出ている間は、傾けた3Dの地図の上を自分のアバターが歩き、ゾンビとシェルターの位置を確認できます。

## ローカルで動かす

```bash
python3 -m venv .venv
.venv/bin/python -m pip install -r requirements-dev.txt
.venv/bin/python -m uvicorn app:app --reload
```

`http://localhost:8000` を開き、「テスト隊員でホームを開く」を押します。Google Cloudの準備がなくても動きます。その場合、公園は開発用の偽物で、画面上部に帯が表示されます。データは再起動で消えます。

拠点の10段階の見た目は、ローカルでだけ `/dev/base/{user_id}?m=23000` のように逃げた距離を設定して確認できます（本番では使えません。デモ動画には使わないでください）。

`python3 -m venv` が失敗したら、`sudo apt install python3.12-venv` を実行してください。

## テスト

```bash
.venv/bin/python -m pytest
```

## Cloud Run で動かす

公開URL: https://zombie-run-xdd4x65s6a-an.a.run.app

更新は次のコマンドで公開できます（テスト → 公開 → 動作確認まで行います）。

```bash
./deploy.sh
```

## 資料

- [docs/api.md](docs/api.md): 画面とサーバーのAPIの形式
- [docs/gcp-setup.md](docs/gcp-setup.md): GCPの準備と公開手順
- [docs/decisions.md](docs/decisions.md): 設計の判断と理由

## 秘密情報

APIキーはコミットしません。ローカルでは `.env`（`.env.example` を参照）、本番では Secret Manager を使います。
