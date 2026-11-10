/* 3Dモデル。外部の素材は使わず、three.js の基本図形を組み合わせたローポリで作る（著作権の確認が不要）。
   どのモデルも y が上、1単位を約1mとして作る。 */
import * as THREE from "./vendor/three/three.module.js";

export { THREE };

export const COLORS = {
  accent: 0xc6f135, danger: 0xff4136, warn: 0xffc53d,
  ground: 0x1a201c, dirt: 0x2b2a22, wood: 0x7a5534, woodDark: 0x4e3622, plank: 0x9a7048,
  tarp: 0xd0692c, canvas: 0x8d8a62, metal: 0x5e6862, concrete: 0x7d817a, concreteDark: 0x575c57,
  roof: 0x3c2f2a, window: 0xffd27a, skin: 0x7fa36a, rag: 0x3d4a3f, ragDark: 0x2a2f2a,
  suit: 0x2f7fd6, pack: 0xc6f135, ice: 0x7fd8ff,
};

const materials = new Map();

/* 同じ色の素材は使い回す。flatShading でローポリらしく見せる */
export function mat(color, options = {}) {
  const key = color + JSON.stringify(options);
  if (!materials.has(key)) {
    materials.set(key, new THREE.MeshStandardMaterial({ color, flatShading: true, roughness: 0.9, metalness: 0, ...options }));
  }
  return materials.get(key);
}

function glow(color, intensity = 1.6) {
  return mat(color, { emissive: color, emissiveIntensity: intensity, roughness: 0.5 });
}

function mesh(geometry, material, x = 0, y = 0, z = 0) {
  const m = new THREE.Mesh(geometry, material);
  m.position.set(x, y, z);
  m.castShadow = true;
  m.receiveShadow = true;
  return m;
}

function box(w, h, d, material, x = 0, y = 0, z = 0) {
  return mesh(new THREE.BoxGeometry(w, h, d), material, x, y + h / 2, z);
}

function cylinder(r, h, material, x = 0, y = 0, z = 0, segments = 7) {
  return mesh(new THREE.CylinderGeometry(r, r, h, segments), material, x, y + h / 2, z);
}

/* 四角錐の屋根。幅 w、奥行き d の箱に載る */
function roof(w, d, h, material, y) {
  const m = mesh(new THREE.ConeGeometry(Math.SQRT1_2, 1, 4), material, 0, y + h / 2, 0);
  m.rotation.y = Math.PI / 4;
  m.scale.set(w * 1.15, h, d * 1.15);
  return m;
}

/* --- 小物 --- */

function crate(x, z, size = 0.6, rotation = 0) {
  const c = box(size, size, size, mat(COLORS.plank), x, 0, z);
  c.rotation.y = rotation;
  return c;
}

function barrel(x, z) {
  const group = new THREE.Group();
  group.add(cylinder(0.32, 0.9, mat(COLORS.warn), 0, 0, 0, 8));
  group.add(cylinder(0.33, 0.12, mat(0x15130a), 0, 0.3, 0, 8));
  group.add(cylinder(0.33, 0.12, mat(0x15130a), 0, 0.6, 0, 8));
  group.position.set(x, 0, z);
  return group;
}

function campfire(x, z) {
  const group = new THREE.Group();
  for (let i = 0; i < 3; i += 1) {
    const log = mesh(new THREE.CylinderGeometry(0.07, 0.07, 0.8, 5), mat(COLORS.woodDark), 0, 0.08, 0);
    log.rotation.z = Math.PI / 2;
    log.rotation.y = (i * Math.PI) / 3;
    group.add(log);
  }
  const flame = mesh(new THREE.ConeGeometry(0.22, 0.6, 5), glow(COLORS.warn, 2.2), 0, 0.38, 0);
  flame.name = "flame";
  group.add(flame);
  const light = new THREE.PointLight(COLORS.warn, 6, 6, 1.6);
  light.position.y = 0.6;
  light.name = "flame-light";
  group.add(light);
  group.position.set(x, 0, z);
  return group;
}

function lamp(x, z, height = 2.2) {
  const group = new THREE.Group();
  group.add(cylinder(0.05, height, mat(COLORS.metal), 0, 0, 0, 5));
  group.add(box(0.3, 0.16, 0.3, glow(COLORS.window, 2), 0, height, 0));
  const light = new THREE.PointLight(COLORS.window, 5, 7, 1.6);
  light.position.y = height;
  group.add(light);
  group.position.set(x, 0, z);
  return group;
}

/* 円周に沿って物を並べる。手前（+z）の gapAngle の範囲は入口として空ける。
   各品は x 方向が円の接線、+z が円の内側を向く。傾けるときは rotation.x を使う（負で外向き） */
