/* フリーラン。今いる場所を取り、近くの公園とモードを選んで、そのまま外のマップ（⑥）へ入る。 */
(function () {
  var form = document.getElementById("run-form");
  var userId = form.dataset.user;
  var status = document.getElementById("shelter-status");
  var relocate = document.getElementById("relocate");
  var list = document.getElementById("shelters");
  var submit = document.getElementById("submit");
  var error = document.getElementById("error");
  var here = null;

  function showError(message) {
    error.textContent = message;
    error.hidden = false;
  }

  // 公園の地図。家（今いる場所）の印は出さない
  var mapEl = document.getElementById("shelter-map");
  var shelterMap = window.ShelterPicker.map(mapEl, {
    key: mapEl.dataset.mapsKey,
    mapId: mapEl.dataset.mapId,
    onPick: function (placeId) { window.ShelterPicker.check(list, placeId); },
  });

  function renderShelter(shelter, number) {
    return window.ShelterPicker.render(shelter, number, function () {
      submit.disabled = false;
      shelterMap.select(shelter.place_id);
    });
  }

  function loadShelters() {
    status.textContent = "近くの公園を探しています…";
    list.textContent = "";
    submit.disabled = true;
    fetch("/api/users/" + userId + "/shelters/near", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ lat: here.lat, lng: here.lng }),
    })
      .then(function (res) {
        if (!res.ok) { throw new Error("search"); }
        return res.json();
      })
      .then(function (shelters) {
        if (shelters.length === 0) {
          status.textContent = "近くに公園が見つかりませんでした。拠点を強くすると、遠くの公園も探せます。";
          return;
        }
        status.textContent = window.ShelterPicker.note(shelters);
        status.hidden = !status.textContent;
        shelters.forEach(function (shelter, index) { list.append(renderShelter(shelter, index + 1)); });
        shelterMap.setShelters(shelters);
        list.querySelector('input[name="place"]').click();  // いちばん近い公園を選んでおく
      })
      .catch(function () {
        status.textContent = "公園を探せませんでした。時間をおいて開き直してください。";
      });
  }

  function locate() {
    relocate.hidden = true;
    status.hidden = false;
    status.textContent = "現在地を確認中…";
    if (!navigator.geolocation) {
      status.textContent = "このブラウザでは位置情報を使えません。";
      return;
    }
    navigator.geolocation.getCurrentPosition(function (position) {
      here = { lat: position.coords.latitude, lng: position.coords.longitude };
      loadShelters();
    }, function (err) {
      status.textContent = err.code === 1
        ? "位置情報が許可されていません。ブラウザの設定から許可してください。"
        : "現在地を取得できませんでした。屋外でもう一度試してね。";
      relocate.hidden = false;
    }, { enableHighAccuracy: true, timeout: 15000, maximumAge: 10000 });
  }

  relocate.addEventListener("click", locate);
  locate();

  form.addEventListener("submit", function (event) {
    event.preventDefault();
    var selected = form.querySelector('input[name="place"]:checked');
    var mode = form.querySelector('input[name="mode"]:checked');
    if (!selected || !here) { return; }
    error.hidden = true;
    submit.disabled = true;
    submit.textContent = "ゾンビが迫ってくる…";
    fetch("/api/users/" + userId + "/runs", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ place_id: selected.value, mode: mode ? mode.value : "normal", lat: here.lat, lng: here.lng }),
    })
      .then(function (res) {
        return res.json().then(function (data) {
          if (!res.ok) { throw new Error((data.error && data.error.message) || "始められませんでした"); }
          return data;
        });
      })
      .then(function (mission) { location.href = mission.escape_url; })
      .catch(function (err) {
        showError(err.message);
        submit.disabled = false;
        submit.textContent = "フリーラン開始！";
      });
  });
})();
