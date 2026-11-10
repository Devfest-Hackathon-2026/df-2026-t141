/* 状況に応じた通知の登録。FCM の register() と端末ID（FID）を使う。 */
import { initializeApp } from "https://www.gstatic.com/firebasejs/13.1.0/firebase-app.js";
import { getMessaging, onRegistered, register } from "https://www.gstatic.com/firebasejs/13.1.0/firebase-messaging.js";

const button = document.getElementById("push-enable");
const status = document.getElementById("push-status");
const config = JSON.parse(document.getElementById("push-config").textContent);

function show(message) {
  status.textContent = message;
  status.hidden = false;
}

async function enable() {
  if (!("serviceWorker" in navigator) || !("PushManager" in window)) {
    show("このブラウザでは通知を使えません。iPhoneの場合は、ホーム画面に追加してから開いてください。");
    return;
  }
  button.disabled = true;
  try {
    const permission = await Notification.requestPermission();
    if (permission !== "granted") {
      show("通知が許可されませんでした。ブラウザの設定から許可できます。");
      button.disabled = false;
      return;
    }
    const messaging = getMessaging(initializeApp(config.firebase));
    onRegistered(messaging, async (fid) => {
      const response = await fetch(`/api/users/${config.user_id}/push`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ fid }),
      });
      if (response.ok) {
        button.hidden = true;
        show("通知をオンにしました。ゾンビの接近と、脱出できるようになる時刻をお知らせします（22時〜7時は、脱出の時刻の知らせだけが届きます）。");
      } else {
        show("通知の登録に失敗しました。もう一度お試しください。");
        button.disabled = false;
      }
    });
    await register(messaging, { vapidKey: config.vapid_key });
  } catch {
    show("通知の登録に失敗しました。もう一度お試しください。");
    button.disabled = false;
  }
}

button.addEventListener("click", enable);