function ring(radius, count, make, gapAngle = 0.5) {
  const group = new THREE.Group();
  for (let i = 0; i < count; i += 1) {
    const angle = (i / count) * Math.PI * 2;
    if (Math.abs(Math.atan2(Math.sin(angle - Math.PI / 2), Math.cos(angle - Math.PI / 2))) < gapAngle) { continue; }
    const item = make(i);
    item.position.set(Math.cos(angle) * radius, item.position.y, Math.sin(angle) * radius);
    item.rotation.order = "YXZ";  // 自分の傾きを先に付けてから、円に沿って向きを回す
    item.rotation.y = -angle - Math.PI / 2;
    group.add(item);
  }
  return group;
}

function house(w, h, d, wall, roofColor) {
  const group = new THREE.Group();
  group.add(box(w, h, d, mat(wall)));
  group.add(roof(w, d, h * 0.6, mat(roofColor), h));
  group.add(box(0.5, 0.9, 0.05, mat(COLORS.woodDark), 0, 0, d / 2 + 0.01));
  group.add(box(0.4, 0.35, 0.05, glow(COLORS.window, 1.4), w * 0.28, h * 0.5, d / 2 + 0.01));
  group.add(box(0.4, 0.35, 0.05, glow(COLORS.window, 1.4), -w * 0.28, h * 0.5, d / 2 + 0.01));
  return group;
}

function watchtower(x, z, height = 3.2) {
  const group = new THREE.Group();
  const legs = [[-0.5, -0.5], [0.5, -0.5], [-0.5, 0.5], [0.5, 0.5]];
  legs.forEach(([lx, lz]) => group.add(box(0.14, height, 0.14, mat(COLORS.woodDark), lx, 0, lz)));
  group.add(box(1.5, 0.12, 1.5, mat(COLORS.plank), 0, height, 0));
  group.add(box(1.5, 0.5, 0.08, mat(COLORS.wood), 0, height + 0.12, 0.71));
  group.add(box(1.5, 0.5, 0.08, mat(COLORS.wood), 0, height + 0.12, -0.71));
  group.add(roof(1.4, 1.4, 0.7, mat(COLORS.roof), height + 1.25));
  [[-0.65, -0.65], [0.65, -0.65], [-0.65, 0.65], [0.65, 0.65]].forEach(([px, pz]) => {
    group.add(box(0.08, 1.25, 0.08, mat(COLORS.woodDark), px, height + 0.12, pz));
  });
  const light = new THREE.SpotLight(COLORS.window, 30, 14, 0.5, 0.6, 1.4);
  light.position.set(0, height + 0.8, 0);
  light.target.position.set(0, 0, 6);
  light.name = "searchlight";
  group.add(light, light.target);
  group.add(box(0.3, 0.3, 0.3, glow(COLORS.window, 2.4), 0, height + 0.62, 0.3));
  group.position.set(x, 0, z);
  return group;
}

function flag(x, z, height = 2.6) {
  const group = new THREE.Group();
  group.add(cylinder(0.04, height, mat(COLORS.metal), 0, 0, 0, 5));
  const cloth = box(0.9, 0.5, 0.03, mat(COLORS.accent, { emissive: COLORS.accent, emissiveIntensity: 0.35 }), 0.45, height - 0.55, 0);
  cloth.name = "flag";
  group.add(cloth);
  group.position.set(x, 0, z);
  return group;
}

/* --- 拠点の10段階 --- */

function tent() {
  const group = new THREE.Group();
  const shell = mesh(new THREE.ConeGeometry(1.3, 1.5, 4), mat(COLORS.tarp), 0, 0.75, 0);
  shell.rotation.y = Math.PI / 4;
  group.add(shell);
  group.add(box(0.5, 0.7, 0.05, mat(0x2a1a10), 0, 0, 0.62));
  group.add(campfire(1.7, 1.1));
  group.add(crate(-1.5, 0.6, 0.55, 0.3));
  group.add(crate(-1.3, 1.2, 0.4, 0.9));
  return group;
}

function hut() {
  const group = new THREE.Group();
  group.add(house(2.2, 1.5, 1.8, COLORS.wood, COLORS.roof));
  group.add(campfire(2.1, 1.4));
  group.add(crate(-1.7, 1.1, 0.55));
  group.add(crate(-1.6, 1.7, 0.45, 0.6));
  group.add(lamp(1.4, 1.3, 1.6));
  return group;
}

function barricadeHouse() {
  const group = hut();
  const planks = ring(3.1, 22, (i) => {
    const plank = box(0.55, 1.1 + (i % 3) * 0.15, 0.12, mat(i % 2 ? COLORS.plank : COLORS.wood));
    plank.rotation.z = ((i % 5) - 2) * 0.06;
    return plank;
  });
  group.add(planks);
  const spikes = ring(3.6, 16, () => {
    const spike = mesh(new THREE.ConeGeometry(0.1, 1.1, 5), mat(COLORS.woodDark), 0, 0.35, 0);
    spike.rotation.x = -0.9;  // 外へ向けて突き出す
    return spike;
  }, 0.6);
  group.add(spikes);
  group.add(barrel(2.3, -1.6));
  group.add(barrel(-2.3, -1.4));
  return group;
}

