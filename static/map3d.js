/* ⑥外のマップの3D表示。Google マップの WebGLOverlayView に three.js を載せ、
   自分のアバター、ゾンビ（ハードは3体）、アイテム、シェルターの光の柱を3Dで描く。
   ベクター地図でないと使えないので、使えるときだけ2Dのマーカーと入れ替え、使えなければ画面に知らせる。 */
import {
  THREE, animateAvatar, buildAvatar, buildItem, buildRunner, buildShelterBeacon, buildZombie, shamble, spinItem, sprint,
  webglAvailable,
} from "./models3d.js";

/* 大きさ（倍率）。実寸だと小さすぎるので大きく描く。地図の上の大きさとして固定し、地図と一緒に拡大・縮小する。
   引きすぎて見失わないよう、基準の視点（⑥を開いたとき：ズーム19・最大の傾き）での見た目の4分の1より小さくはしない */
const SCALE_AT_ZOOM_19 = { avatar: 12, zombie: 12, item: 8, beacon: 5 };
const EARTH_CIRCUMFERENCE_M = 40075016.686;
const REFERENCE_ZOOM = 19;
const REFERENCE_TILT = 67.5;
const MIN_SCREEN_RATIO = 0.25;  // ズーム17あたりから、これ以上は縮めない
const METERS_PER_DEGREE_LAT = 110574;
const METERS_PER_DEGREE_LNG = 111320;
/* 捕まったときの演出（escape.js が "zombie-capture" を送る）。時間は秒 */
const CAPTURE_EATERS = 3;  // ノーマルの1体でも、群がって食べるよう3体にする
const CAPTURE_RUSH_S = 0.6;  // ゾンビが飛びかかってくるまで
const CAPTURE_FALL_AT_S = 0.35;  // 自分が倒れ始める
const CAPTURE_FALL_S = 0.45;
const CAPTURE_SPLATTER_S = [0.7, 3.0];  // しぶきが飛ぶ間
const CAPTURE_PARTICLES = 48;

function clamp01(x) { return Math.max(0, Math.min(1, x)); }

/* ノーマルはよろよろ歩き、ハードは走る */
function animateZombie(model, t, phase) {
  if (model.userData.rig) { sprint(model, t, phase); } else { shamble(model, t, phase); }
}

function whenOutsideReady(callback) {
  if (window.ZombieOutside) {
    callback(window.ZombieOutside);
  } else {
    document.addEventListener("zombie-map-ready", () => callback(window.ZombieOutside), { once: true });
  }
}

function hasPoint(p) { return p && typeof p.lat === "number" && typeof p.lng === "number"; }

/* 基準点からの東西・南北の距離（m） */
function offsetMeters(anchor, point) {
  return {
    east: (point.lng - anchor.lng) * Math.cos((anchor.lat * Math.PI) / 180) * METERS_PER_DEGREE_LNG,
    north: (point.lat - anchor.lat) * METERS_PER_DEGREE_LAT,
  };
}

/* モデルは y が上で作っているので、地図の座標（x 東、y 北、z 上）に合わせて立てる */
function standUp(model) {
  const holder = new THREE.Group();
  holder.rotation.x = Math.PI / 2;
  holder.add(model);
  return holder;
}

/* 向き。モデルの正面（+z）を、東 east・北 north の方向へ向ける */
function face(model, east, north) {
  if (east === 0 && north === 0) { return; }
  model.rotation.y = Math.atan2(east, -north);
}

/* 方位（北が0度、時計回り）の方向へ向ける */
function faceHeading(model, heading) {
  const rad = (heading * Math.PI) / 180;
  face(model, Math.sin(rad), Math.cos(rad));
}

