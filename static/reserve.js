(function () {
  var form = document.getElementById("reserve-form");
  var userId = form.dataset.user;
  var status = document.getElementById("shelter-status");
  var list = document.getElementById("shelters");
  var submit = document.getElementById("submit");
  var error = document.getElementById("error");

  // ゾンビが来る時刻。時刻は毎日同じで、ここでは変えられない
  document.querySelectorAll("time.js-clock").forEach(function (el) {
    el.textContent = new Date(el.dateTime).toLocaleTimeString("ja-JP", { hour: "2-digit", minute: "2-digit" });
  });

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

  fetch("/api/users/" + userId + "/shelters")
    .then(function (res) {
      if (!res.ok) { throw new Error("search"); }
      return res.json();
    })
    .then(function (shelters) {
      if (shelters.length === 0) {
        status.textContent = "近くに公園が見つかりませんでした。";
        return;
      }
      status.textContent = window.ShelterPicker.note(shelters);
      status.hidden = !status.textContent;
      shelters.forEach(function (shelter, index) { list.append(renderShelter(shelter, index + 1)); });
      shelterMap.setShelters(shelters);
    })
    .catch(function () {
      status.textContent = "公園を探せませんでした。時間をおいて開き直してください。";
    });

  form.addEventListener("submit", function (event) {
    event.preventDefault();
    var selected = form.querySelector('input[name="place"]:checked');
    var mode = form.querySelector('input[name="mode"]:checked');
    if (!selected) { return; }
    error.hidden = true;
    submit.disabled = true;
    submit.textContent = "本部と交信中…";
    fetch("/api/users/" + userId + "/missions", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ place_id: selected.value, mode: mode ? mode.value : "normal" }),
    })
      .then(function (res) {
        return res.json().then(function (data) {
          if (!res.ok) {
            throw new Error((data.error && data.error.message) || "決定できませんでした");
          }
          return data;
        });
      })
      .then(function (mission) {
        form.hidden = true;
        document.getElementById("done-shelter").textContent = mission.shelter.name;
        document.getElementById("done-mode").textContent = mission.mode === "hard" ? "ハード" : "ノーマル";
        document.getElementById("done").hidden = false;
      })
      .catch(function (err) {
        showError(err.message);
        submit.disabled = false;
        submit.textContent = "これで決定";
      });
  });
})();