function outpost() {
  const group = new THREE.Group();
  group.add(house(2.4, 1.7, 2.0, COLORS.wood, COLORS.roof));
  const wall = ring(3.6, 34, (i) => {
    const log = cylinder(0.17, 1.8 + (i % 2) * 0.25, mat(i % 2 ? COLORS.wood : COLORS.woodDark), 0, 0, 0, 6);
    return log;
  }, 0.45);
  group.add(wall);
  group.add(watchtower(-2.2, -2.0));
  group.add(flag(2.3, -2.2));
  group.add(campfire(1.9, 1.3));
  group.add(barrel(-1.9, 1.4));
  group.add(crate(2.2, 0.1, 0.55, 0.4));
  group.add(lamp(-0.9, 2.6, 2.0));
  group.add(lamp(0.9, 2.6, 2.0));
  return group;
}

function fortress() {
  const group = new THREE.Group();
  // 2階建ての本部
  group.add(box(2.8, 1.6, 2.2, mat(COLORS.concrete)));
  group.add(box(2.2, 1.3, 1.8, mat(COLORS.concreteDark), 0, 1.6, 0));
  [-0.8, 0, 0.8].forEach((x) => group.add(box(0.4, 0.3, 0.05, glow(COLORS.window, 1.4), x, 2.1, 0.91)));
  group.add(box(0.7, 1.0, 0.05, mat(COLORS.metal), 0, 0, 1.11));
  // 屋上のアンテナと赤い灯
  group.add(cylinder(0.04, 1.6, mat(COLORS.metal), 0.6, 2.9, -0.4, 5));
  const beacon = mesh(new THREE.SphereGeometry(0.12, 8, 6), glow(COLORS.danger, 3), 0.6, 4.55, -0.4);
  beacon.name = "beacon";
  group.add(beacon);
  // 太陽光パネル
  [-0.6, -0.1].forEach((x) => {
    const panel = box(0.45, 0.04, 0.8, mat(0x24344a, { metalness: 0.4, roughness: 0.4 }), x, 2.95, 0.3);
    panel.rotation.x = -0.35;
    group.add(panel);
  });
  // 四角い城壁と角の塔
  const size = 4.0;
  const wallMat = mat(COLORS.concreteDark);
  [[0, -size, size * 2, 0], [-size, 0, 0.35, size * 2], [size, 0, 0.35, size * 2]].forEach(([x, z, w, d]) => {
    group.add(box(w || 0.35, 1.5, d || 0.35, wallMat, x, 0, z));
  });
  group.add(box(size * 0.7, 1.5, 0.35, wallMat, -size * 0.65, 0, size));
  group.add(box(size * 0.7, 1.5, 0.35, wallMat, size * 0.65, 0, size));
  // 門の上のハザード模様
  group.add(box(size * 0.6, 0.25, 0.4, mat(COLORS.warn), 0, 1.7, size));
  [[-size, -size], [size, -size], [-size, size], [size, size]].forEach(([x, z]) => {
    group.add(cylinder(0.55, 2.4, mat(COLORS.concrete), x, 0, z, 8));
    for (let i = 0; i < 6; i += 1) {
      const a = (i / 6) * Math.PI * 2;
      group.add(box(0.22, 0.3, 0.22, mat(COLORS.concrete), x + Math.cos(a) * 0.45, 2.4, z + Math.sin(a) * 0.45));
    }
  });
  group.add(watchtower(-size, -size, 3.4));
  group.add(flag(size, -size, 3.6));
  group.add(lamp(-1.2, size - 0.6, 2.2));
  group.add(lamp(1.2, size - 0.6, 2.2));
  group.add(barrel(2.6, 2.4));
  group.add(barrel(2.9, 1.8));
  group.add(crate(-2.6, 2.3, 0.6, 0.2));
  return group;
}

/* 6段階目から。要塞の外にもう一重の城壁を巡らせ、段階ごとに設備を足していく。
   外側の城壁は±5。地面に収まるよう、buildBase で全体を少し縮める */
const OUTER = 5;

