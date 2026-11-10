/* 別の端末から、引き継ぎコードで続ける。 */
(function () {
  var form = document.getElementById("link-form");
  var code = document.getElementById("code");
  var submit = document.getElementById("submit");
  var error = document.getElementById("error");

  form.addEventListener("submit", function (event) {
    event.preventDefault();
    submit.disabled = true;
    error.hidden = true;
    fetch("/api/devices/link", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ code: code.value }),
    }).then(function (response) {
      return response.json().then(function (data) {
        if (!response.ok) {
          throw new Error((data.error && data.error.message) || "引き継げませんでした。もう一度試してね");
        }
        location.href = data.home_url;
      });
    }).catch(function (e) {
      error.textContent = e.message;
      error.hidden = false;
      submit.disabled = false;
    });
  });
})();
