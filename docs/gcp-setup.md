# GCPの準備と公開手順

クレジットカードの登録や支払いに関わるため、本人が実行します。`PROJECT_ID` は自分のものに置き換えてください。

## 1. 準備

1. `gcloud` をインストールして、`gcloud auth login` を実行する
2. プロジェクトを作成し、運営の案内に沿ってクレジットを有効化する
3. コンソールの「お支払い → 予算とアラート」で予算アラートを設定する（$10など。通知だけで自動では止まりません）

```bash
gcloud config set project PROJECT_ID
gcloud services enable run.googleapis.com cloudbuild.googleapis.com artifactregistry.googleapis.com \
  firestore.googleapis.com \
  places.googleapis.com routes.googleapis.com secretmanager.googleapis.com maps-backend.googleapis.com \
  cloudscheduler.googleapis.com
```

## 2. データの置き場所

```bash
gcloud firestore databases create --location=asia-northeast1
```

## 3. Places APIキー

コンソールの「APIとサービス → 認証情報」でキーを作成し、**APIの制限で Places API (New) だけを許可**します。キーはチャットやコードに貼らないでください。

APIの制限の一覧には、**有効にしたAPIしか出ません**。Places API (New) が見つからないときは、1. の `gcloud services enable` が済んでいるか、画面上部で選んでいるプロジェクトが正しいかを確認してください。名前の似た「Places API」（旧版）ではなく「Places API (New)」を選びます。

```bash
printf '%s' 'ここにキー' | gcloud secrets create PLACES_API_KEY --data-file=-
```

上のコマンドはシェルの履歴にキーが残ることがあるので、実行後に `history -d` で消すか、コンソールから登録してください。

逃走中の地図の徒歩ルートには Routes API を使います。このキーの「APIの制限」に **Routes API** も追加してください。別のキーにしたいときは、Secret Manager に入れて環境変数 `ROUTES_API_KEY` で渡します。

## 3-2. Maps JavaScript APIキーとマップID

このキーはブラウザから見えるので、Places APIのキーとは**別に**作ります。

1. 「認証情報」でキーを作成し、APIの制限で **Maps JavaScript API** だけを許可する
2. 「マップ管理」でマップIDを作成する。種類は JavaScript、**ベクター**を選び、**傾き（Tilt）と回転（Rotation）にチェックを入れる**。逃走中の地図は地図を傾けて回すので、これがないと平らな地図のままになる（`DEMO_MAP_ID` では傾かないことがある）
3. 公開後にURLが決まったら、キーのアプリケーション制限を「ウェブサイト」にして `https://公開URLのホスト` を登録する

## 3-3. Firebase（状況に応じた通知を使う場合）

設定しなければ通知のボタンは表示されず、通知なしで動きます。通知は次の流れで届きます。

1. 利用者が拠点の画面で「接近したら通知を受け取る」を押すと、ブラウザが Firebase に登録され、その端末のID（FID）がサーバーに保存される
2. Cloud Scheduler が5分ごとに `/internal/notify` を呼ぶ。合言葉（`NOTIFY_SECRET`）が合わなければ404を返す
3. サーバーがゾンビとの距離を確かめ、段階を超えた人にだけ Firebase Cloud Messaging で通知を送る

準備は次のとおりです。