function doubleWall() {
  const group = fortress();
  const wallMat = mat(COLORS.concrete);
  // 奥と左右は一続き、手前は門を空ける
  group.add(box(OUTER * 2, 1.1, 0.3, wallMat, 0, 0, -OUTER));
  group.add(box(0.3, 1.1, OUTER * 2, wallMat, -OUTER, 0, 0));
  group.add(box(0.3, 1.1, OUTER * 2, wallMat, OUTER, 0, 0));
  group.add(box(OUTER * 0.75, 1.1, 0.3, wallMat, -OUTER * 0.625, 0, OUTER));
  group.add(box(OUTER * 0.75, 1.1, 0.3, wallMat, OUTER * 0.625, 0, OUTER));
  // 外壁の上の有刺鉄線（細い棒を並べる）
  for (let i = -4; i <= 4; i += 1) {
    group.add(box(0.05, 0.35, 0.05, mat(COLORS.metal), i * 1.1, 1.1, -OUTER));
    group.add(box(0.05, 0.35, 0.05, mat(COLORS.metal), -OUTER, 1.1, i * 1.1));
    group.add(box(0.05, 0.35, 0.05, mat(COLORS.metal), OUTER, 1.1, i * 1.1));
  }
  group.add(box(OUTER * 2, 0.04, 0.04, mat(COLORS.metal), 0, 1.4, -OUTER));
  group.add(box(0.04, 0.04, OUTER * 2, mat(COLORS.metal), -OUTER, 1.4, 0));
  group.add(box(0.04, 0.04, OUTER * 2, mat(COLORS.metal), OUTER, 1.4, 0));
  group.add(lamp(-OUTER * 0.25, OUTER + 0.5, 2.0));
  group.add(lamp(OUTER * 0.25, OUTER + 0.5, 2.0));
  return group;
}

function windTurbine(x, z, height = 5) {
  const group = new THREE.Group();
  group.add(cylinder(0.09, height, mat(0xd9ddd6), 0, 0, 0, 6));
  group.add(box(0.3, 0.3, 0.5, mat(0xd9ddd6), 0, height - 0.15, 0));
  const rotor = new THREE.Group();
  rotor.position.set(0, height, 0.3);
  rotor.name = "rotor";
  for (let i = 0; i < 3; i += 1) {
    const blade = box(0.14, 1.6, 0.04, mat(0xeef0ea), 0, 0, 0);
    const arm = new THREE.Group();
    arm.rotation.z = (i * Math.PI * 2) / 3;
    arm.add(blade);
    rotor.add(arm);
  }
  group.add(rotor);
  group.position.set(x, 0, z);
  return group;
}

function generator(x, z) {
  const group = new THREE.Group();
  group.add(box(1.0, 0.8, 0.7, mat(COLORS.warn)));
  group.add(box(1.02, 0.12, 0.72, mat(0x15130a), 0, 0.55, 0));
  group.add(box(0.25, 0.18, 0.05, glow(COLORS.accent, 2), 0.25, 0.35, 0.36));
  group.add(cylinder(0.07, 0.6, mat(COLORS.metal), -0.3, 0.8, -0.15, 5));
  group.position.set(x, 0, z);
  return group;
}

function powerFortress() {
  const group = doubleWall();
  group.add(windTurbine(2.7, -2.5));
  group.add(generator(-2.8, 0));
  return group;
}

function lookoutFortress() {
  const group = powerFortress();
  group.add(watchtower(-OUTER, -OUTER, 4.4));
  group.add(watchtower(OUTER, -OUTER, 4.4));
  return group;
}

function greenhouse(x, z) {
  const group = new THREE.Group();
  group.add(box(1.5, 1.0, 1.1, mat(0x9fe0a8, { transparent: true, opacity: 0.35, roughness: 0.2 })));
  group.add(roof(1.5, 1.1, 0.4, mat(0x9fe0a8, { transparent: true, opacity: 0.35, roughness: 0.2 }), 1.0));
  [-0.45, 0, 0.45].forEach((px) => {
    [-0.25, 0.25].forEach((pz) => {
      group.add(mesh(new THREE.ConeGeometry(0.14, 0.4, 5), mat(0x4f9a3c), px, 0.2, pz));
    });
  });
  group.add(box(0.3, 0.2, 0.05, glow(0xb6ff8a, 1.2), 0, 0.75, 0.56));
  group.position.set(x, 0, z);
  return group;
}

function gardenCitadel() {
  const group = lookoutFortress();
  group.add(greenhouse(-2.7, -2.5));
  return group;
}

function lighthouse(x, z, height = 5.5) {
  const group = new THREE.Group();
  for (let i = 0; i < 4; i += 1) {
    const r = 0.5 - i * 0.06;
    group.add(cylinder(r, height / 4, mat(i % 2 ? COLORS.danger : 0xeef0ea), 0, (height / 4) * i, 0, 10));
  }
  group.add(cylinder(0.42, 0.5, glow(COLORS.window, 2.6), 0, height, 0, 10));
  group.add(mesh(new THREE.ConeGeometry(0.48, 0.5, 10), mat(COLORS.roof), 0, height + 0.75, 0));
  const top = mesh(new THREE.SphereGeometry(0.14, 8, 6), glow(COLORS.window, 3.5), 0, height + 1.1, 0);
  top.name = "beacon";
  group.add(top);
  const light = new THREE.PointLight(COLORS.window, 12, 14, 1.4);
  light.position.y = height + 0.25;
  group.add(light);
  group.position.set(x, 0, z);
  return group;
}

function citadelCity() {
  const group = gardenCitadel();
  group.add(lighthouse(2.8, 0.2));
  group.add(flag(-OUTER, OUTER, 3.2));
  group.add(flag(OUTER, OUTER, 3.2));
  return group;
}

