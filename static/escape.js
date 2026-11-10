/* ⑤ゾンビ接近中、⑥外のマップ、⑦クリア。判定そのものはサーバーが行う。 */
(function () {
  var root = document.getElementById("mission");
  var missionId = root.dataset.missionId;
  var loading = document.getElementById("loading");
  var loadError = document.getElementById("load-error");
  var screens = {
    escape: document.getElementById("screen-escape"),
    shelter: document.getElementById("screen-shelter"),
    cleared: document.getElementById("screen-cleared"),
    failed: document.getElementById("screen-failed"),
  };
  var countdown = document.getElementById("countdown");
  var zombieKmLabels = document.querySelectorAll(".js-zombie-km");
  var gauges = document.querySelectorAll(".js-zombie-gauge");
  var message = document.getElementById("escape-message");
  var escapeButton = document.getElementById("escape");
  var remaining = document.getElementById("remaining");
  var walkedLabel = document.getElementById("walked");
  var recenterButton = document.getElementById("recenter");
  var viewToggle = document.getElementById("view-toggle");
  var orbitHint = document.getElementById("orbit-hint");
  var checkinButton = document.getElementById("checkin");
  var checkinMessage = document.getElementById("checkin-message");
  var itemToast = document.getElementById("item-toast");
  var itemCounts = document.getElementById("item-counts");
  var hudFold = document.getElementById("hud-fold");
  var hudTop = document.getElementById("hud-top");
  var hudWarning = document.getElementById("hud-warning");

  var MAX_KM = 5;
  var POST_INTERVAL_MS = 15000;
  var NEAR_POST_INTERVAL_MS = 5000;  // ゾンビが近いときは追う先を早めに更新する
  var NEAR_KM = 0.3;
  var MAX_POST_ACCURACY_M = 100;  // サーバーは精度がこれより悪い位置を追う先にしない
  var CATCH_KM = 0.001;  // サーバーの game.CATCH_KM と同じ
  var FRESH_POSITION_MS = 15000;
  // サーバーの game.POSITION_FRESH と同じ。これより長く位置が届いていなければ、画面では捕獲にせずサーバーの判定を待つ
  var TRACK_FRESH_MS = 45000;
  var REFETCH_GPS_MS = 15000;  // これだけ GPS の更新がなければ取り直す（立ち止まっている間は更新が来ないことがある）
  var NEAR_WARNINGS_KM = [0.3, 0.1];  // ゾンビが近づいたことを振動で知らせる距離
  var HUD_WARNING_MS = 8000;  // 歩きスマホの注意を出しておく長さ
  // 歩いた距離の数え方。サーバーの game.py と同じ基準にする
  var MIN_TRACK_ACCURACY_M = 30;
  var MAX_WALK_SPEED_KM_PER_HOUR = 15;
  var MIN_STEP_M = 10;  // 立ち止まっているときの位置のぶれを足さない
  var MIN_HEADING_STEP_M = 5;
  // アイテム。サーバーは40m以内・精度50m以内で拾える。少し内側で送る
  var ITEM_REACH_M = 38;
  var ITEM_MAX_ACCURACY_M = 50;

  var mission = null;
  var user = null;
  var userUrl = null;
  var map = null;
  var points = { zombie: null, zombies: [], player: null, shelter: null, items: [] };
  var fleet = { zombies: [], at: 0, speedKmPerHour: 0 };  // サーバーと同期した時点のゾンビ全員
  var collecting = {};
  var toastTimer = null;
  var timer = null;
  var watchId = null;
  var lastPostAt = 0;
  var lastPosition = null;
  var routeInfo = document.getElementById("route-info");
  var routeRefresh = document.getElementById("route-refresh");
  var routeRequested = false;
  var routeLoading = false;
  var headingFrom = null;
  // 自分の表示位置。GPS の更新の間をなめらかにつなぎ、アバターがワープしないようにする
  var fix = null;
  var lastFix = null;
  var smoothing = false;
  var palette = JSON.parse(document.getElementById("avatar-palette").textContent);
  var walk = { m: 0, last: null };
  // サーバーがゾンビに追わせている点（最後に受け付けた位置を、サーバーと同じく小数第4位に丸めたもの）
  var chaseTarget = null;
  var caughtLocally = false;  // 画面の計算で追いつかれ、サーバーの確認を待っている間
  var CONFIRM_TRIES = 5;
  var lastAcceptedAt = 0;  // サーバーが位置を最後に受け付けた時刻
  var lastGpsAt = 0;  // GPS の更新か、取り直しを最後にした時刻
  var refetching = false;
  var wakeLock = null;
  var wakeLockPending = false;
  var nearWarned = {};
  var arrivalNoticed = false;
  var warningTimer = null;
  // 最初の表示に要るデータはページに入っている。API を待たずに描く
  var initial = JSON.parse(document.getElementById("initial").textContent || "null");

  // 地図のスクリプトは、ミッションを描く前から読み込み始める
  if (root.dataset.mapsKey) { ZombieMap.preload(root.dataset.mapsKey); }

  function request(url, options) {
    return fetch(url, options).then(function (response) {
      return response.json().catch(function () { return {}; }).then(function (body) {
        if (!response.ok) {
          var failure = new Error((body.error && body.error.message) || "通信に失敗しました。もう一度試してね");
          failure.code = body.error && body.error.code;
          throw failure;
        }
        return body;
      });
    });
  }

  function show(name) {
    loading.hidden = true;
    Object.keys(screens).forEach(function (key) { screens[key].hidden = key !== name; });
    document.body.classList.toggle("is-outside", name === "shelter");
    if (timer && name !== "escape" && name !== "shelter") {
      clearInterval(timer);
      timer = null;
    }
    if (watchId !== null && name !== "shelter") {
      navigator.geolocation.clearWatch(watchId);
      watchId = null;
    }
    if (name !== "shelter") {
      smoothing = false;
      releaseScreen();
    }
  }

  /* ⑥で画面を消さない。ブラウザは画面が消えると位置を取得できない */
  function holdScreen() {
    if (wakeLock || wakeLockPending || !navigator.wakeLock || document.hidden) { return; }
    wakeLockPending = true;
    navigator.wakeLock.request("screen").then(function (lock) {
      wakeLock = lock;
      lock.addEventListener("release", function () { wakeLock = null; });
      if (!mission || mission.status !== "escaped") { releaseScreen(); }
    }).catch(function () { /* 電池が少ないときなどは断られる。その間は画面が消えても、位置が戻ったときに判定する */ })
      .then(function () { wakeLockPending = false; });
  }

  function releaseScreen() {
    if (!wakeLock) { return; }
    wakeLock.release().catch(function () {});
    wakeLock = null;
  }

  function redraw() {
    if (map) { map.update(points); }
  }

  /* 画面を見ていなくても分かるよう、ゾンビが近づいたら振動で知らせる。家にいる⑤では震えさせない */
  function warnNear(km) {
    if (!navigator.vibrate || !mission || mission.status !== "escaped") { return; }
    NEAR_WARNINGS_KM.forEach(function (limit) {
      if (km <= limit && !nearWarned[limit]) {
        nearWarned[limit] = true;
        navigator.vibrate(limit === NEAR_WARNINGS_KM[1] ? [300, 100, 300, 100, 300] : [300, 150, 300]);
      } else if (km > limit + 0.05) {
        nearWarned[limit] = false;
      }
    });
  }

  function showMessage(text) {
    message.textContent = text;
    message.hidden = false;
  }

  function pad(n) { return String(n).padStart(2, "0"); }

  function metersBetween(a, b) {
    var rad = Math.PI / 180;
    var dLat = (b.lat - a.lat) * rad;
    var dLng = (b.lng - a.lng) * rad;
    var h = Math.pow(Math.sin(dLat / 2), 2) +
      Math.cos(a.lat * rad) * Math.cos(b.lat * rad) * Math.pow(Math.sin(dLng / 2), 2);
    return 2 * 6371000 * Math.asin(Math.sqrt(h));
  }

  function bearing(a, b) {
    var rad = Math.PI / 180;
    var y = Math.sin((b.lng - a.lng) * rad) * Math.cos(b.lat * rad);
    var x = Math.cos(a.lat * rad) * Math.sin(b.lat * rad) -
      Math.sin(a.lat * rad) * Math.cos(b.lat * rad) * Math.cos((b.lng - a.lng) * rad);
    return (Math.atan2(y, x) / rad + 360) % 360;
  }

  /* --- ゾンビ（ハードでは3体）。正しい位置はサーバーの計算で、その間は画面で動かして見せる --- */
  function movedKm() {
    return (Date.now() - fleet.at) * fleet.speedKmPerHour / 3600000;
  }

  function zombieKmNow() {
    var moved = movedKm();
    return fleet.zombies.reduce(function (min, z) { return Math.min(min, Math.max(0, z.km - moved)); }, Infinity);
  }

  /* 追跡中は、サーバーと同じ点（chaseTarget）へ同じ速さで向かわせる。距離の表示、地図の位置、捕獲の判定がそろう */
  function zombiePointsNow() {
    var moved = movedKm() * 1000;
    var target = chaseTarget || points.player;
    return fleet.zombies.map(function (z) {
      var start = { lat: z.lat, lng: z.lng };
      // 家へ向かう間は家の座標を持たないので動かさない
      if (!user || !user.chasing || !target) { return start; }
      var total = metersBetween(start, target);
      if (total <= 0) { return start; }
      var f = Math.min(1, moved / total);
      return { lat: start.lat + (target.lat - start.lat) * f, lng: start.lng + (target.lng - start.lng) * f };
    });
  }

  function roundChase(value) { return Math.round(value * 10000) / 10000; }

  /* ゾンビまでの距離。1km未満は m 単位で、なめらかに減って見えるようにする。
     「0 m」はサーバーが捕まえる距離に入ったときだけ出す */
  function formatZombieDistance(km) {
    var m = Math.max(0, km - CATCH_KM) * 1000;
    return m < 1000 ? Math.ceil(m) + " m" : (m / 1000).toFixed(2) + " km";
  }

  /* 画面の計算で追いつかれたら、サーバーの返事を待たずにその瞬間に失敗の画面を出す。
     サーバーも同じ計算をしているので、確認すれば捕獲が記録されている。記録を確かめてから中身（拠点の崩壊など）を合わせる */
  function checkCaught(km) {
    if (caughtLocally || !user || !user.chasing || !userUrl || !mission || mission.status !== "escaped" || km > CATCH_KM) { return; }
    // 画面を消して戻ってきた直後など、位置が届いていない間はサーバーの判定を待つ
    if (Date.now() - lastAcceptedAt > TRACK_FRESH_MS) { return; }
    caughtLocally = true;
    mission.status = "failed";
    render();
    confirmCaught(0);
  }

  function confirmCaught(tries) {
    function retryOrRestore() {
      if (tries + 1 < CONFIRM_TRIES) {
        setTimeout(function () { confirmCaught(tries + 1); }, 1000);
        return;
      }
      // サーバーではまだ追われている。今の状態の画面に戻す
      caughtLocally = false;
      load();
    }
    request(userUrl).then(function (next) {
      if (next.chasing) { retryOrRestore(); return; }
      caughtLocally = false;
      load();  // 失敗の記録と、崩れた拠点の名前を取り直す
    }).catch(retryOrRestore);
  }

  /* 近づかれたときの怖さ（0〜1）。100m以内で強くなる。iPhone は振動しないので、BGMの息づかいと叫び声はどのモードでも鳴らす。
     画面のふちの赤い脈打ちはハードだけ */
  var THREAT_KM = 0.1;
  var threatEl = document.getElementById("threat");
  function showThreat(km) {
    var on = mission && mission.status === "escaped";
    var threat = on ? Math.max(0, Math.min(1, (THREAT_KM - Math.max(0, km - CATCH_KM)) / THREAT_KM)) : 0;
    window.ZombieBGM.setThreat(threat);
    threatEl.style.setProperty("--threat", threat.toFixed(2));
    threatEl.hidden = threat <= 0 || mission.mode !== "hard";
  }

  function showZombie() {
    if (!fleet.zombies.length) { return; }
    var km = zombieKmNow();
    zombieKmLabels.forEach(function (label) { label.textContent = formatZombieDistance(km); });
    gauges.forEach(function (gauge) { gauge.style.width = Math.min(100, (km / MAX_KM) * 100) + "%"; });
    points.zombies = zombiePointsNow();
    points.zombie = points.zombies[0] || null;
    redraw();
    showThreat(km);
    warnNear(km);
    checkCaught(km);
  }

  /* 立ち止まっていると GPS の更新が来ないことがある。開いている間は位置を届け続ける */
  function refetchPosition() {
    if (mission.status !== "escaped" || document.hidden || refetching || !navigator.geolocation) { return; }
    if (Date.now() - lastGpsAt < REFETCH_GPS_MS) { return; }
    refetching = true;
    lastGpsAt = Date.now();
    navigator.geolocation.getCurrentPosition(function (position) {
      refetching = false;
      if (mission.status === "escaped") { onShelterPosition(position); }
    }, function () { refetching = false; }, { enableHighAccuracy: true, maximumAge: 0, timeout: 10000 });
  }

  function tick() {
    showZombie();
    updateEdgeInsets();
    refetchPosition();
    if (mission.status !== "reserved") { return; }
    var left = new Date(mission.depart_at).getTime() - Date.now();
    if (left <= 0) {
      countdown.textContent = "00:00";
      clearInterval(timer);
      timer = null;
      load();  // 時刻が来たらサーバーの判定を見る
      return;
    }
    var seconds = Math.ceil(left / 1000);
    var hours = Math.floor(seconds / 3600);
    var minutes = Math.floor((seconds % 3600) / 60);
    countdown.textContent = (hours > 0 ? hours + ":" : "") + pad(minutes) + ":" + pad(seconds % 60);
  }

  function applyUser(next) {
    var wasChasing = user && user.chasing;
    user = next;
    if (window.ZombieOutside) {
      window.ZombieOutside.avatar = { outfit: palette.outfits[next.avatar_outfit], skin: palette.skins[next.avatar_skin] };
    }
    var zombies = next.zombies && next.zombies.length ? next.zombies : [{ lat: next.zombie_lat, lng: next.zombie_lng, km: next.zombie_km }];
    fleet = { zombies: zombies, at: Date.now(), speedKmPerHour: next.zombie_speed_km_per_hour };
    showZombie();
    // ⑥の途中で捕まったら、サーバーがミッションを失敗にしている
    if (mission && mission.status === "escaped" && wasChasing && !next.chasing) { load(); }
  }

  /* --- 捕まったときの演出。ゾンビに群がられて食べられる（約3.5秒） ---
     ⑥を見ている最中なら3Dの地図の上でも見せ、画面を消していて後から開いたときは、画面の演出だけを1回見せる */
  var CAPTURE_FX_MS = 3600;
  var captureRunning = null;
  function captureKey() { return "zombie-capture-fx:" + missionId; }

  function captureEffect() {
    if (captureRunning) { return captureRunning; }
    var captured = caughtLocally || (user && (user.last_result === "captured" || user.last_result === "run_captured"));
    var seen = false;
    try { seen = localStorage.getItem(captureKey()) === "1"; } catch (e) { /* 覚えられなくても見せる */ }
    if (!captured || seen) { return Promise.resolve(); }
    try { localStorage.setItem(captureKey(), "1"); } catch (e) { /* なにもしない */ }

    var live = !screens.shelter.hidden;  // ⑥の地図が出ている
    var reduce = window.matchMedia("(prefers-reduced-motion: reduce)").matches;
    if (live) { document.dispatchEvent(new CustomEvent("zombie-capture")); }
    window.ZombieBGM.captured();
    if (navigator.vibrate) { navigator.vibrate([400, 100, 200, 80, 200, 80, 600]); }

    var fx = document.createElement("div");
    fx.className = "capture-fx" + (live ? " is-live" : "") + (reduce ? " is-reduced" : "");
    fx.setAttribute("role", "alert");
    var bites = "";
    for (var i = 0; i < 5; i += 1) { bites += '<span class="capture-bite"></span>'; }
    fx.innerHTML = '<div class="capture-vignette"></div><div class="capture-drip"></div>' + bites +
      '<p class="capture-text">ゲームオーバー</p><div class="capture-black"></div>';
    document.body.append(fx);
    if (live && !reduce) { screens.shelter.classList.add("is-shaking"); }

    captureRunning = new Promise(function (resolve) {
      setTimeout(function () {
        screens.shelter.classList.remove("is-shaking");
        resolve();
        // 暗転した状態でゲームオーバーの画面に切り替わってから、幕を外す
        setTimeout(function () { fx.classList.add("is-done"); }, 50);
        setTimeout(function () { fx.remove(); }, 700);
      }, reduce ? 1800 : CAPTURE_FX_MS);
    });
    return captureRunning;
  }

  /* --- 歩いた距離。ページを開き直しても続きから数えられるよう、端末に残す --- */
  function walkKey() { return "zombie-walk:" + missionId; }

  function loadWalk() {
    try {
      var saved = JSON.parse(localStorage.getItem(walkKey()));
      if (saved && typeof saved.m === "number") { walk = saved; }
    } catch (e) { /* 読めなければ0から */ }
  }

  function saveWalk() {
    try { localStorage.setItem(walkKey(), JSON.stringify(walk)); } catch (e) { /* 保存できなくても続ける */ }
  }

  function clearWalk() {
    try { localStorage.removeItem(walkKey()); } catch (e) { /* なにもしない */ }
  }

  function trackWalk(position) {
    if (position.coords.accuracy > MIN_TRACK_ACCURACY_M) { return; }
    var here = { lat: position.coords.latitude, lng: position.coords.longitude, t: position.timestamp };
    if (walk.last) {
      var step = metersBetween(walk.last, here);
      if (step < Math.max(MIN_STEP_M, position.coords.accuracy)) { return; }
      var hours = (here.t - walk.last.t) / 3600000;
      // 乗り物に乗った区間は足さない
      if (hours > 0 && step / 1000 / hours <= MAX_WALK_SPEED_KM_PER_HOUR) { walk.m += step; }
    }
    walk.last = here;
    saveWalk();
    walkedLabel.textContent = Math.round(walk.m).toLocaleString("ja-JP");
  }

  /* 歩く向き。アバターの向きに使う。カメラは自動では回さず、指で回す */
  function setHeading(degrees) {
    points.heading = degrees;
  }

  function trackHeading(position, here) {
    var heading = position.coords.heading;
    if (typeof heading === "number" && !isNaN(heading) && position.coords.speed > 0.5) {
      setHeading(heading);
      return;
    }
    if (!headingFrom) { headingFrom = here; return; }
    if (metersBetween(headingFrom, here) >= MIN_HEADING_STEP_M) {
      setHeading(bearing(headingFrom, here));
      headingFrom = here;
    }
  }

  /* 速さ（m/秒）。アバターの止まる・歩く・走るに使う */
  function trackSpeed(position, here) {
    var speed = position.coords.speed;
    if (typeof speed === "number" && !isNaN(speed) && speed >= 0) {
      points.speedMps = speed;
    } else if (lastFix) {
      var seconds = (position.timestamp - lastFix.t) / 1000;
      points.speedMps = seconds > 0 ? metersBetween(lastFix, here) / seconds : 0;
    }
    lastFix = { lat: here.lat, lng: here.lng, t: position.timestamp };
  }

  /* 表示位置を毎フレーム GPS の位置へ近づけ、カメラも一緒に動かす */
  function smoothLoop() {
    if (!smoothing) { return; }
    if (fix) {
      if (!points.player) {
        points.player = { lat: fix.lat, lng: fix.lng };
      } else {
        points.player = {
          lat: points.player.lat + (fix.lat - points.player.lat) * 0.12,
          lng: points.player.lng + (fix.lng - points.player.lng) * 0.12,
        };
      }
      // 4秒以上位置が変わらなければ止まっている
      if (lastFix && Date.now() - lastFix.t > 4000) { points.speedMps = 0; }
      redraw();
    }
    requestAnimationFrame(smoothLoop);
  }

  /* --- アイテム。近づいたら自動で拾い、振動で知らせる（画面を見なくてよい） --- */
  function showToast(text) {
    itemToast.textContent = text;
    itemToast.hidden = false;
    clearTimeout(toastTimer);
    toastTimer = setTimeout(function () { itemToast.hidden = true; }, 4000);
  }

  function showItemCounts() {
    var material = Math.round((mission.material_bonus_m || 0) / 200);
    itemCounts.textContent = "資材 " + material + (mission.freeze_found ? " ・ フリーズ 1" : "");
  }

  function refreshItems() {
    // 前に作ったミッションに残る撃退（repel）は、もう描かない
    points.items = (mission.items || []).filter(function (item) {
      return !item.collected && (item.kind === "material" || item.kind === "freeze");
    });
  }

  function onCollected(item, result) {
    item.collected = true;
    refreshItems();
    if (navigator.vibrate) { navigator.vibrate(result.kind === "freeze" ? [80, 60, 80] : [80]); }
    if (result.kind === "material") {
      mission.material_bonus_m = (mission.material_bonus_m || 0) + 200;
      showToast("資材を拾った！着いたら拠点ポイント+200");
    } else if (result.kind === "freeze") {
      mission.freeze_found = true;
      showToast("フリーズを拾った！着いたら、ゾンビを1日止められる");
    }
    showItemCounts();
    redraw();
  }

  function tryCollect(position) {
    if (position.coords.accuracy > ITEM_MAX_ACCURACY_M) { return; }
    var here = { lat: position.coords.latitude, lng: position.coords.longitude };
    (mission.items || []).forEach(function (item) {
      if (item.collected || collecting[item.id] || metersBetween(here, item) > ITEM_REACH_M) { return; }
      collecting[item.id] = true;
      request("/api/missions/" + encodeURIComponent(missionId) + "/items/" + encodeURIComponent(item.id) + "/collect", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ lat: here.lat, lng: here.lng, accuracy_m: position.coords.accuracy }),
      }).then(function (result) {
        if (result.status === "collected") {
          onCollected(item, result);
        } else if (result.status === "already") {
          item.collected = true;
          refreshItems();
        }
      }).catch(function () { /* 次の位置で拾い直す */ }).then(function () { collecting[item.id] = false; });
    });
  }

  function fillEscape() {
    var shelter = mission.shelter;
    document.getElementById("shelter-info").textContent = shelter.direction + "へ約" + shelter.distance_m + "m";
    document.getElementById("night-warning").hidden = !mission.night_warning;
  }

  function fillCommon() {
    var shelter = mission.shelter;
    document.querySelectorAll(".js-shelter-name").forEach(function (el) { el.textContent = shelter.name; });
    points.shelter = { lat: shelter.lat, lng: shelter.lng };
    points.mode = mission.mode;  // 3Dの地図が、ハードなら走ってくる怖いゾンビにする
    if (!map) {
      map = ZombieMap.create(document.getElementById("map"), { key: root.dataset.mapsKey, mapId: root.dataset.mapId });
      map.onFollowChange(function (following) { recenterButton.hidden = following; });
      window.ZombieOutside = { map: map, points: points, avatar: null };
      if (user) { window.ZombieOutside.avatar = { outfit: palette.outfits[user.avatar_outfit], skin: palette.skins[user.avatar_skin] }; }
      document.dispatchEvent(new CustomEvent("zombie-map-ready"));
    }
    map.update(points);
  }

  // ⑥でシェルターまでの徒歩ルートを取り、地図に線を引く。費用がかかるので、開いたときと更新ボタンのときだけ呼ぶ
  function loadRoute(here) {
    routeLoading = true;
    routeRefresh.disabled = true;
    routeInfo.textContent = "ルートを検索中…";
    routeInfo.hidden = false;
    return request("/api/missions/" + encodeURIComponent(missionId) + "/route", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ lat: here.lat, lng: here.lng }),
    }).then(function (route) {
      map.setRoute(route.points);
      routeInfo.textContent = "徒歩ルート 約" + route.duration_min + "分・" + route.distance_m.toLocaleString("ja-JP") + "m";
    }).catch(function (error) {
      routeInfo.textContent = error.message;
      showToast(error.message);  // 上の表示をたたんでいても分かるように
    }).then(function () {
      routeLoading = false;
      routeRefresh.disabled = false;
    });
  }

  // ⑥は開いている間の現在地を送る。これがゾンビの追う先になる
  function onShelterPosition(position) {
    lastPosition = position;
    lastGpsAt = Date.now();
    var here = { lat: position.coords.latitude, lng: position.coords.longitude };
    fix = here;
    if (!routeRequested) {
      routeRequested = true;
      loadRoute(here);
    }
    if (!points.player) { points.player = here; }
    trackWalk(position);
    trackHeading(position, here);
    trackSpeed(position, here);
    tryCollect(position);
    redraw();
    var left = Math.ceil(metersBetween(here, points.shelter) - mission.shelter.checkin_radius_m);
    remaining.textContent = left > 0 ? "あと約" + left + "m" : "到着！チェックインしよう";
    // ボタンはサーバーが着いたと判定する範囲（距離 ≦ 半径）に入っているときだけ出す。出たらまた隠す
    checkinButton.hidden = left > 0;
    if (left <= 0 && !arrivalNoticed) {
      // 画面を見ていなくても着いたと分かるよう、1回だけ知らせる
      arrivalNoticed = true;
      if (navigator.vibrate) { navigator.vibrate([200, 100, 200, 100, 400]); }
      window.ZombieBGM.arrived();
    }
    var interval = fleet.zombies.length && zombieKmNow() < NEAR_KM ? NEAR_POST_INTERVAL_MS : POST_INTERVAL_MS;
    if (Date.now() - lastPostAt < interval) { return; }
    postPosition(position, here);
  }

  function postPosition(position, here) {
    lastPostAt = Date.now();
    var accepted = position.coords.accuracy <= MAX_POST_ACCURACY_M;
    request(userUrl + "/position", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ lat: here.lat, lng: here.lng, accuracy_m: position.coords.accuracy }),
    }).then(function (next) {
      if (accepted) { lastAcceptedAt = Date.now(); }
      // 受け付けられた位置が、これからゾンビの追う先になる
      if (accepted && next.chasing) { chaseTarget = { lat: roundChase(here.lat), lng: roundChase(here.lng) }; }
      applyUser(next);
    }).catch(function () { lastPostAt = 0; });
  }

  function onShelterPositionError(error) {
    remaining.textContent = error.code === 1 ? "位置情報を許可してね" : "現在地を確認できない";
    checkinButton.hidden = false;  // 距離が分からないときに、押せなくならないようにする
  }

  function startShelterTracking() {
    if (watchId !== null) { return; }
    if (!navigator.geolocation) {
      onShelterPositionError({ code: 0 });
      return;
    }
    watchId = navigator.geolocation.watchPosition(onShelterPosition, onShelterPositionError, {
      enableHighAccuracy: true, maximumAge: 5000, timeout: 20000,
    });
    if (!smoothing) {
      smoothing = true;
      requestAnimationFrame(smoothLoop);
    }
  }

  function fillCleared() {
    var result = mission.result;
    var bonuses = [];
    if (result.mode_bonus_m) { bonuses.push("ハードボーナス +" + result.mode_bonus_m.toLocaleString("ja-JP")); }
    if (result.material_bonus_m) { bonuses.push("資材 +" + result.material_bonus_m.toLocaleString("ja-JP")); }
    if (result.freeze_gained) { bonuses.push("フリーズ +1"); }
    document.getElementById("clear-bonus").textContent =
      "拠点ポイント +" + result.base_points_gained.toLocaleString("ja-JP") + (bonuses.length ? "（" + bonuses.join("、") + "）" : "");
    document.getElementById("clear-walked").textContent = result.walked_m.toLocaleString("ja-JP");
    document.getElementById("clear-rank").textContent = result.base_rank_after;
    document.getElementById("clear-streak").textContent = result.streak;
    var rankUp = document.getElementById("rank-up");
    rankUp.hidden = result.base_rank_after <= result.base_rank_before;
    rankUp.textContent = "拠点が強化された！「" + result.base_rank_name_after + "」になった";
    document.getElementById("clear-base").textContent =
      "拠点「" + result.base_rank_name_after + "」拠点ポイント " + result.base_escaped_m_after.toLocaleString("ja-JP");
    clearWalk();
  }

  function fillFailed() {
    var collapse = document.getElementById("failed-collapse");
    if (mission.kind === "free" && user && user.last_result === "run_captured") {
      // 毎日の時刻より前のフリーランで捕まっても拠点は無事
      document.getElementById("failed-title").textContent = "フリーランは失敗。ゾンビに追いつかれた。";
      document.getElementById("failed-next").textContent = "拠点は無事。また走ろう。";
      collapse.hidden = true;
    } else {
      if (mission.kind === "free") {
        document.getElementById("failed-title").textContent = "毎日の時刻をまたいで、ゾンビに追いつかれた。";
      }
      collapse.hidden = !(user && user.lost_base_rank_name);
      if (!collapse.hidden) { collapse.textContent = user.lost_base_rank_name + "が崩壊し、テントからやり直しだ。"; }
    }
    clearWalk();
  }

  routeRefresh.addEventListener("click", function () {
    if (fix && !routeLoading) { loadRoute(fix); }
  });

  function render() {
    document.querySelectorAll("[data-home]").forEach(function (link) { link.href = mission.home_url; });
    // もう一度走る（フリーラン）
    var againUrl = "/run/" + mission.home_url.split("/").pop();
    document.querySelectorAll("[data-again]").forEach(function (link) { link.href = againUrl; });
    if (mission.status === "reserved") {
      show("escape");
      fillCommon();
      fillEscape();
      if (!timer) { timer = setInterval(tick, 1000); }
      tick();
    } else if (mission.status === "escaped") {
      show("shelter");
      // 地図は表示される枠に置いてから作る（隠れた枠の中で作ると大きさ0になり、表示が崩れる）
      document.getElementById("map-slot-shelter").append(document.getElementById("map"));
      fillCommon();
      refreshItems();
      showItemCounts();
      document.getElementById("hud-mode").textContent =
        (mission.kind === "free" ? "フリーラン・" : "") + (mission.mode === "hard" ? "ハード" : "ノーマル");
      document.getElementById("hud-mode").classList.toggle("is-hard", mission.mode === "hard");
      map.setFollow(true);
      updateEdgeInsets();
      showOrbitHintOnce();
      loadWalk();
      walkedLabel.textContent = Math.round(walk.m).toLocaleString("ja-JP");
      if (!timer) { timer = setInterval(tick, 1000); }
      tick();
      startShelterTracking();
      holdScreen();
      window.ZombieBGM.play();
      if (!warningTimer) { warningTimer = setTimeout(function () { hudWarning.hidden = true; }, HUD_WARNING_MS); }
    } else if (mission.status === "cleared") {
      window.ZombieBGM.stop();
      show("cleared");
      fillCleared();
    } else {
      // 捕まったときは、食べられる演出を見せてからゲームオーバーの画面に移る
      captureEffect().then(function () {
        if (mission.status !== "failed") { return; }  // 演出の間にサーバーの確認で戻った
        window.ZombieBGM.stop();
        show("failed");
        fillFailed();
      });
    }
  }

  function load() {
    var first = initial;
    initial = null;  // 埋め込みのデータは最初の1回だけ使う。2回目からは今の状態を API で取る
    var start = first
      ? Promise.resolve(first.mission).then(function (body) { mission = body; userUrl = "/api/users/" + body.home_url.split("/").pop(); return first.user; })
      : request("/api/missions/" + encodeURIComponent(missionId)).then(function (body) {
        mission = body;
        // user_id は home_url の末尾にある
        userUrl = "/api/users/" + mission.home_url.split("/").pop();
        return request(userUrl);
      });
    return start.then(function (next) {
      applyUser(next);
      render();
    }).catch(function (error) {
      loading.hidden = true;
      loadError.textContent = error.message;
      loadError.hidden = false;
    });
  }

  function currentPosition() {
    return new Promise(function (resolve, reject) {
      if (!navigator.geolocation) {
        reject({ code: 0 });
        return;
      }
      navigator.geolocation.getCurrentPosition(resolve, reject, {
        enableHighAccuracy: true, maximumAge: 0, timeout: 15000,
      });
    });
  }

  function onEscapeResult(result, position) {
    if (result.status === "escaped") {
      // 脱出した地点から歩いた距離を数え始める
      walk = { m: 0, last: null };
      trackWalk(position);
      load().then(function () {
        if (result.shelter_changed && mission) { showToast("外出先なので、近くの「" + mission.shelter.name + "」へ逃げ込もう"); }
      });
    } else if (result.status === "too_early") {
      showMessage("まだ早い。出発10分前から脱出できるよ");
    } else if (result.status === "low_accuracy") {
      showMessage("現在地がはっきりしない。屋外でもう一度試してね");
    } else if (result.status === "timeout") {
      load();
    }
  }

  // 逃走中のBGM。スマホは画面に触るまで音を出せないので、最初のタップで鳴らせるようにする
  var bgmToggle = document.getElementById("bgm-toggle");
  function showBgmState() {
    var on = window.ZombieBGM.enabled();
    bgmToggle.setAttribute("aria-pressed", on ? "true" : "false");
    bgmToggle.textContent = on ? "BGM オン" : "BGM オフ";
  }
  // 指で触れた瞬間（pointerdown）は、スマホでは音を出してよい操作に数えられない。離したときに有効にする
  ["touchend", "click", "keydown"].forEach(function (type) {
    document.addEventListener(type, function () {
      window.ZombieBGM.unlock();
      if (mission && mission.status === "escaped") { holdScreen(); }  // 断られていても、タップの中なら取れることがある
    }, true);
  });

  // 画面に戻ったら、画面の点灯を取り直し、今の位置をすぐ届ける
  document.addEventListener("visibilitychange", function () {
    if (document.hidden || !mission || mission.status !== "escaped") { return; }
    holdScreen();
    lastGpsAt = 0;
  });

  bgmToggle.addEventListener("click", function () {
    window.ZombieBGM.unlock();
    window.ZombieBGM.setEnabled(!window.ZombieBGM.enabled());
    showBgmState();
  });
  showBgmState();

  escapeButton.addEventListener("click", function () {
    window.ZombieBGM.unlock();
    escapeButton.disabled = true;
    message.hidden = true;
    currentPosition().then(function (position) {
      points.player = { lat: position.coords.latitude, lng: position.coords.longitude };
      map.update(points);
      return request("/api/missions/" + encodeURIComponent(missionId) + "/escape", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          lat: position.coords.latitude,
          lng: position.coords.longitude,
          accuracy_m: position.coords.accuracy,
        }),
      }).then(function (result) { onEscapeResult(result, position); });
    }).catch(function (error) {
      if (error && error.code === 1) {
        showMessage("位置情報を許可してね");
      } else if (error instanceof Error) {
        showMessage(error.message);
      } else {
        showMessage("現在地を取得できなかった。もう一度試してね");
      }
    }).then(function () {
      escapeButton.disabled = false;
    });
  });

  function onCheckinResult(result) {
    if (result.status === "cleared") {
      mission.status = "cleared";
      mission.result = result.result;
      render();
    } else if (result.status === "too_far") {
      checkinMessage.textContent = "あと" + result.remaining_m + "m！";
      checkinMessage.hidden = false;
    } else if (result.status === "low_accuracy") {
      checkinMessage.textContent = "現在地がはっきりしない。屋外でもう一度試してね";
      checkinMessage.hidden = false;
    } else if (result.status === "captured") {
      load();  // 途中で追いつかれていた。失敗の記録を取り直す
    }
  }

  checkinButton.addEventListener("click", function () {
    checkinButton.disabled = true;
    checkinMessage.hidden = true;
    // 監視中の最新の位置があれば、取り直さずに使う
    var fresh = lastPosition && Date.now() - lastPosition.timestamp < FRESH_POSITION_MS;
    (fresh ? Promise.resolve(lastPosition) : currentPosition()).then(function (position) {
      trackWalk(position);
      return request("/api/missions/" + encodeURIComponent(missionId) + "/checkin", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          lat: position.coords.latitude,
          lng: position.coords.longitude,
          accuracy_m: position.coords.accuracy,
          walked_m: Math.round(walk.m),
        }),
      }).then(onCheckinResult);
    }).catch(function (error) {
      if (error && error.code === "not_escaped") {
        load();  // 先に捕まっていた。「先に脱出して」ではなく、失敗の画面を出す
        return;
      }
      if (error && error.code === 1) {
        checkinMessage.textContent = "位置情報を許可してね";
      } else if (error instanceof Error) {
        checkinMessage.textContent = error.message;
      } else {
        checkinMessage.textContent = "現在地を取得できなかった。もう一度試してね";
      }
      checkinMessage.hidden = false;
    }).then(function () {
      checkinButton.disabled = false;
    });
  });

  // マップIDがベクターでないと、地図が傾かず3Dも出ない。設定ミスに気づけるよう知らせる
  document.addEventListener("zombie-map-flat", function () {
    document.getElementById("map-flat").hidden = false;
  });

  /* 画面の端の矢印が HUD に隠れないよう、HUD の高さを地図に渡す */
  function updateEdgeInsets() {
    if (!map || !mission || mission.status !== "escaped") { return; }
    var mapRect = document.getElementById("map").getBoundingClientRect();
    if (!mapRect.height) { return; }
    var top = document.querySelector("#screen-shelter .hud-top").getBoundingClientRect();
    var bottom = document.querySelector("#screen-shelter .hud-bottom").getBoundingClientRect();
    map.setEdgeInsets({
      top: Math.max(0, top.bottom - mapRect.top),
      bottom: Math.max(0, mapRect.bottom - bottom.top),
    });
  }
  window.addEventListener("resize", updateEdgeInsets);

  /* 上の表示の折りたたみ。最初は開いておき、たたんだかどうかは端末ごとに覚える */
  var HUD_FOLDED_KEY = "zombie-hud-folded";
  function setHudFolded(folded) {
    hudTop.classList.toggle("is-folded", folded);
    hudFold.setAttribute("aria-expanded", String(!folded));
    hudFold.setAttribute("aria-label", folded ? "上の表示を開く" : "上の表示をたたむ");
    updateEdgeInsets();
  }
  try { setHudFolded(localStorage.getItem(HUD_FOLDED_KEY) === "1"); } catch (e) { /* 読めなければ開いたまま */ }
  hudFold.addEventListener("click", function () {
    var folded = !hudTop.classList.contains("is-folded");
    setHudFolded(folded);
    try { localStorage.setItem(HUD_FOLDED_KEY, folded ? "1" : "0"); } catch (e) { /* 覚えられなくても開閉はする */ }
  });

  // 簡易レーダー（地図のキーがない）では傾きがないので出さない
  viewToggle.hidden = !root.dataset.mapsKey;
  viewToggle.addEventListener("click", function () {
    var top = map.toggleTopView();
    viewToggle.textContent = top ? "傾けて見る" : "真上から見る";
    viewToggle.setAttribute("aria-pressed", String(top));
  });

  /* 地図の回し方は最初の1回だけ案内する */
  var HINT_KEY = "zombie-orbit-hint";
  var hintTimer = null;
  function hideOrbitHint() {
    orbitHint.hidden = true;
    clearTimeout(hintTimer);
  }
  function showOrbitHintOnce() {
    var seen = !root.dataset.mapsKey;  // 簡易レーダーは回せないので案内しない
    if (!seen) try { seen = localStorage.getItem(HINT_KEY) === "1"; } catch (e) { /* 読めなければ出す */ }
    if (seen) { return; }
    try { localStorage.setItem(HINT_KEY, "1"); } catch (e) { /* 保存できなくても続ける */ }
    orbitHint.hidden = false;
    hintTimer = setTimeout(hideOrbitHint, 6000);
  }
  document.addEventListener("zombie-map-orbit", hideOrbitHint);

  recenterButton.addEventListener("click", function () {
    map.setFollow(true);
    map.update(points);
  });

  if (!missionId) {
    loading.hidden = true;
    loadError.textContent = "ミッションが見つかりません";
    loadError.hidden = false;
    return;
  }
  load();
  // ゾンビの位置は1分ごとにサーバーの値へ合わせる
  setInterval(function () {
    if (userUrl && mission && (mission.status === "reserved" || mission.status === "escaped")) {
      request(userUrl).then(applyUser).catch(function () {});
    }
  }, 60000);
})();