1. [Firebaseコンソール](https://console.firebase.google.com/)で「プロジェクトを追加」を押し、このGCPプロジェクトを選んでFirebaseを追加する（追加済みならとばす）
2. 「プロジェクトの設定 → 全般 → マイアプリ」でウェブアプリ（`</>`）を登録し、表示された `apiKey`, `appId`, `messagingSenderId` を控える。Firebase Hosting の設定は要らない
3. 「プロジェクトの設定 → Cloud Messaging → ウェブ構成 → ウェブプッシュ証明書」で「鍵ペアを生成」を押し、表示された鍵（VAPIDキー。`B` で始まる長い文字列）を控える
4. 定期実行の合言葉を Secret Manager に作る（値は画面に出さない）

```bash
openssl rand -hex 24 | gcloud secrets create NOTIFY_SECRET --data-file=-
```

`apiKey` は、Firebase が自動で作る「Browser key (auto created by Firebase)」です。`appId`, `messagingSenderId`, VAPIDキーと同じく、ブラウザへ渡す前提の値で、秘密ではありません。このキーの「APIの制限」から Firebase Installations API と FCM Registration API を外さないでください。外すと通知の登録に失敗します。

設定した値の渡し方は 5-1 を参照してください。

## 4. 権限

```bash
NUMBER=$(gcloud projects describe PROJECT_ID --format='value(projectNumber)')
SA="$NUMBER-compute@developer.gserviceaccount.com"
for role in roles/datastore.user roles/secretmanager.secretAccessor roles/firebasecloudmessaging.admin; do
  gcloud projects add-iam-policy-binding PROJECT_ID --member="serviceAccount:$SA" --role="$role"
done
```

## 5. 公開

```bash
gcloud run deploy zombie-run --source . --region asia-northeast1 --allow-unauthenticated \
  --max-instances 3 \
  --set-env-vars GOOGLE_CLOUD_PROJECT=PROJECT_ID,USE_FIRESTORE=true,MAPS_BROWSER_KEY=MAPSのキー,MAPS_MAP_ID=マップID,FIREBASE_API_KEY=値,FIREBASE_APP_ID=値,FIREBASE_SENDER_ID=値,FIREBASE_VAPID_KEY=値 \
  --set-secrets PLACES_API_KEY=PLACES_API_KEY:latest,NOTIFY_SECRET=NOTIFY_SECRET:latest
```

2回目以降の更新は、設定を引き継いだまま `./deploy.sh` で公開できます（テスト → 公開 → `/health` と「開発モード」の帯の確認まで行います）。

## 5-1. 通知を有効にする（公開後に追加する場合）

3-3 で控えた値を、動いているサービスに追加します。コードは作り直さず、設定だけを変えた新しいリビジョンが数十秒で公開されます。

```bash
gcloud run services update zombie-run --region asia-northeast1 \
  --update-env-vars FIREBASE_API_KEY=値,FIREBASE_APP_ID=値,FIREBASE_SENDER_ID=値,FIREBASE_VAPID_KEY=値,PUBLIC_BASE_URL=https://公開URL \
  --update-secrets NOTIFY_SECRET=NOTIFY_SECRET:latest
```

必ず `--update-env-vars` を使います。`--set-env-vars` にすると、今ある環境変数（Maps のキーなど）がすべて消えます。

次に、5分ごとの定期実行を登録します。

```bash
gcloud scheduler jobs create http zombie-notify --location=asia-northeast1 \
  --schedule='*/5 * * * *' --http-method=POST --uri='https://公開URL/internal/notify' \
  --headers="X-Notify-Secret=$(gcloud secrets versions access latest --secret=NOTIFY_SECRET)"
```

脱出が開く10分前の通知があるので、5分おきに実行します。15分おきで登録済みなら、次のコマンドで変更してください。

```bash
gcloud scheduler jobs update http zombie-notify --location=asia-northeast1 --schedule='*/5 * * * *'
```

最後に、合言葉が通るかを確かめます。`{"sent": 0, "failed": 0}` のように返れば準備ができています。404なら、環境変数か合言葉が足りません。

```bash
curl -X POST -H "X-Notify-Secret: $(gcloud secrets versions access latest --secret=NOTIFY_SECRET)" https://公開URL/internal/notify
```

## 5-2. 外部APIの1日の上限（必ず設定する）

アプリにも回数の制限がありますが、インスタンスごとに数えるので完全ではありません。予算アラートは通知だけで止まらないので、料金を確実に止めるのはこの設定です。課金に関わるため、本人が実行します。

1. コンソールの「APIとサービス → 有効なAPIとサービス」で対象のAPIを開き、「割り当てとシステムの上限」を開く
2. 「1日あたり」の項目の上限を編集する。その項目がなければ、「1分あたり」の項目を下げる

| API | 上限の例 |
|---|---|
| Places API (New) の Nearby Search | 500回/日 |
| Routes API の Compute Routes | 500回/日 |
| Maps JavaScript API の読み込み | 2,000回/日 |

公園の多い地域では、1回の表示で Nearby Search を2回呼びます。金額は Google の料金表で確認し、残りのクレジットに合わせて決めてください。上限を超えると、その日の検索はエラーになり、画面に「公園を探せませんでした」と出ます。審査やデモの前に、上限に余裕があるかを確認してください。

## 6. 確認

- 表示されたURLの `/health` が `{"status":"ok"}` を返す
- 画面に「開発モード」の帯が出ない
- 公園検索と保存を1回ずつ試す
- 逃走中の地図で、地図が傾いて自分とゾンビが出る（「簡易表示」と出たらキーの制限、平らなままならマップIDの傾きの設定を確認する）
- 拠点の画面で3Dの拠点が表示される
- 通知が届くか試す（5-1 を済ませた場合）。下の「通知の試し方」を参照

### 通知の試し方

すぐに確かめるには、PC の Chrome で試すのがいちばん早いです。スマホで遊んでいる隊員に影響しません。

1. PC の Chrome で公開URLを開き、毎日の時刻を**今から25分後**にして新しく隊員登録する。ゾンビは約2.5km先に出る
2. 拠点の画面の「接近したら通知を受け取る」を押し、通知を許可する
3. 5分待つか、`gcloud scheduler jobs run zombie-notify --location=asia-northeast1` ですぐ実行する
4. 「ゾンビが近づいている（距離が3kmを切った）」が届けば成功。時刻の10分前には「あと10分でゾンビが来る」も届く

22時から7時は距離の通知を送らないので、昼間に試してください。届かないときは、`gcloud run services logs read zombie-run --region asia-northeast1 --limit 50` で `notify failed` が出ていないかを見ます。

スマホでは次のように有効にします。

- Android（Chrome）: 拠点の画面で「接近したら通知を受け取る」を押し、許可する
- iPhone（iOS 16.4以降）: Safari では通知を受け取れない。ホーム画面に追加したアプリからだけ受け取れる
  1. Safari で拠点を開き、一番下の「別の端末で続ける」で引き継ぎコードを発行して控える
  2. 共有ボタンから「ホーム画面に追加」を選び、ホーム画面のアイコンから開く
  3. トップに戻されたら、「引き継ぎコードをお持ちの方」からコードを入れる（ホーム画面のアプリは、Safari と別の端末として扱われることがある）
  4. 拠点の画面で「接近したら通知を受け取る」を押し、許可する

11/14まで動かし続けるので、残額を確認してください。