const BUILDERS = [
  tent, hut, barricadeHouse, outpost, fortress, doubleWall, powerFortress, lookoutFortress, gardenCitadel, citadelCity,
];
const BIG_BASE_FROM_RANK = 6;  // 外側の城壁がある段階。地面に収まるよう縮める
const BIG_BASE_SCALE = 0.85;

/* ランク（1〜10）の拠点 */
export function buildBase(rank) {
  const index = Math.max(1, Math.min(rank, BUILDERS.length)) - 1;
  let group = BUILDERS[index]();
  if (index + 1 >= BIG_BASE_FROM_RANK) {
    // 建つときの演出で外側の scale を書き換えるので、縮めるのは内側の入れ物にする
    const outer = new THREE.Group();
    group.scale.setScalar(BIG_BASE_SCALE);
    outer.add(group);
    group = outer;
  }
  group.name = "base";
  return group;
}

/* 崩れた拠点のがれき。捕まった直後に、前のランクの跡として散らす */
export function buildRubble(rank, random = Math.random) {
  const group = new THREE.Group();
  const count = 10 + rank * 6;
  const materialsForRank = [COLORS.plank, COLORS.wood, COLORS.woodDark, rank >= 5 ? COLORS.concrete : COLORS.tarp];
  for (let i = 0; i < count; i += 1) {
    const r = 1.6 + random() * (1.2 + rank * 0.5);
    const a = random() * Math.PI * 2;
    const piece = box(0.2 + random() * 0.6, 0.1 + random() * 0.25, 0.2 + random() * 0.5, mat(materialsForRank[i % materialsForRank.length]),
      Math.cos(a) * r, 0, Math.sin(a) * r);
    piece.rotation.set(random() * 0.6, random() * Math.PI, random() * 0.6);
    group.add(piece);
  }
  group.name = "rubble";
  return group;
}

/* --- 人物 --- */

export function buildZombie(seed = 0) {
  const group = new THREE.Group();
  const skin = mat(COLORS.skin);
  const rag = mat(seed % 2 ? COLORS.rag : COLORS.ragDark);
  group.add(box(0.18, 0.75, 0.2, mat(COLORS.ragDark), -0.13, 0, 0));
  group.add(box(0.18, 0.7, 0.2, mat(COLORS.ragDark), 0.13, 0, 0.05));
  const torso = box(0.55, 0.7, 0.3, rag, 0, 0.72, 0);
  torso.rotation.x = 0.18;
  group.add(torso);
  const head = box(0.36, 0.36, 0.34, skin, 0.03, 1.45, 0.08);
  head.rotation.z = 0.25;
  group.add(head);
  group.add(box(0.07, 0.05, 0.02, glow(COLORS.danger, 2.5), -0.06, 1.62, 0.26));
  group.add(box(0.07, 0.05, 0.02, glow(COLORS.danger, 2.5), 0.11, 1.64, 0.26));
  // 前へ突き出した腕
  [-0.36, 0.36].forEach((x, i) => {
    const arm = box(0.14, 0.14, 0.62, skin, x, 1.2 - i * 0.06, 0.32);
    arm.rotation.x = -0.15 + i * 0.12;
    group.add(arm);
  });
  group.name = "zombie";
  return group;
}

/* ハードのゾンビ。背が高く骨ばった体で、前のめりに走ってくる。肋骨がのぞき、裂けた口と目が赤く光る。
   右腕でつかみかかり、左腕は取れかけて筋だけでぶら下がっている。
   足元に赤黒いもやを出し、遠くからでも危ないと分かるようにする。関節ごとに動かす（sprint） */
