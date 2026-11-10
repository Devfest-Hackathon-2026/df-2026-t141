(function () {
  var form = document.getElementById("setup-form");
  var nickname = document.getElementById("nickname");
  var notifyTime = document.getElementById("notify-time");
  var locate = document.getElementById("locate");
  var homeStatus = document.getElementById("home-status");
  var submit = document.getElementById("submit");
  var error = document.getElementById("error");
  var home = null;

  // 全部そろったら登録ボタンを押せるようにする
  function updateSubmit() {
    submit.disabled = !(nickname.value.trim() && notifyTime.value && home);
  }
  form.addEventListener("input", updateSubmit);

  // 現在地を1回だけ取得する
  locate.addEventListener("click", function () {
    homeStatus.textContent = "取得中…";
    navigator.geolocation.getCurrentPosition(function (position) {
      home = { lat: position.coords.latitude, lng: position.coords.longitude };
      homeStatus.textContent = "登録完了";
      updateSubmit();
    }, function (err) {
      homeStatus.textContent = err.code === err.PERMISSION_DENIED
        ? "ブラウザの設定で位置情報を許可してね"
        : "取得できなかった。もう一度試してね";
    }, { enableHighAccuracy: true, timeout: 10000 });
  });

  // サーバーに送って、ホームへ移動する
  form.addEventListener("submit", function (event) {
    event.preventDefault();
    submit.disabled = true;
    error.hidden = true;
    fetch("/api/users", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        nickname: nickname.value.trim(),
        lat: home.lat,
        lng: home.lng,
        daily_notify_time: notifyTime.value
      })
    }).then(function (response) {
      return response.json().then(function (data) {
        if (!response.ok) {
          throw new Error((data.error && data.error.message) || "登録できなかった。もう一度試してね");
        }
        location.href = data.user.home_url;
      });
    }).catch(function (e) {
      error.textContent = e.message;
      error.hidden = false;
      updateSubmit();
    });
  });
})();
