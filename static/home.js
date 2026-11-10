/* 拠点。家の座標は扱わないので、地図と現在地は使わない（外のマップは⑥）。 */
(function () {
  document.querySelectorAll("time.local-time").forEach(function (el) {
    el.textContent = new Date(el.dateTime).toLocaleString("ja-JP", {
      month: "numeric", day: "numeric", hour: "2-digit", minute: "2-digit",
    });
  });

  var data = JSON.parse(document.getElementById("base-data").textContent);
  // トップページの「続きから」のため、このブラウザで開いた拠点を覚えておく（welcome.js）
  try { localStorage.setItem("dead-hour-user", data.user_id); } catch (e) { /* 覚えられなくても拠点は使える */ }
  var POLL_INTERVAL_MS = 60000;
  var OPEN_ESCAPE_BEFORE_MS = 10 * 60 * 1000;

  var label = document.getElementById("zombie-km");
  var gauge = document.getElementById("zombie-gauge");
  var state = { km: data.km, at: Date.now(), speedPerMs: data.speed / 3600000 };

  /* 3Dの拠点（base3d.js）が読み込まれたら、ゾンビの距離を渡す */
  function currentKm() {
    return Math.max(0, state.km - (Date.now() - state.at) * state.speedPerMs);
  }
  window.ZombieBase = { data: data, currentKm: currentKm };

  // 出発10分前に1回だけ⑤へ移動する。時刻が来たらサーバーの判定を反映する
  function openedEscape() {
    try {
      if (sessionStorage.getItem("opened-escape") === data.escape_url) { return true; }
      sessionStorage.setItem("opened-escape", data.escape_url);
    } catch (e) { /* 保存できなくても移動はする */ }
    return false;
  }

  function checkArrival() {
    var now = Date.now();
    var openAt = data.depart_at ? new Date(data.depart_at).getTime() - OPEN_ESCAPE_BEFORE_MS : null;
    var decidedAt = new Date(data.arrival_at).getTime();
    if (openAt !== null && now >= openAt && !openedEscape()) {
      location.href = data.escape_url;
    } else if (!data.chasing && now >= decidedAt) {
      location.reload();
    }
  }

  function apply(user) {
    if (user.capture_count !== data.capture_count || user.chasing !== data.chasing) { location.reload(); return; }
    state.km = user.zombie_km;
    state.at = Date.now();
    state.speedPerMs = user.zombie_speed_km_per_hour / 3600000;
  }

  // 距離の表示は毎秒動かす。正しい値はサーバーの計算
  setInterval(function () {
    var km = currentKm();
    label.textContent = km.toFixed(5);
    gauge.style.width = (km / data.max_km) * 100 + "%";
    checkArrival();
  }, 1000);

  // 毎日の時刻の変更。今日まだ来ていない分はそのままで、明日から新しい時刻になる
  var timeForm = document.getElementById("daily-time-form");
  var timeStatus = document.getElementById("daily-time-status");
  timeForm.addEventListener("submit", function (event) {
    event.preventDefault();
    var value = document.getElementById("daily-time").value;
    fetch("/api/users/" + data.user_id + "/daily-time", {
      method: "PUT",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ daily_notify_time: value }),
    }).then(function (res) {
      if (!res.ok) { throw new Error("request"); }
      location.reload();
    }).catch(function () {
      timeStatus.textContent = "変更できませんでした。もう一度試してね";
      timeStatus.hidden = false;
    });
  });

  // フリーズ。使うと次の毎日の時刻にゾンビが来ない
  var freezeButton = document.getElementById("freeze-use");
  if (freezeButton) {
    var freezeStatus = document.getElementById("freeze-status");
    freezeButton.addEventListener("click", function () {
      if (!window.confirm("フリーズを1つ使って、次の毎日の時刻を休みますか？")) { return; }
      freezeButton.disabled = true;
      fetch("/api/users/" + data.user_id + "/freeze", { method: "POST" }).then(function (res) {
        return res.json().then(function (body) {
          if (!res.ok) { throw new Error((body.error && body.error.message) || "使えませんでした"); }
          location.reload();
        });
      }).catch(function (error) {
        freezeButton.disabled = false;
        freezeStatus.textContent = error.message;
        freezeStatus.hidden = false;
      });
    });
  }

  // 別の端末へ移る引き継ぎコード。発行し直すと、前のコードは使えなくなる
  var transferButton = document.getElementById("transfer-issue");
  var transferStatus = document.getElementById("transfer-status");
  transferButton.addEventListener("click", function () {
    if (!window.confirm("コードを発行しますか？前に発行したコードは使えなくなります。")) { return; }
    transferButton.disabled = true;
    transferStatus.hidden = true;
    fetch("/api/users/" + data.user_id + "/transfer-code", { method: "POST" }).then(function (res) {
      if (!res.ok) { throw new Error("request"); }
      return res.json();
    }).then(function (body) {
      document.getElementById("transfer-code").textContent = body.code;
      document.getElementById("transfer-result").hidden = false;
    }).catch(function () {
      transferStatus.textContent = "発行できませんでした。もう一度試してね";
      transferStatus.hidden = false;
    }).then(function () {
      transferButton.disabled = false;
    });
  });

  // アバターの色は、拠点の右上のボタンで開く。色を見ながら選べるよう、3Dの直下に出す
  var avatarOpen = document.getElementById("avatar-open");
  var avatarPanel = document.getElementById("avatar-card");
  avatarOpen.addEventListener("click", function () {
    avatarPanel.hidden = !avatarPanel.hidden;
    avatarOpen.setAttribute("aria-expanded", String(!avatarPanel.hidden));
  });

  // アバターの色。押すとすぐ保存し、拠点の3Dのアバターも変える
  var avatarStatus = document.getElementById("avatar-status");
  var avatar = { outfit: null, skin: null };
  document.querySelectorAll(".swatch").forEach(function (swatch) {
    if (swatch.getAttribute("aria-checked") === "true") { avatar[swatch.dataset.kind] = swatch.dataset.value; }
    swatch.addEventListener("click", function () {
      var kind = swatch.dataset.kind;
      var previous = avatar[kind];
      avatar[kind] = swatch.dataset.value;
      fetch("/api/users/" + data.user_id + "/avatar", {
        method: "PUT",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(avatar),
      }).then(function (res) {
        if (!res.ok) { throw new Error("request"); }
        document.querySelectorAll('.swatch[data-kind="' + kind + '"]').forEach(function (other) {
          other.setAttribute("aria-checked", other === swatch ? "true" : "false");
        });
        data.avatar[kind] = parseInt(swatch.dataset.color.slice(1), 16);
        document.dispatchEvent(new CustomEvent("zombie-avatar-change", { detail: data.avatar }));
        avatarStatus.hidden = true;
      }).catch(function () {
        avatar[kind] = previous;
        avatarStatus.textContent = "変更できませんでした。もう一度試してね";
        avatarStatus.hidden = false;
      });
    });
  });

  setInterval(function () {
    fetch("/api/users/" + data.user_id).then(function (res) {
      if (!res.ok) { throw new Error("request"); }
      return res.json();
    }).then(apply).catch(function () {});
  }, POLL_INTERVAL_MS);
})();
