/* 拠点の3Dジオラマ。地図とは切り離し、家の座標は使わない。
   ゾンビの群れは、残りの距離に比例した位置から迫ってくる。WebGL が使えなければ、テンプレートの絵文字のまま。 */
import {
  THREE, COLORS, mat, animateAvatar, buildAvatar, buildBase, buildRubble, buildZombie, shamble, webglAvailable,
} from "./models3d.js";

const container = document.getElementById("base-scene");
const state = window.ZombieBase;

const GROUND_RADIUS = 11;
const HORDE_NEAR = 4.6;  // ゾンビが拠点に着いたときの距離（拠点の外側）
const HORDE_NEAR_FORTRESS = 6.2;  // 要塞は城壁が広いので、その外側で止める
const HORDE_NEAR_CITADEL = 6.6;  // 6段階目からは外側の城壁の、さらに外で止める
const HORDE_FAR = 10.2;  // 5km先のときの距離（地面の端）
const ZOMBIE_SCALE = 1.4;  // 迫ってくるのは1体。遠くからも見えるよう少し大きく描く
const AUTO_ROTATE_PER_SEC = 0.12;

function seededRandom(seed) {
  let value = seed % 2147483647;
  if (value <= 0) { value += 2147483646; }
  return () => (value = (value * 16807) % 2147483647) / 2147483647;
}

function hashString(text) {
  let hash = 0;
  for (let i = 0; i < text.length; i += 1) { hash = (hash * 31 + text.charCodeAt(i)) | 0; }
  return Math.abs(hash) + 1;
}

function seenRankKey() { return "zombie-base-rank:" + state.data.user_id; }

/* 前に見たときよりランクが上がっていたら、建ち上がる演出をする */
function rankChange() {
  let seen = null;
  try { seen = Number(localStorage.getItem(seenRankKey())) || null; } catch (e) { /* 読めなければ演出なし */ }
  try { localStorage.setItem(seenRankKey(), String(state.data.rank)); } catch (e) { /* なにもしない */ }
  return { rankUp: seen !== null && state.data.rank > seen };
}