export function buildRunner(seed = 0) {
  const group = new THREE.Group();
  const skin = mat(0x6f8f5c);
  const bone = mat(0xd8d2b8);
  const rag = mat(seed % 2 ? 0x2a1f1c : 0x1e1a18);
  const blood = mat(0x4a0606);
  const rig = {};

  // 脚。腰の関節から下へぶら下げる
  [["legL", -0.13], ["legR", 0.13]].forEach(([name, x]) => {
    const pivot = new THREE.Group();
    pivot.position.set(x, 0.98, 0);
    pivot.add(box(0.13, 0.98, 0.15, rag, 0, -0.98, 0));
    group.add(pivot);
    rig[name] = pivot;
  });

  // 胴。腰を軸に前へ倒す
  const torso = new THREE.Group();
  torso.position.set(0, 0.98, 0);
  torso.add(box(0.42, 0.78, 0.26, rag, 0, 0, 0));
  // 破れた服からのぞく肋骨
  for (let i = 0; i < 4; i += 1) {
    torso.add(box(0.26 - i * 0.03, 0.04, 0.02, bone, 0, 0.3 + i * 0.1, 0.135));
  }
  torso.add(box(0.12, 0.2, 0.02, blood, 0.12, 0.12, 0.135));
  group.add(torso);
  rig.torso = torso;

  // 頭。裂けた口と、強く光る目
  const head = new THREE.Group();
  head.position.set(0, 0.8, 0.04);
  head.add(box(0.32, 0.34, 0.32, skin, 0, 0, 0.04));
  head.add(box(0.08, 0.06, 0.02, glow(COLORS.danger, 4.5), -0.08, 0.2, 0.205));
  head.add(box(0.08, 0.06, 0.02, glow(COLORS.danger, 4.5), 0.08, 0.21, 0.205));
  head.add(box(0.2, 0.07, 0.02, glow(0xff2a1a, 2.2), 0, 0.05, 0.205));  // 口の中
  const jaw = box(0.24, 0.07, 0.22, blood, 0, -0.06, 0.1);
  jaw.rotation.x = 0.45;  // あごが外れたように開く
  head.add(jaw);
  torso.add(head);
  rig.head = head;

  // 右腕。普通の長さで、ひじを曲げて前へつかみかかる。先に爪
  const armR = new THREE.Group();
  armR.position.set(0.28, 0.7, 0);
  armR.add(box(0.1, 0.34, 0.1, skin, 0, -0.34, 0));
  const forearmR = new THREE.Group();
  forearmR.position.set(0, -0.34, 0);
  forearmR.add(box(0.09, 0.32, 0.09, skin, 0, -0.32, 0));
  [-0.03, 0.03].forEach((cx) => {
    const claw = mesh(new THREE.ConeGeometry(0.022, 0.1, 4), bone, cx, -0.37, 0);
    claw.rotation.x = Math.PI;
    forearmR.add(claw);
  });
  armR.add(forearmR);
  torso.add(armR);
  rig.armR = armR;
  rig.forearmR = forearmR;

  // 左腕は取れかけ。肩は血まみれで骨が突き出し、腕は肩から少し離れた所に筋だけでぶら下がっている
  torso.add(box(0.13, 0.12, 0.14, blood, -0.27, 0.62, 0));
  const stump = cylinder(0.025, 0.12, bone, -0.3, 0.6, 0.02, 5);
  stump.rotation.z = 0.5;
  torso.add(stump);
  const armL = new THREE.Group();
  armL.position.set(-0.33, 0.6, 0);  // 肩より少し下で外側。付け根が外れている
  armL.add(box(0.025, 0.12, 0.025, blood, 0, -0.1, 0));  // 腕をつないでいる筋
  armL.add(box(0.1, 0.32, 0.1, skin, 0, -0.42, 0));
  armL.add(box(0.11, 0.05, 0.11, blood, 0, -0.12, 0));  // ちぎれかけた切り口
  const forearmL = new THREE.Group();
  forearmL.position.set(0, -0.42, 0);
  forearmL.add(box(0.09, 0.3, 0.09, skin, 0, -0.3, 0));
  forearmL.rotation.x = 0.5;  // 変な向きに折れ曲がっている
  forearmL.rotation.z = -0.3;
  armL.add(forearmL);
  torso.add(armL);
  rig.armL = armL;

  // 足元の赤黒いもや
  const mist = new THREE.Group();
  [[0.9, 0.32], [1.4, 0.16]].forEach(([r, opacity]) => {
    const ring = new THREE.Mesh(
      new THREE.CircleGeometry(r, 24),
      new THREE.MeshBasicMaterial({ color: 0x8a0000, transparent: true, opacity, depthWrite: false }),
    );
    ring.rotation.x = -Math.PI / 2;
    ring.position.y = 0.02;
    mist.add(ring);
  });
  group.add(mist);
  rig.mist = mist;

  group.userData.rig = rig;
  group.name = "runner";
  return group;
}

/* ハードのゾンビの走り方。前のめりで脚を大きく振る。右腕はつかみかかり、取れかけの左腕はぶらぶら揺れる。
   ときどき首がカクッと動く */
export function sprint(runner, t, phase = 0) {
  const rig = runner.userData.rig;
  if (!rig) { shamble(runner, t, phase); return; }
  const c = t * 11 + phase;
  rig.legL.rotation.x = Math.sin(c) * 0.9;
  rig.legR.rotation.x = -Math.sin(c) * 0.9;
  // 右腕はつかみかかり、取れかけの左腕は力なくぶらぶら揺れる（体の動きから少し遅れる）
  // 胴が0.65ほど前に倒れているので、腕の角度はその分を差し引いて考える（-0.65 で真下にぶら下がる）
  rig.armR.rotation.x = -1.6 + Math.sin(c) * 0.3;
  rig.forearmR.rotation.x = -0.5 + Math.sin(c + 0.6) * 0.2;
  rig.armL.rotation.x = -0.6 + Math.sin(c - 1.4) * 0.45;
  rig.armL.rotation.z = -0.25 + Math.sin(c * 0.5 + phase) * 0.15;
  rig.torso.rotation.x = 0.65 + Math.sin(c * 2) * 0.05;
  const snap = Math.sin(t * 1.7 + phase * 3) > 0.93 ? 1 : 0;
  rig.head.rotation.y = snap * 0.6;
  rig.head.rotation.z = snap * -0.35 + Math.sin(c) * 0.06;
  runner.position.y = Math.abs(Math.sin(c)) * 0.08;
  rig.mist.scale.setScalar(1 + Math.sin(t * 3 + phase) * 0.12);
}