function attach(googleMap, outside) {
  const { map, points } = outside;
  const maps = window.google && window.google.maps;
  if (!maps || !maps.WebGLOverlayView || !maps.RenderingType) { return; }

  let started = false;
  function check() {
    const type = googleMap.getRenderingType();
    if (type === maps.RenderingType.RASTER) {
      // マップIDがベクターでないと、傾きも3Dも使えない。設定ミスにすぐ気づけるよう画面に出す
      document.dispatchEvent(new CustomEvent("zombie-map-flat"));
      return;
    }
    if (started || type !== maps.RenderingType.VECTOR) { return; }
    started = true;
    start();
  }
  googleMap.addListener("renderingtype_changed", check);
  check();

  function start() {
    const overlay = new maps.WebGLOverlayView();
    let renderer = null;
    let scene;
    let camera;
    const player = { holder: null, model: null, colors: null };
    const shelter = { holder: null };
    const zombieModels = [];  // ゾンビ1体に1つのモデル（ノーマルは1体、ハードは3体）
    const items = new Map();  // アイテムのID → 3Dモデル
    let capture = null;  // 捕まった瞬間の { at, spot, from }。演出の間は、位置の更新を止めて描く
    let particles = [];
    let pool = null;  // 足元に広がる赤い跡
    const startedAt = performance.now();
    let lastFrameAt = startedAt;

    function zombieFor(index) {
      if (!zombieModels[index]) {
        // ハードは、走ってくる怖いゾンビにする
        const model = points.mode === "hard" ? buildRunner(index) : buildZombie(index);
        const holder = standUp(model);
        holder.userData = { phase: index * 1.7 };
        scene.add(holder);
        zombieModels[index] = { holder, model };
      }
      return zombieModels[index];
    }

    function itemFor(item) {
      if (!items.has(item.id)) {
        const holder = standUp(buildItem(item.kind));
        holder.userData = { phase: items.size * 1.3 };
        scene.add(holder);
        items.set(item.id, holder);
      }
      return items.get(item.id);
    }

    /* 自分の色でアバターを作る。色が分かったら作り直す */
    function ensureAvatar() {
      const colors = outside.avatar || null;
      const key = colors ? colors.outfit + ":" + colors.skin : "default";
      if (player.model && player.colors === key) { return; }
      if (player.model) { player.holder.remove(player.model); }
      player.model = buildAvatar(colors || undefined);
      player.holder.add(player.model);
      player.colors = key;
    }

    /* 捕まった瞬間の位置で止め、ゾンビのいた方角を覚える */
    document.addEventListener("zombie-capture", () => {
      if (capture || !hasPoint(points.player)) { return; }
      const spot = { lat: points.player.lat, lng: points.player.lng };
      const from = (points.zombies || []).filter(hasPoint).map((z) => offsetMeters(spot, z));
      capture = { at: performance.now(), spot, from };
      overlay.requestRedraw();
    });

    function makeParticles(scale) {
      const red = new THREE.MeshBasicMaterial({ color: 0xa00000 });
      const dark = new THREE.MeshBasicMaterial({ color: 0x5a0000 });
      particles = [];
      for (let i = 0; i < CAPTURE_PARTICLES; i += 1) {
        const piece = new THREE.Mesh(new THREE.BoxGeometry(1, 1, 1), i % 3 ? red : dark);
        piece.visible = false;
        piece.userData = { life: 0, vx: 0, vy: 0, vz: 0, size: scale * (0.05 + Math.random() * 0.05) };
        scene.add(piece);
        particles.push(piece);
      }
      pool = new THREE.Mesh(
        new THREE.CircleGeometry(1, 24),
        new THREE.MeshBasicMaterial({ color: 0x4a0000, transparent: true, opacity: 0.85 }),
      );
      pool.position.z = 0.05;
      pool.scale.setScalar(0.001);
      scene.add(pool);
    }

    /* 食べられる演出。自分が倒れ、ゾンビが周りから飛びかかって前かがみになり、赤いしぶきが飛ぶ */
    function drawCapture(scaleOf, t, dt) {
      const e = (performance.now() - capture.at) / 1000;
      const avatarScale = scaleOf("avatar");
      const zombieScale = scaleOf("zombie");
      if (!particles.length) { makeParticles(avatarScale); }

      ensureAvatar();
      player.holder.visible = true;
      player.holder.position.set(0, 0, 0);
      player.holder.scale.setScalar(avatarScale);
      player.model.rotation.order = "YXZ";  // 向いている方へ倒す（後ろに倒れる）
      player.model.rotation.x = -1.45 * clamp01((e - CAPTURE_FALL_AT_S) / CAPTURE_FALL_S);
      animateAvatar(player.model, 0, t, dt);

      const ring = 0.5 * zombieScale;
      const base = capture.from.length ? Math.atan2(capture.from[0].north, capture.from[0].east) : 0;
      const rush = 1 - Math.pow(1 - clamp01(e / CAPTURE_RUSH_S), 3);
      const crouch = clamp01((e - CAPTURE_RUSH_S) / 0.3);
      zombieModels.forEach(({ holder }) => { holder.visible = false; });
      for (let i = 0; i < CAPTURE_EATERS; i += 1) {
        const { holder, model } = zombieFor(i);
        const angle = base + (i * Math.PI * 2) / CAPTURE_EATERS;
        const end = { east: Math.cos(angle) * ring, north: Math.sin(angle) * ring };
        // 遠くにいた分は、画面に入る近さから飛びかからせる
        let from = capture.from[i] || { east: end.east * 4, north: end.north * 4 };
        const far = Math.hypot(from.east, from.north);
        const limit = 3 * zombieScale;
        if (far > limit) { from = { east: (from.east / far) * limit, north: (from.north / far) * limit }; }
        const east = from.east + (end.east - from.east) * rush;
        const north = from.north + (end.north - from.north) * rush;
        holder.visible = true;
        holder.position.set(east, north, 0);
        holder.scale.setScalar(zombieScale);
        model.rotation.order = "YXZ";
        face(model, -east, -north);
        if (crouch > 0) {
          // 前かがみになり、むさぼるように頭を動かす
          model.rotation.x = crouch * (0.8 + Math.sin(t * 13 + i * 2.1) * 0.2);
          model.rotation.z = Math.sin(t * 7 + i) * 0.08;
          model.position.y = -0.25 * crouch;
        } else {
          model.rotation.x = 0;
          animateZombie(model, t, i * 1.7);
        }
      }

      // しぶき
      const spawning = e >= CAPTURE_SPLATTER_S[0] && e <= CAPTURE_SPLATTER_S[1];
      particles.forEach((piece) => {
        const d = piece.userData;
        if (d.life <= 0 && spawning && Math.random() < dt * 14) {
          const a = Math.random() * Math.PI * 2;
          const speed = avatarScale * (0.3 + Math.random() * 0.5);
          d.life = 0.5 + Math.random() * 0.4;
          d.vx = Math.cos(a) * speed;
          d.vy = Math.sin(a) * speed;
          d.vz = avatarScale * (0.4 + Math.random() * 0.6);
          piece.position.set(0, 0, avatarScale * 0.3);
          piece.scale.setScalar(d.size);
          piece.visible = true;
        }
        if (d.life > 0) {
          d.life -= dt;
          d.vz -= avatarScale * 2.6 * dt;
          piece.position.x += d.vx * dt;
          piece.position.y += d.vy * dt;
          piece.position.z = Math.max(0.05, piece.position.z + d.vz * dt);
          piece.rotation.x += dt * 8;
          piece.rotation.y += dt * 6;
          if (d.life <= 0) { piece.visible = false; }
        }
      });
      pool.scale.setScalar(Math.max(0.001, avatarScale * 0.9 * clamp01((e - 0.8) / 2)));
    }

    overlay.onAdd = () => {
      scene = new THREE.Scene();
      camera = new THREE.PerspectiveCamera();
      scene.add(new THREE.HemisphereLight(0xe8f2e0, 0x2a332c, 2.4));
      const sun = new THREE.DirectionalLight(0xffffff, 1.8);
      sun.position.set(0.4, -0.8, 1);
      scene.add(sun);
      player.holder = new THREE.Group();
      player.holder.rotation.x = Math.PI / 2;
      shelter.holder = standUp(buildShelterBeacon());
      scene.add(player.holder, shelter.holder);
      ensureAvatar();
    };

    overlay.onContextRestored = ({ gl }) => {
      try {
        renderer = new THREE.WebGLRenderer({ canvas: gl.canvas, context: gl, ...gl.getContextAttributes() });
      } catch (e) {
        // three.js は WebGL2 が必要。使えなければ2Dのマーカーのまま
        renderer = null;
        overlay.setMap(null);
        map.hideMarkers(false);
        return;
      }
      renderer.autoClear = false;
      renderer.autoClearDepth = false;
      renderer.outputColorSpace = THREE.SRGBColorSpace;
    };

    overlay.onContextLost = () => {
      renderer = null;
      setActive(false);
    };

    /* 3Dで描くのは⑥の追従モードだけ。⑤は真上から見る地図なので2Dのマーカーのまま */
    let active = false;
    function setActive(on) {
      if (active === on) { return; }
      active = on;
      map.hideMarkers(on);
      map.setProjector(on ? projectToScreen : null);
    }

    /* 画面の端の矢印（map.js）のため、地図の座標を画面の座標（px）に直す。カメラの後ろなら null */
    let lastMatrix = null;
    let lastAnchor = null;
    const clip = new THREE.Vector4();
    function projectToScreen(point) {
      if (!lastMatrix || !hasPoint(point)) { return null; }
      const offset = offsetMeters(lastAnchor, point);
      clip.set(offset.east, offset.north, 0, 1).applyMatrix4(lastMatrix);
      if (clip.w <= 0) { return null; }
      const div = googleMap.getDiv();
      return {
        x: ((clip.x / clip.w + 1) / 2) * div.clientWidth,
        y: ((1 - clip.y / clip.w) / 2) * div.clientHeight,
      };
    }

    function place(holder, anchor, point, scale) {
      const visible = hasPoint(point);
      holder.visible = visible;
      if (!visible) { return null; }
      const offset = offsetMeters(anchor, point);
      holder.position.set(offset.east, offset.north, 0);
      holder.scale.setScalar(scale);
      return offset;
    }

    /* 引きすぎたときだけ大きくする倍率（ふだんは1）。基準点で「画面の右へ1m」が何pxに映るかをカメラの行列で測り、
       基準の視点で測った px/m の4分の1を下回ったら、その分だけ大きくして画面上の大きさを保つ。
       基準の視点をまだ測れていなければ、ズーム19・真上の px/m を使う */
    const origin = new THREE.Vector4();
    const side = new THREE.Vector4();
    let referencePxPerMeter = null;
    function sizeFactor(matrix, anchor) {
      const div = googleMap.getDiv();
      const rad = ((googleMap.getHeading() || 0) * Math.PI) / 180;
      origin.set(0, 0, 0, 1).applyMatrix4(matrix);
      side.set(Math.cos(rad), -Math.sin(rad), 0, 1).applyMatrix4(matrix);
      const pxPerMeterAt19 = (256 * Math.pow(2, 19)) / (EARTH_CIRCUMFERENCE_M * Math.cos((anchor.lat * Math.PI) / 180));
      let pxPerMeter = 0;
      if (origin.w > 0 && side.w > 0) {
        const dx = ((side.x / side.w - origin.x / origin.w) / 2) * div.clientWidth;
        const dy = ((side.y / side.w - origin.y / origin.w) / 2) * div.clientHeight;
        pxPerMeter = Math.hypot(dx, dy);
      }
      const zoom = googleMap.getZoom() || REFERENCE_ZOOM;
      if (!(pxPerMeter > 0)) {
        // 測れないときはズームから求める（傾きは考えない）
        return Math.max(1, MIN_SCREEN_RATIO * Math.pow(2, REFERENCE_ZOOM - zoom));
      }
      if (referencePxPerMeter === null && Math.abs(zoom - REFERENCE_ZOOM) < 0.05
        && Math.abs((googleMap.getTilt() || 0) - REFERENCE_TILT) < 0.5) {
        referencePxPerMeter = pxPerMeter;
      }
      return Math.max(1, (MIN_SCREEN_RATIO * (referencePxPerMeter || pxPerMeterAt19)) / pxPerMeter);
    }

    overlay.onDraw = ({ transformer }) => {
      if (!renderer) { return; }
      setActive(map.followRequested());
      if (!active) { return; }  // 追従を始めるとカメラが動き、また呼ばれる
      const zombies = (points.zombies || []).filter(hasPoint);
      const anchor = capture ? capture.spot
        : hasPoint(points.player) ? points.player : hasPoint(points.shelter) ? points.shelter : zombies[0];
      if (!hasPoint(anchor)) { overlay.requestRedraw(); return; }
      const now = performance.now();
      const t = (now - startedAt) / 1000;
      const dt = Math.min(0.1, (now - lastFrameAt) / 1000);
      lastFrameAt = now;
      const matrix = new THREE.Matrix4().fromArray(
        transformer.fromLatLngAltitude({ lat: anchor.lat, lng: anchor.lng, altitude: 0 }),
      );
      const growth = sizeFactor(matrix, anchor);
      const scaleOf = (kind) => SCALE_AT_ZOOM_19[kind] * growth;

      if (capture) {
        drawCapture(scaleOf, t, dt);
        place(shelter.holder, anchor, points.shelter, scaleOf("beacon"));
        items.forEach((holder) => { holder.visible = false; });
        camera.projectionMatrix = matrix;
        lastMatrix = matrix;
        lastAnchor = { lat: anchor.lat, lng: anchor.lng };
        renderer.render(scene, camera);
        renderer.resetState();
        overlay.requestRedraw();
        return;
      }

      // 自分: 進む向きを向き、速さで歩く・走るを切り替える
      ensureAvatar();
      const me = place(player.holder, anchor, points.player, scaleOf("avatar"));
      if (me) {
        if (typeof points.heading === "number") {
          faceHeading(player.model, points.heading);
        } else if (hasPoint(points.shelter)) {
          const toShelter = offsetMeters(points.player, points.shelter);
          face(player.model, toShelter.east, toShelter.north);
        }
        animateAvatar(player.model, points.speedMps || 0, t, dt);
      }
      place(shelter.holder, anchor, points.shelter, scaleOf("beacon"));

      // ゾンビは自分（いなければシェルター）へ向かう。位置はサーバーの計算した座標そのもの
      const target = hasPoint(points.player) ? points.player : points.shelter;
      zombieModels.forEach(({ holder }, index) => { if (!zombies[index]) { holder.visible = false; } });
      zombies.forEach((zombie, index) => {
        const { holder, model } = zombieFor(index);
        place(holder, anchor, zombie, scaleOf("zombie"));
        if (hasPoint(target)) {
          const toTarget = offsetMeters(zombie, target);
          face(model, toTarget.east, toTarget.north);
        }
        animateZombie(model, t, holder.userData.phase);
      });

      // 拾ったアイテムは消す
      const shown = new Set();
      (points.items || []).forEach((item) => {
        const holder = itemFor(item);
        place(holder, anchor, item, scaleOf("item"));
        spinItem(holder.children[0], t, holder.userData.phase);
        shown.add(item.id);
      });
      items.forEach((holder, id) => { if (!shown.has(id)) { holder.visible = false; } });

      camera.projectionMatrix = matrix;
      lastMatrix = matrix;
      lastAnchor = { lat: anchor.lat, lng: anchor.lng };
      renderer.render(scene, camera);
      renderer.resetState();
      // 歩き方の揺れを動かし続ける。画面を閉じている間はブラウザが止める
      overlay.requestRedraw();
    };

    overlay.setMap(googleMap);
  }
}

if (webglAvailable()) {
  whenOutsideReady((outside) => outside.map.onGoogleMap((googleMap) => attach(googleMap, outside)));
}
