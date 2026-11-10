#!/usr/bin/env bash
# 今の作業中のコードを Cloud Run (zombie-run) に公開する。
# 使い方: ./deploy.sh        （テスト → 公開 → 動作確認）
#         ./deploy.sh --skip-tests
# 環境変数・シークレット・公開設定は既存のサービスのものを引き継ぐ。
# 設定を変えたいときは docs/gcp-setup.md の 5. を参照。
set -euo pipefail
cd "$(dirname "$0")"

SERVICE=zombie-run
REGION=asia-northeast1

if [[ "${1:-}" != "--skip-tests" ]]; then
  echo "== テスト"
  .venv/bin/python -m pytest -q
fi

echo "== 公開（数分かかります）"
gcloud run deploy "$SERVICE" --source . --region "$REGION" --max-instances 3 --quiet

URL=$(gcloud run services describe "$SERVICE" --region "$REGION" --format='value(status.url)')

echo "== 動作確認"
health=$(curl -fsS "$URL/health" || true)
if [[ "$health" != *'"ok"'* ]]; then
  echo "NG: /health が ok を返しません: $health" >&2
  exit 1
fi
if curl -fsS "$URL/" | grep -q "開発モード"; then
  echo "NG: 「開発モード」の帯が出ています（本番の設定が足りない）" >&2
  exit 1
fi
echo "OK: $URL"