/* --- 自分のアバター。関節ごとに動く人型で、服と肌の色を選べる --- */

/* 色を少し暗くする（ズボンや帽子に使う） */
function shade(color, factor) {
  const c = new THREE.Color(color);
  return c.multiplyScalar(factor).getHex();
}

/* 関節から下へぶら下がる部品。関節を回すと部品が振れる */
function limb(w, h, d, material, y = 0) {
  return mesh(new THREE.BoxGeometry(w, h, d), material, 0, y - h / 2, 0);
}

function joint(x, y, z) {
  const group = new THREE.Group();
  group.position.set(x, y, z);
  return group;
}

export const AVATAR_DEFAULT = { outfit: COLORS.accent, skin: 0xe3b48f };

export function buildAvatar({ outfit = AVATAR_DEFAULT.outfit, skin = AVATAR_DEFAULT.skin } = {}) {
  const root = new THREE.Group();
  root.name = "player";
  const shirt = mat(outfit);
  const pants = mat(shade(outfit, 0.35));
  const skinMat = mat(skin);
  const shoe = mat(0x1b1f1c);

  const body = new THREE.Group();  // 全身の上下の揺れ
  root.add(body);
  const hips = joint(0, 0.95, 0);
  body.add(hips);

  // 脚: 股関節 → 太もも → 膝 → すね → 靴
  const legs = [-1, 1].map((side) => {
    const hip = joint(side * 0.12, 0, 0);
    hips.add(hip);
    hip.add(limb(0.18, 0.46, 0.2, pants));
    const knee = joint(0, -0.46, 0);
    hip.add(knee);
    knee.add(limb(0.16, 0.42, 0.18, pants));
    knee.add(mesh(new THREE.BoxGeometry(0.18, 0.1, 0.3), shoe, 0, -0.45, 0.05));
    return { hip, knee, side };
  });

  // 胴: 前傾できるよう腰から上をまとめる
  const chest = joint(0, 0, 0);
  hips.add(chest);
  chest.add(mesh(new THREE.BoxGeometry(0.52, 0.6, 0.3), shirt, 0, 0.3, 0));
  chest.add(mesh(new THREE.BoxGeometry(0.4, 0.42, 0.18), glow(COLORS.accent, 0.5), 0, 0.33, -0.23));  // リュック
  chest.add(mesh(new THREE.BoxGeometry(0.54, 0.08, 0.32), pants, 0, 0.04, 0));  // ベルト

  // 頭: 首 → 頭 → 帽子と目
  const neck = joint(0, 0.62, 0);
  chest.add(neck);
  neck.add(mesh(new THREE.BoxGeometry(0.34, 0.34, 0.32), skinMat, 0, 0.2, 0));
  neck.add(mesh(new THREE.BoxGeometry(0.38, 0.12, 0.36), mat(shade(outfit, 0.6)), 0, 0.4, 0));
  neck.add(mesh(new THREE.BoxGeometry(0.36, 0.04, 0.16), mat(shade(outfit, 0.6)), 0, 0.35, 0.22));  // つば
  [-0.08, 0.08].forEach((x) => neck.add(mesh(new THREE.BoxGeometry(0.05, 0.05, 0.02), mat(0x111411), x, 0.22, 0.165)));

  // 腕: 肩 → 上腕 → 肘 → 前腕
  const arms = [-1, 1].map((side) => {
    const shoulder = joint(side * 0.33, 0.56, 0);
    chest.add(shoulder);
    shoulder.add(limb(0.14, 0.32, 0.15, shirt));
    const elbow = joint(0, -0.32, 0);
    shoulder.add(elbow);
    elbow.add(limb(0.12, 0.3, 0.13, skinMat));
    return { shoulder, elbow, side };
  });

  // 足元の目印の輪
  const halo = mesh(new THREE.RingGeometry(0.55, 0.75, 24), glow(COLORS.accent, 1.2), 0, 0.02, 0);
  halo.rotation.x = -Math.PI / 2;
  halo.castShadow = false;
  root.add(halo);

  root.userData.rig = { body, hips, chest, neck, legs, arms, swing: 0, lean: 0, cadence: 0, phase: 0 };
  return root;
}

/* 互換のため。外のマップの古い呼び出し */
export function buildPlayer(colors) {
  return buildAvatar(colors);
}

/* 動き。速さ（m/秒）で、止まる・歩く・走るを切り替える。dt は前のフレームからの秒数 */
export const WALK_MPS = 0.5;
export const RUN_MPS = 2.4;

