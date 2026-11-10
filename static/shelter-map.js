/* 公園選び（毎日の逃走の予約と、フリーラン）の一覧と地図。
   ShelterPicker.render(公園, 番号, 選んだときの関数) で一覧の1行を作り、
   ShelterPicker.map(要素, { key, mapId, onPick }) で地図を作る。地図は setShelters(公園の一覧) と select(place_id) で動かす。
   地図のピンを押すと onPick(place_id) が呼ばれる。
   家（フリーランでは今いる場所）の印は出さず、地図の範囲は公園だけに合わせる。真ん中が家にならないようにするため。 */
(function () {
  var SINGLE_ZOOM = 16;  // 公園が1つだけのときの拡大
  var SELECTED_ZOOM = 16;  // 一覧で選んだ公園に寄るとき

  function render(shelter, number, onSelect) {
    var label = document.createElement("label");
    label.className = "shelter";
    var radio = document.createElement("input");
    radio.type = "radio";
    radio.name = "place";
    radio.value = shelter.place_id;
    radio.addEventListener("change", function () { onSelect(shelter); });
    var badge = document.createElement("span");
    badge.className = "shelter-number";
    badge.textContent = String(number);
    badge.setAttribute("aria-hidden", "true");
    var info = document.createElement("span");
    info.className = "shelter-info";
    var name = document.createElement("strong");
    name.textContent = shelter.name;
    var detail = document.createElement("span");
    detail.className = "muted small";
    detail.textContent = " " + shelter.direction + "の方角";
    info.append(name, document.createElement("br"), detail);
    if (shelter.address) {
      var address = document.createElement("span");
      address.className = "shelter-address";
      address.textContent = shelter.address;
      info.append(document.createElement("br"), address);
    }
    var distance = document.createElement("span");
    distance.className = "shelter-xp";
    var about = document.createElement("span");
    about.className = "jp";  // 数字用のフォントには日本語がないので、本文のフォントにする
    about.textContent = "約";
    distance.append(about, shelter.distance_m + "m");
    label.append(radio, badge, info, distance);
    return label;
  }

  /* 一覧の中の、その公園のラジオボタンを押す。一覧で選んだときと同じ処理が動く */
  function check(list, placeId) {
    list.querySelectorAll('input[name="place"]').forEach(function (radio) {
      if (radio.value === placeId) { radio.click(); }
    });
  }

  /* 公園以外を候補にしたときの説明。公園だけなら空文字 */
  function note(shelters) {
    if (shelters.some(function (s) { return s.kind === "waypoint"; })) {
      return "近くに公園やお店が見つからなかったので、地図上の目印を候補にしました。歩いて行ける場所か、地図で確かめて選んでね。";
    }
    if (shelters.some(function (s) { return s.kind === "landmark"; })) {
      return "近くに公園がなかったので、コンビニや駅などを候補にしました。";
    }
    return "";
  }

  function loadGoogleMaps(key) {
    if (window.google && window.google.maps && window.google.maps.importLibrary) { return Promise.resolve(); }
    return new Promise(function (resolve, reject) {
      window.__shelterMapReady = resolve;
      window.gm_authFailure = function () { reject(new Error("auth")); };
      var script = document.createElement("script");
      script.async = true;
      script.src = "https://maps.googleapis.com/maps/api/js?key=" + encodeURIComponent(key) +
        "&loading=async&callback=__shelterMapReady";
      script.onerror = function () { reject(new Error("load")); };
      document.head.append(script);
    });
  }

  /* 地図が使えないとき（開発中でキーがない、読み込めない）は、枠ごと隠して一覧だけにする */
  function createMap(el, config) {
    var state = { map: null, Marker: null, Bounds: null, markers: {}, shelters: [], selected: null, ready: null, keepView: false };
    var caption = document.createElement("p");
    caption.className = "shelter-picked muted small";
    caption.setAttribute("role", "status");
    function hide() {
      el.hidden = true;
      caption.hidden = true;
    }
    if (!config.key) {
      hide();
      return { setShelters: function () {}, select: function () {} };
    }
    el.hidden = false;
    caption.textContent = "地図の番号を押しても選べます。";
    el.after(caption);
    state.ready = loadGoogleMaps(config.key)
      .then(function () {
        return Promise.all([google.maps.importLibrary("maps"), google.maps.importLibrary("marker"), google.maps.importLibrary("core")]);
      })
      .then(function (libs) {
        state.map = new libs[0].Map(el, {
          center: { lat: 35.681, lng: 139.767 },
          zoom: 14,
          mapId: config.mapId,
          disableDefaultUI: true,
          zoomControl: true,
          gestureHandling: "cooperative",  // 1本指では画面のスクロールにする
          clickableIcons: false,
        });
        state.Marker = libs[1].AdvancedMarkerElement;
        state.Bounds = libs[2].LatLngBounds;
      })
      .catch(hide);

    function pin(number, selected) {
      var el = document.createElement("span");
      el.className = "shelter-pin" + (selected ? " is-selected" : "");
      el.textContent = String(number);
      return el;
    }

    /* 地図のピンを押したら、一覧で選んだのと同じことをする。他のピンも押せるよう、見ている範囲は動かさない */
    function tapped(shelter) {
      if (!config.onPick) { return; }
      state.keepView = true;
      config.onPick(shelter.place_id);
      state.keepView = false;
    }

    function draw() {
      if (!state.map) { return; }
      Object.keys(state.markers).forEach(function (id) { state.markers[id].map = null; });
      state.markers = {};
      state.shelters.forEach(function (shelter, index) {
        var selected = shelter.place_id === state.selected;
        var marker = new state.Marker({
          map: state.map,
          position: { lat: shelter.lat, lng: shelter.lng },
          content: pin(index + 1, selected),
          title: shelter.name,
          zIndex: selected ? 10 : 1,
          gmpClickable: true,
        });
        marker.addEventListener("gmp-click", function () { tapped(shelter); });
        state.markers[shelter.place_id] = marker;
      });
    }

    return {
      setShelters: function (shelters) {
        state.shelters = shelters.slice();
        state.ready.then(function () {
          if (!state.map || !state.shelters.length) { return; }
          draw();
          if (state.shelters.length === 1) {
            state.map.setCenter({ lat: state.shelters[0].lat, lng: state.shelters[0].lng });
            state.map.setZoom(SINGLE_ZOOM);
            return;
          }
          var bounds = new state.Bounds();
          state.shelters.forEach(function (s) { bounds.extend({ lat: s.lat, lng: s.lng }); });
          state.map.fitBounds(bounds, 36);
        });
      },
      select: function (placeId) {
        var keepView = state.keepView;
        var shelter = state.shelters.find(function (s) { return s.place_id === placeId; });
        state.selected = placeId;
        if (shelter) { caption.textContent = "選択中: " + shelter.name + "（約" + shelter.distance_m + "m）"; }
        state.ready.then(function () {
          if (!state.map || !shelter) { return; }
          draw();
          if (keepView) { return; }
          state.map.panTo({ lat: shelter.lat, lng: shelter.lng });
          if (state.map.getZoom() < SELECTED_ZOOM) { state.map.setZoom(SELECTED_ZOOM); }
        });
      },
    };
  }

  window.ShelterPicker = { render: render, map: createMap, check: check, note: note };
})();