function start() {
  if (!container || !state || !webglAvailable()) { return; }
  const reduceMotion = window.matchMedia("(prefers-reduced-motion: reduce)").matches;
  const random = seededRandom(hashString(state.data.user_id));
  const hordeNear = state.data.rank >= 6 ? HORDE_NEAR_CITADEL : state.data.rank >= 5 ? HORDE_NEAR_FORTRESS : HORDE_NEAR;

  let renderer;
  try {
    renderer = new THREE.WebGLRenderer({ antialias: true, alpha: true, powerPreference: "low-power" });
  } catch (e) {
    return;  // 絵文字のまま
  }
  renderer.setPixelRatio(Math.min(window.devicePixelRatio || 1, 2));
  renderer.shadowMap.enabled = true;
  renderer.shadowMap.type = THREE.PCFShadowMap;
  renderer.outputColorSpace = THREE.SRGBColorSpace;
  renderer.toneMapping = THREE.ACESFilmicToneMapping;
  container.textContent = "";
  container.append(renderer.domElement);

  const scene = new THREE.Scene();
  scene.fog = new THREE.Fog(0x060807, 14, 26);

  const camera = new THREE.PerspectiveCamera(38, 4 / 3, 0.1, 100);

  // 夜の明かり。月明かりと、拠点を照らす緑の光
  scene.add(new THREE.HemisphereLight(0x9fb3a0, 0x0b0f0c, 1.1));
  const moon = new THREE.DirectionalLight(0xcfe0ff, 1.8);
  moon.position.set(-6, 12, 5);
  moon.castShadow = true;
  moon.shadow.mapSize.set(1024, 1024);
  Object.assign(moon.shadow.camera, { left: -9, right: 9, top: 9, bottom: -9 });
  scene.add(moon);
  const rim = new THREE.PointLight(COLORS.accent, 12, 18, 1.4);
  rim.position.set(0, 5, 0);
  scene.add(rim);

  // 地面。外側ほど暗くして、霧に溶かす
  const ground = new THREE.Mesh(new THREE.CircleGeometry(GROUND_RADIUS, 48), mat(COLORS.ground));
  ground.rotation.x = -Math.PI / 2;
  ground.receiveShadow = true;
  scene.add(ground);
  const yard = new THREE.Mesh(new THREE.CircleGeometry(4.2, 40), mat(COLORS.dirt));
  yard.rotation.x = -Math.PI / 2;
  yard.position.y = 0.01;
  yard.receiveShadow = true;
  scene.add(yard);
  // 危険ラインの輪（ここまで来たら捕まる）
  const dangerRing = new THREE.Mesh(
    new THREE.RingGeometry(hordeNear - 0.06, hordeNear + 0.06, 64),
    new THREE.MeshBasicMaterial({ color: COLORS.danger, transparent: true, opacity: 0.45 }),
  );
  dangerRing.rotation.x = -Math.PI / 2;
  dangerRing.position.y = 0.02;
  scene.add(dangerRing);
  // 枯れ木と岩を散らす
  for (let i = 0; i < 14; i += 1) {
    const r = 5.6 + random() * 4.5;
    const a = random() * Math.PI * 2;
    const rock = new THREE.Mesh(new THREE.DodecahedronGeometry(0.2 + random() * 0.35, 0), mat(0x3a403b));
    rock.position.set(Math.cos(a) * r, 0.1, Math.sin(a) * r);
    rock.rotation.set(random(), random(), random());
    rock.castShadow = true;
    scene.add(rock);
  }
  for (let i = 0; i < 6; i += 1) {
    const r = 6.5 + random() * 3.5;
    const a = random() * Math.PI * 2;
    const tree = new THREE.Group();
    const trunk = new THREE.Mesh(new THREE.CylinderGeometry(0.07, 0.12, 1.8, 5), mat(COLORS.woodDark));
    trunk.position.y = 0.9;
    tree.add(trunk);
    for (let b = 0; b < 3; b += 1) {
      const branch = new THREE.Mesh(new THREE.CylinderGeometry(0.03, 0.05, 0.8, 4), mat(COLORS.woodDark));
      branch.position.y = 1.1 + b * 0.25;
      branch.rotation.z = (b % 2 ? 1 : -1) * 0.9;
      branch.rotation.y = b * 2;
      tree.add(branch);
    }
    tree.position.set(Math.cos(a) * r, 0, Math.sin(a) * r);
    tree.traverse((o) => { o.castShadow = true; });
    scene.add(tree);
  }

  // 拠点
  const change = rankChange();
  const base = buildBase(state.data.rank);
  scene.add(base);
  let buildProgress = change.rankUp && !reduceMotion ? 0 : 1;
  if (state.data.lost_rank) {
    scene.add(buildRubble(state.data.lost_rank, random));
    if (!reduceMotion) { container.classList.add("is-collapsed"); }
  }

  // 自分のアバター。拠点の手前に立ち、色を変えるとすぐ作り直す
  let avatar = null;
  function placeAvatar(colors) {
    if (avatar) { scene.remove(avatar); }
    avatar = buildAvatar(colors);
    avatar.position.set(-1.0, 0, 2.3);
    avatar.scale.setScalar(1.3);  // ジオラマの中でも見分けやすく
    avatar.rotation.y = 0.35;
    scene.add(avatar);
  }
  placeAvatar(state.data.avatar);
  document.addEventListener("zombie-avatar-change", (event) => placeAvatar(event.detail));

  // 拠点へ迫ってくるゾンビ（1体）。来る方角はユーザーごとに固定する
  const hordeAngle = random() * Math.PI * 2;
  const zombie = buildZombie(0);
  zombie.scale.setScalar(ZOMBIE_SCALE);
  scene.add(zombie);

  function placeHorde(t) {
    const km = state.currentKm();
    const fraction = Math.max(0, Math.min(1, km / state.data.max_km));
    const distance = hordeNear + (HORDE_FAR - hordeNear) * fraction;
    const x = Math.cos(hordeAngle) * distance;
    const z = Math.sin(hordeAngle) * distance;
    zombie.position.x = x;
    zombie.position.z = z;
    zombie.rotation.y = Math.atan2(-x, -z);  // 拠点の方を向く
    if (!reduceMotion) { shamble(zombie, t, 0); }
    // 追いかけている間は拠点にいないので見せない
    zombie.visible = !state.data.chasing && km < state.data.max_km + 0.01;
  }

  // カメラは拠点の周りを回る。指で左右にドラッグして回せる
  let angle = 0.6;
  let dragging = null;
  const canvas = renderer.domElement;
  canvas.addEventListener("pointerdown", (event) => {
    dragging = { x: event.clientX, angle };
    canvas.setPointerCapture(event.pointerId);
  });
  canvas.addEventListener("pointermove", (event) => {
    if (!dragging) { return; }
    angle = dragging.angle - (event.clientX - dragging.x) * 0.01;
  });
  const endDrag = () => { dragging = null; };
  canvas.addEventListener("pointerup", endDrag);
  canvas.addEventListener("pointercancel", endDrag);

  function resize() {
    const width = container.clientWidth;
    const height = container.clientHeight;
    if (!width || !height) { return; }
    renderer.setSize(width, height, false);
    camera.aspect = width / height;
    camera.updateProjectionMatrix();
  }
  new ResizeObserver(resize).observe(container);
  resize();

  const flames = [];
  const beacons = [];
  const flags = [];
  const rotors = [];
  base.traverse((o) => {
    if (o.name === "flame") { flames.push(o); }
    if (o.name === "beacon") { beacons.push(o); }
    if (o.name === "flag") { flags.push(o); }
    if (o.name === "rotor") { rotors.push(o); }
  });

  const startedAt = performance.now();
  let lastAt = startedAt;
  let running = true;
  function frame(now) {
    if (!running) { return; }
    const dt = Math.min((now - lastAt) / 1000, 0.1);
    lastAt = now;
    const t = (now - startedAt) / 1000;
    if (!dragging && !reduceMotion) { angle += AUTO_ROTATE_PER_SEC * dt; }
    const radius = 13 + (state.data.rank >= 6 ? 2.2 : state.data.rank >= 4 ? 1.5 : 0);
    camera.position.set(Math.sin(angle) * radius, 7.2, Math.cos(angle) * radius);
    camera.lookAt(0, 1.1, 0);

    if (buildProgress < 1) {
      buildProgress = Math.min(1, buildProgress + dt / 1.4);
      const ease = 1 - Math.pow(1 - buildProgress, 3);
      base.scale.set(1, Math.max(0.01, ease + Math.sin(ease * Math.PI) * 0.12), 1);
    }
    if (!reduceMotion) {
      flames.forEach((flame, i) => { flame.scale.set(1, 0.8 + Math.abs(Math.sin(t * 9 + i)) * 0.4, 1); });
      beacons.forEach((beacon) => { beacon.visible = Math.sin(t * 4) > -0.2; });
      flags.forEach((flag) => { flag.rotation.y = Math.sin(t * 2.4) * 0.25; });
      rotors.forEach((rotor) => { rotor.rotation.z -= dt * 1.8; });
      dangerRing.material.opacity = 0.3 + Math.abs(Math.sin(t * 2)) * 0.3;
    }
    placeHorde(t);
    if (avatar) { animateAvatar(avatar, 0, t, dt); }
    renderer.render(scene, camera);
    requestAnimationFrame(frame);
  }

  // 画面を閉じている間は止めて、電池を減らさない
  document.addEventListener("visibilitychange", () => {
    if (document.hidden) {
      running = false;
    } else if (!running) {
      running = true;
      lastAt = performance.now();  // 止めていた間の時間で一気に回らないようにする
      requestAnimationFrame(frame);
    }
  });
  requestAnimationFrame(frame);
}

start();