export function animateAvatar(avatar, speedMps, t, dt = 1 / 60) {
  const rig = avatar.userData.rig;
  if (!rig) { return; }
  const running = speedMps >= RUN_MPS;
  const walking = speedMps >= WALK_MPS;
  const target = running ? { swing: 1.0, lean: 0.28, cadence: 10 } : walking ? { swing: 0.55, lean: 0.08, cadence: 6.5 } : { swing: 0, lean: 0, cadence: 0 };
  // 急に切り替わらないよう、なめらかに近づける
  const blend = 1 - Math.exp(-dt * 6);
  rig.swing += (target.swing - rig.swing) * blend;
  rig.lean += (target.lean - rig.lean) * blend;
  rig.cadence += (target.cadence - rig.cadence) * blend;
  rig.phase += rig.cadence * dt;

  const s = rig.swing;
  rig.legs.forEach(({ hip, knee, side }) => {
    const swing = Math.sin(rig.phase + (side > 0 ? Math.PI : 0));
    hip.rotation.x = -swing * 0.8 * s;
    knee.rotation.x = Math.max(0, swing) * 1.3 * s;  // 後ろへ振ったときに膝を曲げる
  });
  rig.arms.forEach(({ shoulder, elbow, side }) => {
    const swing = Math.sin(rig.phase + (side > 0 ? 0 : Math.PI));
    shoulder.rotation.x = -swing * 0.9 * s;
    shoulder.rotation.z = side * (0.06 + 0.1 * s);
    elbow.rotation.x = -(0.15 + 1.1 * s);
  });
  rig.chest.rotation.x = rig.lean;
  // 止まっているときは呼吸の揺れ、動いているときは一歩ごとに上下する
  rig.body.position.y = s > 0.05 ? Math.abs(Math.sin(rig.phase)) * 0.07 * s : Math.sin(t * 2) * 0.012;
  rig.neck.rotation.y = s > 0.05 ? 0 : Math.sin(t * 0.7) * 0.25;  // 待機中はきょろきょろする
}

/* シェルターの目印。光の柱で遠くからも分かるようにする */
export function buildShelterBeacon() {
  const group = new THREE.Group();
  group.add(cylinder(0.9, 0.3, mat(COLORS.concrete), 0, 0, 0, 8));
  const dome = mesh(new THREE.SphereGeometry(0.75, 10, 6, 0, Math.PI * 2, 0, Math.PI / 2), mat(COLORS.accent, { emissive: COLORS.accent, emissiveIntensity: 0.5 }), 0, 0.3, 0);
  group.add(dome);
  const pillar = new THREE.Mesh(
    new THREE.CylinderGeometry(0.35, 0.6, 14, 12, 1, true),
    new THREE.MeshBasicMaterial({ color: COLORS.accent, transparent: true, opacity: 0.28, side: THREE.DoubleSide, depthWrite: false }),
  );
  pillar.position.y = 7.3;
  pillar.name = "pillar";
  group.add(pillar);
  group.name = "shelter";
  return group;
}

/* アイテム。資材は目印の付いた木箱、フリーズは水色に光る氷の結晶。ゆっくり回して浮かせる */
export function buildItem(kind) {
  const group = new THREE.Group();
  const spinner = new THREE.Group();
  spinner.name = "spinner";
  if (kind === "freeze") {
    const crystal = mesh(new THREE.OctahedronGeometry(0.55, 0), glow(COLORS.ice, 1.3), 0, 0.85, 0);
    crystal.scale.y = 1.5;
    spinner.add(crystal);
  } else {
    spinner.add(box(0.8, 0.8, 0.8, mat(COLORS.plank), 0, 0.3, 0));
    spinner.add(box(0.82, 0.12, 0.82, glow(COLORS.accent, 1.2), 0, 0.64, 0));
  }
  group.add(spinner);
  // 足元の光の輪で、遠くからも拾える場所が分かるようにする
  const ring = mesh(new THREE.RingGeometry(0.7, 0.9, 24), glow(kind === "freeze" ? COLORS.ice : COLORS.accent, 1.5), 0, 0.03, 0);
  ring.rotation.x = -Math.PI / 2;
  ring.castShadow = false;
  group.add(ring);
  group.name = "item-" + kind;
  return group;
}

export function spinItem(item, t, phase = 0) {
  const spinner = item.getObjectByName("spinner");
  if (!spinner) { return; }
  spinner.rotation.y = t * 1.4 + phase;
  spinner.position.y = 0.15 + Math.sin(t * 2.2 + phase) * 0.12;
}

/* ゾンビの揺れ歩き。t は秒 */
export function shamble(zombie, t, phase = 0) {
  zombie.rotation.z = Math.sin(t * 3.2 + phase) * 0.12;
  zombie.position.y = Math.abs(Math.sin(t * 3.2 + phase)) * 0.06;
}

/* WebGL が使えるか */
export function webglAvailable() {
  try {
    const canvas = document.createElement("canvas");
    return Boolean(window.WebGL2RenderingContext && canvas.getContext("webgl2")) || Boolean(canvas.getContext("webgl"));
  } catch (e) {
    return false;
  }
}
