/* トップページの「続きから」。ログインがないので、前に開いた拠点のユーザーIDをこのブラウザに覚えておき、
   タブを閉じても拠点に戻れるようにする。覚えたユーザーがもういなければ（開発中にデータを消したなど）忘れる。 */
(function () {
  var KEY = "dead-hour-user";
  var ID = /^[A-Za-z0-9_-]{20,64}$/;
  var resume = document.getElementById("resume");
  var start = document.getElementById("start");
  var userId = null;
  try { userId = localStorage.getItem(KEY); } catch (e) { return; }
  if (!userId || !ID.test(userId)) { return; }

  fetch("/api/users/" + userId).then(function (res) {
    if (res.status === 404) {
      try { localStorage.removeItem(KEY); } catch (e) { /* なにもしない */ }
      return null;
    }
    return res.ok ? res.json() : null;
  }).then(function (user) {
    if (!user) { return; }
    resume.href = "/home/" + userId;
    resume.textContent = "続きから（" + user.nickname + " 隊員の拠点へ）";
    resume.hidden = false;
    start.classList.remove("primary");
    start.textContent = "新しくはじめる";
  }).catch(function () {});
})();
