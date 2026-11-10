/* ゾンビ、自分、シェルター、アイテムの地図。ZombieMap.create(要素, 設定) で使う。
   update({ player, shelter, zombies: [{lat, lng}], items: [{id, kind, lat, lng}] }) で表示を更新する。
   ⑥では setFollow(true) で、自分を中心に傾けて表示する（追従モード）。
   追従中は位置情報ゲームのように、1本指で自分の周りを回して傾け、2本指で拡大と回転をする。
   画面の外にいるゾンビは、画面の端に方角と距離の矢印で示す。
   Google マップを読み込めないときは簡易レーダーに切り替える。 */
(function () {
  var ICONS = {
    zombie: "/static/icons/zombie.svg", player: "/static/icons/player.svg", shelter: "/static/icons/shelter.svg",
    material: "/static/icons/material.svg", freeze: "/static/icons/freeze.svg",
  };
  var LABELS = { zombie: "ゾンビ", player: "自分", shelter: "シェルター", material: "資材", freeze: "フリーズ" };
  /* ゾンビは1体を1体として描く（ノーマルは1体、ハードは3体）。飾りの群れは数を誤解させるので描かない */
  /* 追従モードのカメラ。位置情報ゲームのように低く近くから見る。傾きと回転はベクター地図でだけ効く */
  var FOLLOW_ZOOM = 19;
  var FOLLOW_TILT = 67.5;  // 最大の傾き
  var FOLLOW_LEAD_M = 22;  // カメラの向きの少し先を画面の中心にして、自分を中央よりやや下に置く（ズーム19・最大の傾きのとき）
  var MIN_ZOOM = 12;  // 周り数kmまで引ける（ハードの3体がそろって入る）
  var MAX_ZOOM = 21;
  var DEGREES_PER_PX = 0.3;  // 1本指で左右になぞったときの回転
  var TILT_PER_PX = 0.3;  // 1本指で上下になぞったときの傾き
  var WHEEL_ZOOM_PER_PX = 0.002;
  /* 画面の端の矢印 */
  var EDGE_MARGIN_PX = 36;  // 矢印の丸の半径より少し大きく
  var EDGE_DANGER_M = 500;  // これより近いと点滅させる
  var METERS_PER_DEGREE_LAT = 110574;
  var METERS_PER_DEGREE_LNG = 111320;

  function iconImage(kind) {
    var image = document.createElement("img");
    image.src = ICONS[kind];
    image.alt = "";
    return image;
  }

  function hasPoint(p) { return p && typeof p.lat === "number" && typeof p.lng === "number"; }

  function clamp(value, min, max) { return Math.max(min, Math.min(max, value)); }

  function wrapDegrees(degrees) { return ((degrees % 360) + 360) % 360; }

  /* a から b への東西・南北の距離（m） */
  function offsetMeters(a, b) {
    return {
      east: (b.lng - a.lng) * Math.cos((a.lat * Math.PI) / 180) * METERS_PER_DEGREE_LNG,
      north: (b.lat - a.lat) * METERS_PER_DEGREE_LAT,
    };
  }

  function metersBetween(a, b) {
    var d = offsetMeters(a, b);
    return Math.sqrt(d.east * d.east + d.north * d.north);
  }

  /* a から見た b の方位（北が0度、時計回り） */
  function bearingDeg(a, b) {
    var d = offsetMeters(a, b);
    return wrapDegrees((Math.atan2(d.east, d.north) * 180) / Math.PI);
  }

  /* p から方位 heading へ meters 進んだ点 */
  function moveBy(p, heading, meters) {
    var rad = (heading * Math.PI) / 180;
    return {
      lat: p.lat + (Math.cos(rad) * meters) / METERS_PER_DEGREE_LAT,
      lng: p.lng + (Math.sin(rad) * meters) / (METERS_PER_DEGREE_LNG * Math.cos((p.lat * Math.PI) / 180)),
    };
  }

  function formatDistance(meters) {
    return meters < 1000 ? Math.round(meters / 10) * 10 + "m" : (meters / 1000).toFixed(1) + "km";
  }

  /* 古い呼び方（zombie だけ）にも対応する */
  function zombiesOf(points) {
    if (points.zombies && points.zombies.length) { return points.zombies.filter(hasPoint); }
    return hasPoint(points.zombie) ? [points.zombie] : [];
  }

  /* 地図上に置くもの。種類、位置、キーの一覧 */
  function placesOf(points) {
    var places = [];
    if (hasPoint(points.shelter)) { places.push({ key: "shelter", kind: "shelter", point: points.shelter }); }
    (points.items || []).forEach(function (item) {
      if (hasPoint(item)) { places.push({ key: "item:" + item.id, kind: item.kind, point: item }); }
    });
    if (hasPoint(points.player)) { places.push({ key: "player", kind: "player", point: points.player }); }
    zombiesOf(points).forEach(function (zombie, index) {
      places.push({ key: "zombie:" + index, kind: "zombie", point: zombie });
    });
    return places;
  }

  /* --- 簡易レーダー --- */
  function Radar(el) {
    this.el = el;
    el.classList.add("radar");
    el.textContent = "";
    this.nodes = {};
    var note = document.createElement("span");
    note.className = "radar-note";
    note.textContent = "簡易表示（道路は出ません）";
    el.append(note);
    var svg = document.createElementNS("http://www.w3.org/2000/svg", "svg");
    svg.setAttribute("class", "radar-route");
    svg.setAttribute("viewBox", "0 0 100 100");
    svg.setAttribute("preserveAspectRatio", "none");
    svg.setAttribute("aria-hidden", "true");
    this.routeLine = document.createElementNS("http://www.w3.org/2000/svg", "polyline");
    svg.append(this.routeLine);
    el.append(svg);
    this.route = [];
  }

  Radar.prototype.setRoute = function (route) { this.route = route || []; };

  Radar.prototype.node = function (key, kind) {
    if (this.nodes[key]) { return this.nodes[key]; }
    var item = document.createElement("span");
    item.className = "radar-item" + (kind === "zombie" ? " shamble" : "") + (kind === "material" || kind === "freeze" ? " small" : "");
    item.title = LABELS[kind];
    item.setAttribute("aria-label", LABELS[kind]);
    item.append(iconImage(kind));
    this.el.append(item);
    this.nodes[key] = item;
    return item;
  };

  /* レーダーは全員が収まるよう縮尺を合わせるので、端の矢印は要らない */
  Radar.prototype.update = function (points) {
    var places = placesOf(points);
    var zombies = zombiesOf(points);
    // 脱出する前はゾンビの座標が来ないので、シェルターを中心にする
    var center = hasPoint(points.player) ? points.player : (zombies[0] || points.shelter);
    if (!hasPoint(center)) { return; }
    var scaleLng = Math.cos((center.lat * Math.PI) / 180);
    var max = 0.0001;
    places.forEach(function (place) {
      place.dx = (place.point.lng - center.lng) * scaleLng;
      place.dy = place.point.lat - center.lat;
      max = Math.max(max, Math.abs(place.dx), Math.abs(place.dy));
    });
    var seen = {};
    places.forEach(function (place) {
      var left = 50 + (place.dx / max) * 42 + "%";
      var top = 50 - (place.dy / max) * 42 + "%";
      var node = this.node(place.key, place.kind);
      node.hidden = false;
      node.style.left = left;
      node.style.top = top;
      seen[place.key] = true;
    }, this);
    Object.keys(this.nodes).forEach(function (key) { if (!seen[key]) { this.nodes[key].hidden = true; } }, this);
    this.routeLine.setAttribute("points", this.route.map(function (point) {
      var x = 50 + (((point.lng - center.lng) * scaleLng) / max) * 42;
      var y = 50 - ((point.lat - center.lat) / max) * 42;
      return x + "," + y;
    }).join(" "));
  };

  /* --- Google マップ --- */
  function GoogleZombieMap(el, libs, mapId, view) {
    this.el = el;
    this.view = view;
    this.Marker = libs.marker.AdvancedMarkerElement;
    this.LatLngBounds = libs.core.LatLngBounds;
    this.Polyline = libs.maps.Polyline;
    this.routeLine = null;
    var options = {
      center: { lat: 35.681, lng: 139.767 },
      zoom: 14,
      mapId: mapId,
      disableDefaultUI: true,
      zoomControl: true,
      gestureHandling: "greedy",
    };
    // 終末の雰囲気に合わせて暗い配色にし、傾けられるようベクター地図を頼む（古い版では無視される）
    if (libs.core.ColorScheme) { options.colorScheme = libs.core.ColorScheme.DARK; }
    if (libs.maps.RenderingType) { options.renderingType = libs.maps.RenderingType.VECTOR; }
    // マップIDの設定で傾きと回転が無効でも、コードの側で許可する（ベクター地図でだけ効く）
    options.tiltInteractionEnabled = true;
    options.headingInteractionEnabled = true;
    this.map = new libs.maps.Map(el, options);
    this.markers = {};
    this.fittedKinds = "";
    this.markersHidden = false;
    this.latest = null;
    this.orbiting = false;
    this.cameraQueued = false;
    this.edgeLoop = null;
    var self = this;
    // 追従していないときに指で動かしたら追従を止める（追従中は自前の操作なので、この経路は通らない）
    this.map.addListener("dragstart", function () { self.view.pauseFollow(); });
    // ベクター地図の準備ができる前に傾けると0度のままになるので、準備ができたら合わせ直す
    this.map.addListener("renderingtype_changed", function () { self.queueCamera(); });

    this.edges = document.createElement("div");
    this.edges.className = "zombie-edges";
    this.edges.setAttribute("aria-hidden", "true");
    this.edgeNodes = [];
    el.append(this.edges);

    this.setUpOrbit();
  }

  /* シェルターまでの徒歩ルート。下に暗い線を敷いて、明るい地図でも見えるようにする */
  GoogleZombieMap.prototype.setRoute = function (route) {
    (this.routeLine || []).forEach(function (line) { line.setMap(null); });
    if (!route || route.length < 2) {
      this.routeLine = null;
      return;
    }
    this.routeLine = [["#0c120a", 10], ["#c6f135", 5]].map(function (style) {
      return new this.Polyline({
        map: this.map, path: route, strokeColor: style[0], strokeOpacity: .95, strokeWeight: style[1], clickable: false,
      });
    }, this);
  };

  /* 追従中のカメラ。自分から、カメラの向きへ少し進んだ点を中心にするので、自分はいつも画面のやや下に来る。
     真上から見るときは自分を真ん中に置く */
  GoogleZombieMap.prototype.followCamera = function () {
    var points = this.latest;
    if (!points || !hasPoint(points.player)) { return; }
    var camera = this.view.camera;
    if (camera.heading === null) {
      // 最初はシェルターの方角を向く
      camera.heading = hasPoint(points.shelter) ? bearingDeg(points.player, points.shelter) : 0;
    }
    var lead = FOLLOW_LEAD_M * (camera.tilt / FOLLOW_TILT) * Math.pow(2, FOLLOW_ZOOM - camera.zoom);
    this.map.moveCamera({
      center: moveBy(points.player, camera.heading, lead),
      zoom: camera.zoom,
      tilt: camera.tilt,
      heading: camera.heading,
    });
  };

  /* 指の操作は1フレームにまとめてカメラへ反映する */
  GoogleZombieMap.prototype.queueCamera = function () {
    if (this.cameraQueued || !this.view.following) { return; }
    this.cameraQueued = true;
    var self = this;
    requestAnimationFrame(function () {
      self.cameraQueued = false;
      if (self.view.following) { self.followCamera(); }
    });
  };

  /* 位置情報ゲームのような操作。1本指の左右で自分の周りを回り、上下で傾ける。2本指で拡大と回転。ホイールで拡大 */
  GoogleZombieMap.prototype.setUpOrbit = function () {
    var self = this;
    var el = this.el;
    var pointers = new Map();
    var last = null;

    function snapshot() {
      var list = Array.from(pointers.values());
      if (list.length === 0) { return null; }
      if (list.length === 1) { return { count: 1, x: list[0].x, y: list[0].y }; }
      var a = list[0];
      var b = list[1];
      return {
        count: 2,
        dist: Math.max(1, Math.hypot(b.x - a.x, b.y - a.y)),
        angle: (Math.atan2(b.y - a.y, b.x - a.x) * 180) / Math.PI,
      };
    }

    function touched() {
      if (!self.view.touched) {
        self.view.touched = true;
        document.dispatchEvent(new CustomEvent("zombie-map-orbit"));
      }
      self.queueCamera();
    }

    el.addEventListener("pointerdown", function (event) {
      if (!self.orbiting) { return; }
      pointers.set(event.pointerId, { x: event.clientX, y: event.clientY });
      try { el.setPointerCapture(event.pointerId); } catch (e) { /* 取れなくても動く */ }
      last = snapshot();
    }, true);

    el.addEventListener("pointermove", function (event) {
      if (!self.orbiting || !pointers.has(event.pointerId)) { return; }
      pointers.set(event.pointerId, { x: event.clientX, y: event.clientY });
      var now = snapshot();
      var camera = self.view.camera;
      if (last && now && now.count === last.count && camera.heading !== null) {
        if (now.count === 1) {
          // 画面の奥をつかんで動かす感覚にする。右へなぞると、奥の景色が右へ動く
          camera.heading = wrapDegrees(camera.heading - (now.x - last.x) * DEGREES_PER_PX);
          camera.tilt = clamp(camera.tilt - (now.y - last.y) * TILT_PER_PX, 0, FOLLOW_TILT);
        } else {
          camera.zoom = clamp(camera.zoom + Math.log2(now.dist / last.dist), MIN_ZOOM, MAX_ZOOM);
          var turn = now.angle - last.angle;
          if (turn > 180) { turn -= 360; }
          if (turn < -180) { turn += 360; }
          camera.heading = wrapDegrees(camera.heading - turn);
        }
        touched();
      }
      last = now;
    }, true);

    function release(event) {
      if (!pointers.has(event.pointerId)) { return; }
      pointers.delete(event.pointerId);
      last = snapshot();
    }
    el.addEventListener("pointerup", release, true);
    el.addEventListener("pointercancel", release, true);

    el.addEventListener("wheel", function (event) {
      if (!self.orbiting) { return; }
      event.preventDefault();
      var camera = self.view.camera;
      camera.zoom = clamp(camera.zoom - event.deltaY * WHEEL_ZOOM_PER_PX, MIN_ZOOM, MAX_ZOOM);
      touched();
    }, { capture: true, passive: false });

    this.releaseAll = function () { pointers.clear(); last = null; };
  };

  /* 追従中だけ、地図の標準の操作を止めて自前の操作にする */
  GoogleZombieMap.prototype.setOrbit = function (on) {
    if (this.orbiting === on) { return; }
    this.orbiting = on;
    this.releaseAll();
    this.map.setOptions({ gestureHandling: on ? "none" : "greedy", zoomControl: !on });
    this.el.classList.toggle("is-orbit", on);
    if (on) {
      this.startEdges();
    } else {
      this.stopEdges();
    }
  };

  /* --- 画面の外のゾンビを、画面の端の矢印で示す --- */
  GoogleZombieMap.prototype.startEdges = function () {
    if (this.edgeLoop) { return; }
    var self = this;
    function frame() {
      if (!self.orbiting) { self.edgeLoop = null; return; }
      self.drawEdges();
      self.edgeLoop = requestAnimationFrame(frame);
    }
    this.edgeLoop = requestAnimationFrame(frame);
  };

  GoogleZombieMap.prototype.stopEdges = function () {
    if (this.edgeLoop) { cancelAnimationFrame(this.edgeLoop); }
    this.edgeLoop = null;
    this.edgeNodes.forEach(function (node) { node.root.hidden = true; });
  };

  GoogleZombieMap.prototype.edgeNode = function (index) {
    if (this.edgeNodes[index]) { return this.edgeNodes[index]; }
    var root = document.createElement("div");
    root.className = "zombie-edge";
    var badge = document.createElement("span");
    badge.className = "zombie-edge-badge";
    var arrow = document.createElement("span");
    arrow.className = "zombie-edge-arrow";
    badge.append(arrow, iconImage("zombie"));
    var label = document.createElement("span");
    label.className = "zombie-edge-km";
    root.append(badge, label);
    this.edges.append(root);
    this.edgeNodes[index] = { root: root, arrow: arrow, label: label };
    return this.edgeNodes[index];
  };

  /* 地図の座標 → 画面の座標（px）。3D表示があれば、その正確な投影を使う。
     なければ真上から見た近似（傾きは無視する）。カメラの後ろなら null */
  GoogleZombieMap.prototype.projector = function (width, height) {
    if (this.view.projector) { return this.view.projector; }
    var center = this.map.getCenter();
    var zoom = this.map.getZoom();
    if (!center || typeof zoom !== "number") { return null; }
    var origin = { lat: center.lat(), lng: center.lng() };
    var metersPerPx = (156543.03392 * Math.cos((origin.lat * Math.PI) / 180)) / Math.pow(2, zoom);
    var rad = ((this.map.getHeading() || 0) * Math.PI) / 180;
    return function (point) {
      var d = offsetMeters(origin, point);
      var right = d.east * Math.cos(rad) - d.north * Math.sin(rad);
      var up = d.east * Math.sin(rad) + d.north * Math.cos(rad);
      return { x: width / 2 + right / metersPerPx, y: height / 2 - up / metersPerPx };
    };
  };

  GoogleZombieMap.prototype.drawEdges = function () {
    var points = this.latest;
    var zombies = points ? zombiesOf(points) : [];
    var width = this.el.clientWidth;
    var height = this.el.clientHeight;
    var project = points && hasPoint(points.player) && width && height ? this.projector(width, height) : null;
    if (!project) { zombies = []; }

    // 矢印を置く範囲。上下は HUD に重ならないようにする
    var insets = this.view.edgeInsets || { top: 0, bottom: 0 };
    var box = {
      left: EDGE_MARGIN_PX,
      right: width - EDGE_MARGIN_PX,
      top: Math.min(height / 2, insets.top + EDGE_MARGIN_PX),
      bottom: Math.max(height / 2, height - insets.bottom - EDGE_MARGIN_PX),
    };
    var heading = this.map.getHeading() || 0;
    var origin = zombies.length ? project(points.player) : null;
    if (!origin) { origin = { x: width / 2, y: height / 2 }; }
    origin = { x: clamp(origin.x, box.left, box.right), y: clamp(origin.y, box.top, box.bottom) };

    zombies.forEach(function (zombie, index) {
      var node = this.edgeNode(index);
      var screen = project(zombie);
      var onScreen = screen && screen.x >= box.left && screen.x <= box.right && screen.y >= box.top && screen.y <= box.bottom;
      node.root.hidden = Boolean(onScreen);
      if (onScreen) { return; }
      // 向きは方位から決める（カメラの後ろにいても正しい向きになる）
      var angle = ((bearingDeg(points.player, zombie) - heading) * Math.PI) / 180;
      var dx = Math.sin(angle);
      var dy = -Math.cos(angle);
      var reach = Infinity;
      if (dx > 1e-6) { reach = Math.min(reach, (box.right - origin.x) / dx); }
      if (dx < -1e-6) { reach = Math.min(reach, (box.left - origin.x) / dx); }
      if (dy > 1e-6) { reach = Math.min(reach, (box.bottom - origin.y) / dy); }
      if (dy < -1e-6) { reach = Math.min(reach, (box.top - origin.y) / dy); }
      if (!isFinite(reach)) { reach = 0; }
      var meters = metersBetween(points.player, zombie);
      node.root.style.transform = "translate(" + (origin.x + dx * reach) + "px, " + (origin.y + dy * reach) + "px) translate(-50%, -50%)";
      node.arrow.style.transform = "rotate(" + (angle * 180) / Math.PI + "deg)";
      node.label.textContent = formatDistance(meters);
      node.root.classList.toggle("is-near", meters < EDGE_DANGER_M);
    }, this);
    this.edgeNodes.forEach(function (node, index) { if (index >= zombies.length) { node.root.hidden = true; } });
  };

  /* 3Dのモデル（map3d.js）で描くときは、2Dのマーカーを隠す */
  GoogleZombieMap.prototype.hideMarkers = function (hidden) {
    this.markersHidden = hidden;
    var map = hidden ? null : this.map;
    Object.keys(this.markers).forEach(function (key) { this.markers[key].map = map; }, this);
  };

  GoogleZombieMap.prototype.marker = function (key, kind, position) {
    if (this.markers[key]) {
      this.markers[key].position = position;
      this.markers[key].map = this.markersHidden ? null : this.map;
      return;
    }
    var icon = document.createElement("span");
    icon.className = "map-icon " + kind;
    icon.append(iconImage(kind));
    this.markers[key] = new this.Marker({
      map: this.markersHidden ? null : this.map, position: position, title: LABELS[kind], content: icon,
    });
  };

  GoogleZombieMap.prototype.update = function (points) {
    this.latest = points;
    var places = placesOf(points);
    var seen = {};
    places.forEach(function (place) {
      var position = { lat: place.point.lat, lng: place.point.lng };
      this.marker(place.key, place.kind, position);
      seen[place.key] = true;
    }, this);
    // 拾ったアイテムや、いなくなったゾンビの印は消す
    Object.keys(this.markers).forEach(function (key) {
      if (!seen[key]) { this.markers[key].map = null; }
    }, this);

    this.setOrbit(Boolean(this.view.following));
    if (this.view.following) {
      this.followCamera();
      return;
    }
    // 操作中に勝手に動かないよう、表示する種類が増えたときだけ全体を収める
    var main = places.filter(function (p) { return p.kind === "shelter" || p.kind === "player" || p.kind === "zombie"; });
    var signature = main.map(function (p) { return p.key; }).join();
    if (signature === this.fittedKinds || main.length === 0) { return; }
    this.fittedKinds = signature;
    if (main.length === 1) {
      this.map.setCenter(main[0].point);
      return;
    }
    var bounds = new this.LatLngBounds();
    main.forEach(function (p) { bounds.extend(p.point); });
    this.map.fitBounds(bounds, 48);
  };

  /* --- 読み込み --- */
  /* 1つのページで読み込むのは1回だけ。preload で先に始めておけば、create はその続きを待つだけになる */
  var loading = null;
  function loadGoogleMaps(key) {
    if (!loading) { loading = startLoading(key); }
    return loading;
  }

  function startLoading(key) {
    return new Promise(function (resolve, reject) {
      window.__zombieMapsReady = resolve;
      window.gm_authFailure = function () {
        if (window.__zombieMapsAuthFailed) { window.__zombieMapsAuthFailed(); }
        reject(new Error("auth"));
      };
      var script = document.createElement("script");
      script.async = true;
      script.src = "https://maps.googleapis.com/maps/api/js?key=" + encodeURIComponent(key) +
        "&loading=async&callback=__zombieMapsReady";
      script.onerror = function () { reject(new Error("load")); };
      document.head.append(script);
    });
  }

  window.ZombieMap = {
    /* ページを開いてすぐ、データを待たずに Google マップのスクリプトを読み込み始める */
    preload: function (key) { if (key) { loadGoogleMaps(key).catch(function () {}); } },
    /* setFollow(true) で追従モード、onFollowChange(関数) で追従の停止と再開を受け取る */
    create: function (el, config) {
      var current = new Radar(el);
      var latest = null;
      var latestRoute = null;
      var listeners = [];
      var view = {
        following: false,
        // 追従中のカメラ。向きは最初に決まる（シェルターの方角）
        camera: { heading: null, tilt: FOLLOW_TILT, zoom: FOLLOW_ZOOM },
        projector: null,
        edgeInsets: { top: 0, bottom: 0 },
        pauseFollow: function () {
          if (!view.wantFollow || !view.following) { return; }
          view.following = false;
          listeners.forEach(function (fn) { fn(false); });
        },
      };
      var api = {
        update: function (points) {
          latest = points;
          current.update(points);
        },
        setFollow: function (on) {
          view.wantFollow = on;
          view.following = on;
          listeners.forEach(function (fn) { fn(on); });
          if (latest) { current.update(latest); }
        },
        /* 互換のため残す。カメラは歩く向きでは回さず、指で回す */
        setHeading: function () {},
        /* 真上から見る ⇄ 傾けて見る。真上になったら true を返す */
        toggleTopView: function () {
          view.camera.tilt = view.camera.tilt > 1 ? 0 : FOLLOW_TILT;
          if (current instanceof GoogleZombieMap) { current.queueCamera(); }
          return view.camera.tilt === 0;
        },
        /* 端の矢印を置かない上下の幅（px）。HUD の高さを渡す */
        setEdgeInsets: function (insets) { view.edgeInsets = insets; },
        /* 3D表示（map3d.js）が、画面上の正確な位置を計算する関数を渡す。やめたら null */
        setProjector: function (fn) { view.projector = fn; },
        /* シェルターまでの徒歩ルート。[{lat, lng}, ...] を渡す */
        setRoute: function (route) {
          latestRoute = route;
          current.setRoute(route);
          if (latest) { current.update(latest); }
        },
        /* ⑥で追従モードを頼まれているか（指で止めている間も true） */
        followRequested: function () { return Boolean(view.wantFollow); },
        onFollowChange: function (fn) { listeners.push(fn); },
        /* 3D表示（map3d.js）が地図そのものを使うための入口。Google マップが読めたら呼ばれる */
        onGoogleMap: function (fn) {
          if (current instanceof GoogleZombieMap) { fn(current.map); } else { view.mapReady = fn; }
        },
        /* 3Dのモデルで描くときに2Dのマーカーを隠す。3Dをやめたら戻す */
        hideMarkers: function (hidden) {
          if (current instanceof GoogleZombieMap) { current.hideMarkers(hidden); }
        },
      };
      if (!config.key) { return api; }
      // 地図が描けるまで真っ黒にならないよう、読み込み中と出す
      el.classList.add("is-loading");

      function fallBack() {
        el.classList.remove("is-loading");
        if (current instanceof GoogleZombieMap) { current.setOrbit(false); }
        current = new Radar(el);
        current.setRoute(latestRoute);
        if (latest) { current.update(latest); }
      }
      window.__zombieMapsAuthFailed = fallBack;

      loadGoogleMaps(config.key)
        .then(function () {
          return Promise.all([
            google.maps.importLibrary("maps"),
            google.maps.importLibrary("marker"),
            google.maps.importLibrary("core"),
          ]);
        })
        .then(function (libs) {
          el.classList.remove("radar");
          el.textContent = "";
          current = new GoogleZombieMap(el, { maps: libs[0], marker: libs[1], core: libs[2] }, config.mapId, view);
          current.setRoute(latestRoute);
          var loaded = current.map.addListener("tilesloaded", function () {
            el.classList.remove("is-loading");
            loaded.remove();
          });
          if (latest) { current.update(latest); }
          if (view.mapReady) { view.mapReady(current.map); }
        })
        .catch(function () { el.classList.remove("is-loading"); /* レーダーのまま続ける */ });
      return api;
    },
  };
})();
