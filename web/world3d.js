// 3D view of the lookout's world. Reads the same state the 2D map uses (window.__S, window.__MAP).
// Controls: scroll / pinch = zoom, left-drag = pan, right-drag or two-finger drag = rotate.
import * as THREE from "three";
import { MapControls } from "./vendor/MapControls.js";

const K = 3.6;            // vertical exaggeration
const TEX = 32;           // ground texture pixels per tile
const COL = {
  F: "#2b472a", T: "#2b472a", C: "#2b472a", H: "#2b472a", O: "#2b472a",
  M: "#647a42", R: "#8b867c", S: "#e8edef", W: "#2f5a74", G: "#66773f", L: "#6f6342", P: "#998f80",
};

let W, H, elev, tiles, waterH = 0;
const groundRock = { value: 1 }, treeTime = { value: 0 }, windAmp = { value: .3 };
let renderer, scene, camera, controls, sun, hemi, ground, groundTex, baseCanvas, snowCanvas, paintCanvas, water;
let trees = null, treeMeta = [], tower, julian, cabWin, smoke = [], rain, snowP, overlay;
let flames = [], fireLights = [], flameMats = [], smokeMat;
let lastBurnedLen = -1, lastSnow = null, flying = null, labels = {};
const tmpV = new THREE.Vector3();

// ---------- height ----------
function rawElev(gx, gy) {
  gx = Math.max(0, Math.min(W - 1, gx)); gy = Math.max(0, Math.min(H - 1, gy));
  const x0 = Math.floor(gx), y0 = Math.floor(gy), x1 = Math.min(W - 1, x0 + 1), y1 = Math.min(H - 1, y0 + 1);
  const fx = gx - x0, fy = gy - y0;
  const a = elev[y0][x0] * (1 - fx) + elev[y0][x1] * fx, b = elev[y1][x0] * (1 - fx) + elev[y1][x1] * fx;
  return a * (1 - fy) + b * fy;
}
const tileAt = (x, z) => { const tx = Math.floor(x), ty = Math.floor(z); return (ty >= 0 && ty < H && tx >= 0 && tx < W) ? tiles[ty][tx] : " "; };
let waterTiles = [];
function heightAt(x, z) {
  let h = rawElev(x - 0.5, z - 0.5) * K;
  // flatten the land around the lake so the shore slopes gently down to the water
  let d = 99;
  for (const [wx, wz] of waterTiles) { const dd = Math.hypot(x - wx - .5, z - wz - .5); if (dd < d) d = dd; }
  if (d < 5) {
    const f = Math.max(0, Math.min(1, (d - .75) / 4.25)), ease = f * f * (3 - 2 * f);
    const bank = waterH + .05 + (h - waterH) * ease;
    h = d <= .75 ? waterH - .15 - (.75 - d) * .3 : Math.min(h, bank);
  }
  return h;
}

// ---------- ground painting ----------
function paintBase(winter) {
  const c = document.createElement("canvas"); c.width = W * TEX; c.height = H * TEX;
  const g = c.getContext("2d");
  // forest everywhere first, then open ground as overlapping round blobs so edges look natural
  g.fillStyle = winter ? "#c9d3d6" : COL.F; g.fillRect(0, 0, c.width, c.height);
  const rr = mulberry(11);
  for (let y = 0; y < H; y++) for (let x = 0; x < W; x++) {
    const t = tiles[y][x]; if ("FTCHO".includes(t)) continue;
    let col = COL[t] || "#000";
    if (winter) col = t === "W" ? "#d6e4ec" : "#eef2f4";
    g.fillStyle = col; g.beginPath();
    g.arc((x + .5 + (rr() - .5) * .25) * TEX, (y + .5 + (rr() - .5) * .25) * TEX, TEX * (.72 + rr() * .12), 0, 7); g.fill();
  }
  // soft worn verges under roads/trails/creek; they get blurred into the ground below
  for (const [ch, col, wdt] of [["OH", winter ? "#cfc8bd" : "#6f6248", 16], ["T", winter ? "#dcd6cc" : "#5b5a3e", 9], ["C", winter ? "#b9cbd6" : "#2e5a4a", 12]]) {
    g.strokeStyle = col; g.lineWidth = wdt; g.lineCap = "round";
    for (const pts of chains(ch)) { g.beginPath(); pts.forEach(([x, y], i) => i ? g.lineTo(x * TEX, y * TEX) : g.moveTo(x * TEX, y * TEX)); g.stroke(); }
  }
  const b = document.createElement("canvas"); b.width = c.width; b.height = c.height;
  const bg = b.getContext("2d"); bg.filter = "blur(11px)"; bg.drawImage(c, 0, 0); bg.filter = "none";
  // texture noise
  const rnd = mulberry(7);
  for (let i = 0; i < W * H * 22; i++) {
    bg.fillStyle = rnd() < 0.5 ? `rgba(0,0,0,${rnd() * 0.07})` : `rgba(255,255,255,${rnd() * 0.045})`;
    bg.beginPath(); bg.arc(rnd() * b.width, rnd() * b.height, 1 + rnd() * 3.5, 0, 7); bg.fill();
  }
  return b;
}
function hash2(x, y) { const h = Math.sin(x * 127.1 + y * 311.7) * 43758.5453; return h - Math.floor(h); }
function vnoise(x, y) {
  const xi = Math.floor(x), yi = Math.floor(y), xf = x - xi, yf = y - yi;
  const u = xf * xf * (3 - 2 * xf), v = yf * yf * (3 - 2 * yf);
  const a = hash2(xi, yi), b = hash2(xi + 1, yi), c = hash2(xi, yi + 1), d = hash2(xi + 1, yi + 1);
  return a + (b - a) * u + (c - a) * v + (a - b - c + d) * u * v;
}
function fbm(x, y) { let s = 0, a = .5, f = 1; for (let i = 0; i < 5; i++) { s += a * vnoise(x * f, y * f); a *= .5; f *= 2.03; } return s; }
const smooth = (a, b, x) => { const t = Math.max(0, Math.min(1, (x - a) / (b - a))); return t * t * (3 - 2 * t); };

// Turn tiles of one kind (road, trail, creek) into smooth continuous lines
function chains(chars) {
  const isP = (x, y) => y >= 0 && y < H && x >= 0 && x < W && chars.includes(tiles[y][x]);
  const key = (x, y) => y * W + x, adj = new Map();
  for (let y = 0; y < H; y++) for (let x = 0; x < W; x++) {
    if (!isP(x, y)) continue;
    const list = [];
    for (let dy = -1; dy <= 1; dy++) for (let dx = -1; dx <= 1; dx++) {
      if (!dx && !dy) continue;
      if (!isP(x + dx, y + dy)) continue;
      if (dx && dy && (isP(x + dx, y) || isP(x, y + dy))) continue;
      list.push(key(x + dx, y + dy));
    }
    adj.set(key(x, y), list);
  }
  const seen = new Set(), ek = (a, b) => a < b ? a + "_" + b : b + "_" + a, out = [];
  const walk = (s0, n0) => {
    const pts = [s0, n0]; seen.add(ek(s0, n0)); let prev = s0, cur = n0;
    while (adj.get(cur).length === 2) {
      const nxt = adj.get(cur).find(k => k !== prev);
      if (nxt === undefined || seen.has(ek(cur, nxt))) break;
      seen.add(ek(cur, nxt)); pts.push(nxt); prev = cur; cur = nxt;
    }
    return pts;
  };
  for (const [k, l] of adj) if (l.length !== 2) for (const n of l) if (!seen.has(ek(k, n))) out.push(walk(k, n));
  for (const [k, l] of adj) for (const n of l) if (!seen.has(ek(k, n))) out.push(walk(k, n));
  return out.map(p => {
    let pts = p.map(k => [k % W + .5, Math.floor(k / W) + .5]);
    // paths that leave the map run off the edge instead of stopping short
    for (const end of [0, 1]) {
      const q = end ? pts[pts.length - 1] : pts[0];
      if (q[0] < 1 || q[0] > W - 1 || q[1] < 1 || q[1] > H - 1) {
        const e = [q[0] < 1 ? -0.5 : q[0] > W - 1 ? W + .5 : q[0], q[1] < 1 ? -0.5 : q[1] > H - 1 ? H + .5 : q[1]];
        if (end) pts.push(e); else pts.unshift(e);
      }
    }
    for (let it = 0; it < 3; it++) {  // Chaikin smoothing: rounds corners into curves
      const n = [pts[0]];
      for (let i = 0; i < pts.length - 1; i++) {
        const [ax, ay] = pts[i], [bx, by] = pts[i + 1];
        n.push([ax * .75 + bx * .25, ay * .75 + by * .25], [ax * .25 + bx * .75, ay * .25 + by * .75]);
      }
      n.push(pts[pts.length - 1]); pts = n;
    }
    return pts;
  });
}

function mulberry(a) { return () => { a |= 0; a = a + 0x6D2B79F5 | 0; let t = Math.imul(a ^ a >>> 15, 1 | a); t = t + Math.imul(t ^ t >>> 7, 61 | t) ^ t; return ((t ^ t >>> 14) >>> 0) / 4294967296; }; }

function repaintGround(S) {
  const src = S.snow ? snowCanvas : baseCanvas;
  const g = paintCanvas.getContext("2d");
  g.drawImage(src, 0, 0);
  g.fillStyle = S.snow ? "rgba(70,66,62,.35)" : "rgba(28,24,21,.8)";
  for (const k of S.burned) {
    const [x, y] = k.split(",").map(Number);
    g.beginPath(); g.arc((x + .5) * TEX, (y + .5) * TEX, TEX * .62, 0, 7); g.fill();
  }
  groundTex.needsUpdate = true;
}

// ---------- scene ----------
function buildTerrain() {
  const geo = new THREE.PlaneGeometry(W, H, W * 4, H * 4);
  geo.rotateX(-Math.PI / 2); geo.translate(W / 2, 0, H / 2);
  const pos = geo.attributes.position;
  for (let i = 0; i < pos.count; i++) pos.setY(i, heightAt(pos.getX(i), pos.getZ(i)));
  geo.computeVertexNormals();
  baseCanvas = paintBase(false); snowCanvas = paintBase(true);
  paintCanvas = document.createElement("canvas"); paintCanvas.width = baseCanvas.width; paintCanvas.height = baseCanvas.height;
  paintCanvas.getContext("2d").drawImage(baseCanvas, 0, 0);
  groundTex = new THREE.CanvasTexture(paintCanvas);
  groundTex.colorSpace = THREE.SRGBColorSpace; groundTex.anisotropy = 4;
  const gm = new THREE.MeshStandardMaterial({ map: groundTex, roughness: .96 });
  gm.onBeforeCompile = sh => {
    sh.uniforms.uRock = groundRock;
    sh.vertexShader = "varying vec3 vWPos; varying vec3 vWN;\n" + sh.vertexShader.replace("#include <begin_vertex>",
      "#include <begin_vertex>\nvWPos = (modelMatrix * vec4(position, 1.0)).xyz; vWN = normalize(mat3(modelMatrix) * normal);");
    sh.fragmentShader = `varying vec3 vWPos; varying vec3 vWN; uniform float uRock;
      float h2(vec2 p){ return fract(sin(dot(p, vec2(127.1, 311.7))) * 43758.5453); }
      float vn(vec2 p){ vec2 i = floor(p), f = fract(p); f = f*f*(3.0-2.0*f);
        return mix(mix(h2(i), h2(i+vec2(1,0)), f.x), mix(h2(i+vec2(0,1)), h2(i+vec2(1,1)), f.x), f.y); }
      ` + sh.fragmentShader.replace("#include <map_fragment>", `#include <map_fragment>
      float slope = 1.0 - clamp(vWN.y, 0.0, 1.0);
      float n = vn(vWPos.xz * 7.0) * .55 + vn(vWPos.xz * 29.0) * .3 + vn(vWPos.xz * 90.0) * .15;
      vec3 rockC = vec3(.30, .29, .27) * (.75 + .5 * n);
      diffuseColor.rgb = mix(diffuseColor.rgb, rockC, smoothstep(.30, .55, slope) * uRock);
      diffuseColor.rgb *= .86 + .28 * n;`);
  };
  ground = new THREE.Mesh(geo, gm);
  ground.receiveShadow = true; ground.castShadow = true;
  scene.add(ground);
}

function pathTexture(kind) {
  const c = document.createElement("canvas"); c.width = 64; c.height = 256; const g = c.getContext("2d"), r = mulberry(kind.length * 31);
  const base = { road: [154, 132, 98], trail: [150, 122, 86], creek: [70, 120, 150] }[kind];
  for (let y = 0; y < 256; y++) for (let x = 0; x < 64; x++) {
    const edge = Math.min(x, 63 - x) / 32;               // 0 at edges, 1 in the middle
    let a = smooth(0, kind === "trail" ? .55 : .35, edge);
    if (kind === "creek") a = smooth(0, .5, edge) * .9;
    const n = (r() - .5) * 26;
    let [R, G, B] = base;
    if (kind === "road" && (Math.abs(x - 20) < 4 || Math.abs(x - 44) < 4)) { R -= 22; G -= 20; B -= 16; }  // wheel ruts
    if (kind === "trail") { R += edge * 16; G += edge * 12; B += edge * 8; }                                // worn centre
    g.fillStyle = `rgba(${R + n | 0},${G + n | 0},${B + n | 0},${a})`; g.fillRect(x, y, 1, 1);
  }
  if (kind !== "creek") for (let i = 0; i < 90; i++) { g.fillStyle = `rgba(${r() < .5 ? "70,62,50" : "200,190,170"},${.25 + r() * .4})`; g.fillRect(8 + r() * 48, r() * 256, 1 + r() * 2, 1 + r() * 2); }
  const t = new THREE.CanvasTexture(c); t.wrapT = THREE.RepeatWrapping; t.colorSpace = THREE.SRGBColorSpace; t.anisotropy = 4;
  return t;
}
let pathMats = {};
function buildPaths() {
  const spec = [["OH", "road", .30, .035], ["T", "trail", .13, .03], ["C", "creek", .2, .02]];
  for (const [ch, kind, width, lift] of spec) {
    const mat = new THREE.MeshStandardMaterial({ map: pathTexture(kind), transparent: true, depthWrite: false, roughness: kind === "creek" ? .15 : .95,
      polygonOffset: true, polygonOffsetFactor: -2, polygonOffsetUnits: -2 });
    pathMats[kind] = mat;
    for (const pts of chains(ch)) {
      // resample so the ribbon hugs the terrain
      const P = [pts[0]];
      for (let i = 1; i < pts.length; i++) {
        const [ax, az] = P[P.length - 1], [bx, bz] = pts[i], d = Math.hypot(bx - ax, bz - az);
        const steps = Math.max(1, Math.ceil(d / .15));
        for (let k = 1; k <= steps; k++) P.push([ax + (bx - ax) * k / steps, az + (bz - az) * k / steps]);
      }
      if (P.length < 2) continue;
      const v = [], uv = [], idx = []; let len = 0;
      for (let i = 0; i < P.length; i++) {
        const [x, z] = P[i], [x0, z0] = P[Math.max(0, i - 1)], [x1, z1] = P[Math.min(P.length - 1, i + 1)];
        let tx = x1 - x0, tz = z1 - z0; const tl = Math.hypot(tx, tz) || 1; tx /= tl; tz /= tl;
        const nx = -tz * width / 2, nz = tx * width / 2;
        if (i) len += Math.hypot(x - P[i - 1][0], z - P[i - 1][1]);
        for (const sgn of [1, -1]) {
          const px = x + nx * sgn, pz = z + nz * sgn;
          v.push(px, Math.max(heightAt(px, pz), heightAt(x, z)) + lift, pz);
          uv.push(sgn > 0 ? 0 : 1, len / (width * 3));
        }
        if (i) { const a = (i - 1) * 2; idx.push(a, a + 1, a + 2, a + 1, a + 3, a + 2); }
      }
      const g = new THREE.BufferGeometry();
      g.setAttribute("position", new THREE.Float32BufferAttribute(v, 3)); g.setAttribute("uv", new THREE.Float32BufferAttribute(uv, 2));
      g.setIndex(idx); g.computeVertexNormals();
      const m = new THREE.Mesh(g, mat); m.receiveShadow = true; m.renderOrder = 1; scene.add(m);
    }
  }
}

// ---------- the world beyond the map: ridges fading into haze ----------
function outerHeight(x, z) {
  const cx = Math.max(.01, Math.min(W - .01, x)), cz = Math.max(.01, Math.min(H - .01, z));
  const out = Math.hypot(x - cx, z - cz);
  if (out === 0) {
    const edgeDist = Math.min(x, z, W - x, H - z);
    return edgeDist < 2.5 ? heightAt(x, z) - .35 : -30;
  }
  const edgeH = heightAt(cx, cz);
  const ridges = Math.pow(fbm(x * .03 + 7, z * .03 + 3), 1.7) * 30 + fbm(x * .12, z * .12) * 2.5;
  return edgeH + (ridges + 2 - edgeH) * smooth(0, 34, out) - .02;
}
function buildOuter() {
  const size = 460, seg = 400;
  const geo = new THREE.PlaneGeometry(size, size, seg, seg);
  geo.rotateX(-Math.PI / 2); geo.translate(W / 2, 0, H / 2);
  const pos = geo.attributes.position, col = [];
  const c = new THREE.Color(), forest = new THREE.Color("#2b472a"), rock = new THREE.Color("#6c6a66"), snow = new THREE.Color("#e9eef2");
  for (let i = 0; i < pos.count; i++) {
    const x = pos.getX(i), z = pos.getZ(i), y = outerHeight(x, z);
    pos.setY(i, y);
    const n = fbm(x * .2, z * .2);
    c.copy(forest).offsetHSL(0, 0, (n - .5) * .06);
    if (y > 13 + n * 4) c.lerp(rock, smooth(13, 17, y - n * 3));
    if (y > 19 + n * 3) c.lerp(snow, smooth(19, 23, y));
    col.push(c.r, c.g, c.b);
  }
  geo.setAttribute("color", new THREE.Float32BufferAttribute(col, 3));
  geo.computeVertexNormals();
  outer = new THREE.Mesh(geo, new THREE.MeshStandardMaterial({ vertexColors: true, roughness: 1 }));
  outer.receiveShadow = true; scene.add(outer);
}
let outer;

function buildWater() {
  let x0 = W, x1 = 0, z0 = H, z1 = 0;
  for (const [x, y] of waterTiles) { x0 = Math.min(x0, x); x1 = Math.max(x1, x + 1); z0 = Math.min(z0, y); z1 = Math.max(z1, y + 1); }
  const g = new THREE.PlaneGeometry(x1 - x0 + 2, z1 - z0 + 2, 1, 1);
  g.rotateX(-Math.PI / 2); g.translate((x0 + x1) / 2, waterH, (z0 + z1) / 2);
  water = new THREE.Mesh(g, new THREE.MeshStandardMaterial({ color: "#3d7396", transparent: true, opacity: .88, roughness: .08, metalness: .1 }));
  water.receiveShadow = true; scene.add(water);
}

function buildTrees() {
  const rnd = mulberry(42);
  const spots = [];
  for (let y = 0; y < H; y++) for (let x = 0; x < W; x++) {
    const t = tiles[y][x];
    const n = t === "F" ? 5 : t === "C" || t === "T" || t === "H" ? 2 : t === "M" ? (rnd() < .25 ? 1 : 0) : t === "R" ? (rnd() < .2 ? 1 : 0) : 0;
    for (let i = 0; i < n; i++) {
      const px = x + .1 + rnd() * .8, pz = y + .1 + rnd() * .8;
      if (tileAt(px, pz) === "W") continue;
      spots.push({ x: px, z: pz, tile: `${x},${y}`, h: .22 + rnd() * .2, r: .07 + rnd() * .05, shade: rnd() });
    }
  }
  const lower = new THREE.ConeGeometry(1, 1, 7), upper = new THREE.ConeGeometry(1, 1, 7), trunk = new THREE.CylinderGeometry(1, 1, 1, 5);
  // a band of forest just outside the map so it doesn't end at a line
  for (let i = 0; i < 26000 && spots.length < 24000; i++) {
    // dense right at the map edge, thinning out with distance
    const side = Math.floor(rnd() * 4), along = rnd(), out = Math.pow(rnd(), 1.8) * 16;
    const x = side === 0 ? along * W : side === 1 ? along * W : side === 2 ? -out : W + out;
    const z = side === 0 ? -out : side === 1 ? H + out : along * H;
    if (out < .05) continue;
    const y = outerHeight(x, z); if (y > 12) continue;
    spots.push({ x, z, y, tile: "outer", h: .24 + rnd() * .22, r: .08 + rnd() * .05, shade: rnd() });
  }
  const mat = new THREE.MeshStandardMaterial({ flatShading: true, roughness: .9 });
  mat.onBeforeCompile = sh => {
    sh.uniforms.uTime = treeTime; sh.uniforms.uWind = windAmp;
    sh.vertexShader = "uniform float uTime; uniform float uWind;\n" + sh.vertexShader.replace("#include <begin_vertex>",
      `#include <begin_vertex>
       #ifdef USE_INSTANCING
       float ph = instanceMatrix[3].x * .7 + instanceMatrix[3].z * .5;
       float k = (position.y + .5);
       transformed.x += sin(uTime * 1.4 + ph) * uWind * k * .35;
       transformed.z += cos(uTime * 1.1 + ph) * uWind * k * .2;
       #endif`);
  };
  const tmat = new THREE.MeshStandardMaterial({ color: "#4a3626", roughness: 1 });
  const N = spots.length;
  trees = { lower: new THREE.InstancedMesh(lower, mat, N), upper: new THREE.InstancedMesh(upper, mat, N), trunk: new THREE.InstancedMesh(trunk, tmat, N) };
  Object.values(trees).forEach(m => { m.castShadow = true; m.receiveShadow = true; });
  treeMeta = spots;
  setTrees(new Set(), false);
  Object.values(trees).forEach(m => scene.add(m));
  const tentM = [new THREE.MeshLambertMaterial({ color: "#d9a441" }), new THREE.MeshLambertMaterial({ color: "#5d86b5" }), new THREE.MeshLambertMaterial({ color: "#b5533c" })];
  const stumpM = new THREE.MeshLambertMaterial({ color: "#6e5638" });
  for (let y = 0; y < H; y++) for (let x = 0; x < W; x++) {
    if (tiles[y][x] === "G") for (let i = 0; i < 2; i++) {
      const px = x + .25 + rnd() * .5, pz = y + .25 + rnd() * .5;
      const t = new THREE.Mesh(new THREE.ConeGeometry(.07, .08, 4), tentM[Math.floor(rnd() * 3)]);
      t.position.set(px, heightAt(px, pz) + .04, pz); t.rotation.y = rnd() * 3; scene.add(t);
    }
    if (tiles[y][x] === "L") for (let i = 0; i < 4; i++) {
      const px = x + .1 + rnd() * .8, pz = y + .1 + rnd() * .8;
      const st = new THREE.Mesh(new THREE.CylinderGeometry(.02, .025, .03, 6), stumpM); st.position.set(px, heightAt(px, pz) + .015, pz); scene.add(st);
    }
  }
}
function setTrees(burned, winter) {
  const m = new THREE.Matrix4(), q = new THREE.Quaternion(), s = new THREE.Vector3(), p = new THREE.Vector3(), c = new THREE.Color();
  treeMeta.forEach((t, i) => {
    const y = t.y !== undefined ? t.y : heightAt(t.x, t.z), dead = burned.has(t.tile);
    const h = dead ? t.h * .8 : t.h, r = dead ? t.r * .35 : t.r;
    p.set(t.x, y + h * .12, t.z); s.set(r * .22, h * .3, r * .22); m.compose(p, q, s); trees.trunk.setMatrixAt(i, m);
    p.set(t.x, y + h * .5, t.z); s.set(r, h * .62, r); m.compose(p, q, s); trees.lower.setMatrixAt(i, m);
    p.set(t.x, y + h * .78, t.z); s.set(r * .68, h * .45, r * .68); m.compose(p, q, s); trees.upper.setMatrixAt(i, m);
    if (dead) c.set("#2a2420");
    else { c.setHSL(.29 + t.shade * .06, .38 + t.shade * .2, .17 + t.shade * .09); if (winter) c.lerp(new THREE.Color("#e9eef0"), .45); }
    trees.lower.setColorAt(i, c);
    if (!dead && winter) c.lerp(new THREE.Color("#ffffff"), .35);
    trees.upper.setColorAt(i, c);
  });
  ["lower", "upper", "trunk"].forEach(k => { trees[k].instanceMatrix.needsUpdate = true; if (trees[k].instanceColor) trees[k].instanceColor.needsUpdate = true; });
}

function buildTower(S) {
  const [tx, ty] = window.__MAP.tower, cx = tx + .5, cz = ty + .5, base = heightAt(cx, cz);
  tower = new THREE.Group(); tower.position.set(cx, base, cz); scene.add(tower);
  const wood = new THREE.MeshLambertMaterial({ color: "#5b4632" }), roofM = new THREE.MeshLambertMaterial({ color: "#3d3027" });
  const legH = .42;
  for (const [dx, dz] of [[-1, -1], [1, -1], [-1, 1], [1, 1]]) {
    const leg = new THREE.Mesh(new THREE.CylinderGeometry(.012, .016, legH, 5), wood);
    leg.position.set(dx * .1, legH / 2, dz * .1); leg.rotation.z = -dx * .1; leg.rotation.x = dz * .1; tower.add(leg);
  }
  for (const yy of [.14, .28]) { const brace = new THREE.Mesh(new THREE.BoxGeometry(.24, .012, .24), wood); brace.position.y = yy; tower.add(brace); }
  const deck = new THREE.Mesh(new THREE.BoxGeometry(.4, .025, .4), wood); deck.position.y = legH; tower.add(deck);
  const cab = new THREE.Mesh(new THREE.BoxGeometry(.26, .05, .26), new THREE.MeshLambertMaterial({ color: "#d8d0bd" })); cab.position.y = legH + .035; tower.add(cab);
  cabWin = new THREE.Mesh(new THREE.BoxGeometry(.265, .11, .265), new THREE.MeshLambertMaterial({ color: "#a9cde0", emissive: "#000000", transparent: true, opacity: .35, depthWrite: false }));
  cabWin.position.y = legH + .115; tower.add(cabWin);
  for (const [dx, dz] of [[-1, -1], [1, -1], [-1, 1], [1, 1]]) { const post = new THREE.Mesh(new THREE.BoxGeometry(.012, .12, .012), wood); post.position.set(dx * .125, legH + .115, dz * .125); tower.add(post); }
  const roof = new THREE.Mesh(new THREE.ConeGeometry(.24, .1, 4), roofM); roof.rotation.y = Math.PI / 4; roof.position.y = legH + .22; tower.add(roof);
  const mast = new THREE.Mesh(new THREE.CylinderGeometry(.004, .004, .14, 4), roofM); mast.position.y = legH + .3; tower.add(mast);
  // summit props
  const L = S.locs, prop = (loc, mesh, dy = 0) => { const [x, z] = L[loc]; mesh.position.set(x, heightAt(x, z) + dy, z); scene.add(mesh); };
  const pile = new THREE.Group();
  for (let i = 0; i < 3; i++) { const l = new THREE.Mesh(new THREE.CylinderGeometry(.018, .018, .16, 6), new THREE.MeshLambertMaterial({ color: "#7a5634" })); l.rotation.z = Math.PI / 2; l.position.set(0, .02 + i * .03, (i % 2) * .02); pile.add(l); }
  prop("woodpile", pile);
  prop("outhouse", new THREE.Mesh(new THREE.BoxGeometry(.07, .12, .07), new THREE.MeshLambertMaterial({ color: "#6a4e33" })), .06);
  prop("cistern", new THREE.Mesh(new THREE.CylinderGeometry(.05, .05, .07, 10), new THREE.MeshLambertMaterial({ color: "#8d9496" })), .035);
  prop("garden", new THREE.Mesh(new THREE.BoxGeometry(.18, .015, .12), new THREE.MeshLambertMaterial({ color: "#5d7a3a" })), .008);
}

function buildJulian() {
  julian = new THREE.Group(); scene.add(julian);
  const m = c => new THREE.MeshLambertMaterial({ color: c });
  const legs = new THREE.Mesh(new THREE.BoxGeometry(.028, .045, .018), m("#3a3f55")); legs.position.y = .0225;
  const body = new THREE.Mesh(new THREE.BoxGeometry(.036, .045, .022), m("#55703d")); body.position.y = .067;
  const head = new THREE.Mesh(new THREE.SphereGeometry(.014, 8, 6), m("#e0b48f")); head.position.y = .103;
  const brim = new THREE.Mesh(new THREE.CylinderGeometry(.024, .024, .004, 10), m("#6b4a2b")); brim.position.y = .115;
  const crown = new THREE.Mesh(new THREE.CylinderGeometry(.012, .014, .014, 10), m("#7d5733")); crown.position.y = .123;
  julian.add(legs, body, head, brim, crown);
  julian.userData.legs = legs;
}

function smokeTexture() {
  const c = document.createElement("canvas"); c.width = c.height = 64; const g = c.getContext("2d");
  const gr = g.createRadialGradient(32, 32, 0, 32, 32, 32); gr.addColorStop(0, "rgba(220,215,208,.9)"); gr.addColorStop(1, "rgba(220,215,208,0)");
  g.fillStyle = gr; g.fillRect(0, 0, 64, 64); return new THREE.CanvasTexture(c);
}
let smokeTex;
function buildWeather() {
  const mk = (n, color, size) => {
    const g = new THREE.BufferGeometry(), p = new Float32Array(n * 3);
    for (let i = 0; i < n; i++) { p[i * 3] = Math.random() * 30 - 15; p[i * 3 + 1] = Math.random() * 10; p[i * 3 + 2] = Math.random() * 30 - 15; }
    g.setAttribute("position", new THREE.BufferAttribute(p, 3));
    const pts = new THREE.Points(g, new THREE.PointsMaterial({ color, size, transparent: true, opacity: .7, depthWrite: false }));
    pts.visible = false; scene.add(pts); return pts;
  };
  rain = mk(2500, "#b9cde6", .035); snowP = mk(2000, "#ffffff", .06);
}

// ---------- sky ----------
let skyDome, skyU, stars, clouds = [];
function buildSky() {
  skyU = { top: { value: new THREE.Color("#4d7fb8") }, horizon: { value: new THREE.Color("#bcd3e6") }, bottom: { value: new THREE.Color("#5a6470") },
           sunDir: { value: new THREE.Vector3(0, 1, 0) }, sunCol: { value: new THREE.Color("#fff2d6") }, sunAmt: { value: 1 } };
  skyDome = new THREE.Mesh(new THREE.SphereGeometry(320, 32, 16), new THREE.ShaderMaterial({
    uniforms: skyU, side: THREE.BackSide, depthWrite: false, fog: false,
    vertexShader: "varying vec3 vP; void main(){ vP = position; gl_Position = projectionMatrix * modelViewMatrix * vec4(position,1.0); }",
    fragmentShader: `uniform vec3 top, horizon, bottom, sunCol, sunDir; uniform float sunAmt; varying vec3 vP;
      void main(){ vec3 d = normalize(vP); float h = d.y;
        vec3 c = h > 0.0 ? mix(horizon, top, pow(smoothstep(0.0, .6, h), .7)) : mix(horizon, bottom, smoothstep(0.0, .15, -h));
        float s = max(dot(d, normalize(sunDir)), 0.0);
        c += sunCol * (pow(s, 900.0) * 3.0 + pow(s, 14.0) * .35) * sunAmt;
        gl_FragColor = vec4(c, 1.0); }`,
  }));
  skyDome.renderOrder = -10; scene.add(skyDome);
  const g = new THREE.BufferGeometry(), p = [];
  for (let i = 0; i < 1600; i++) { const u = Math.random() * Math.PI * 2, v = Math.acos(Math.random() * .95); p.push(Math.sin(v) * Math.cos(u) * 300, Math.cos(v) * 300, Math.sin(v) * Math.sin(u) * 300); }
  g.setAttribute("position", new THREE.Float32BufferAttribute(p, 3));
  stars = new THREE.Points(g, new THREE.PointsMaterial({ color: "#ffffff", size: 1.1, sizeAttenuation: false, transparent: true, opacity: 0, fog: false, depthWrite: false }));
  skyDome.add(stars);
  // soft clouds
  const cc = document.createElement("canvas"); cc.width = cc.height = 128; const cg = cc.getContext("2d");
  for (let i = 0; i < 14; i++) {
    const x = 30 + Math.random() * 68, y = 50 + Math.random() * 30, r = 18 + Math.random() * 22;
    const gr = cg.createRadialGradient(x, y, 0, x, y, r); gr.addColorStop(0, "rgba(255,255,255,.55)"); gr.addColorStop(1, "rgba(255,255,255,0)");
    cg.fillStyle = gr; cg.fillRect(0, 0, 128, 128);
  }
  const ct = new THREE.CanvasTexture(cc);
  for (let i = 0; i < 26; i++) {
    const sp = new THREE.Sprite(new THREE.SpriteMaterial({ map: ct, transparent: true, depthWrite: false, opacity: .8, fog: false }));
    sp.position.set(W / 2 + (Math.random() - .5) * 300, 34 + Math.random() * 14, H / 2 + (Math.random() - .5) * 300);
    sp.scale.set(40 + Math.random() * 40, 18 + Math.random() * 12, 1); scene.add(sp); clouds.push(sp);
  }
}

// ---------- per-frame ----------
function skyState(S) {
  const t = S.tod, r = S.sunrise, s = S.sunset;
  const dayFrac = (t - r) / (s - r);
  const up = dayFrac > 0 && dayFrac < 1 ? Math.sin(Math.PI * dayFrac) : 0;
  const twilight = Math.max(0, 1 - Math.min(Math.abs(t - r), Math.abs(t - s)) / 60);
  return { dayFrac: Math.max(0, Math.min(1, dayFrac)), up, twilight };
}
const C = (a, b, f) => new THREE.Color(a).lerp(new THREE.Color(b), f);

function update(S, now) {
  // snow / burned changes
  if (S.snow !== lastSnow || S.burned.length !== lastBurnedLen) {
    repaintGround(S); setTrees(new Set(S.burned), S.snow);
    water.material.opacity = S.snow ? 1 : .88; water.material.roughness = S.snow ? .8 : .08;
    lastSnow = S.snow; lastBurnedLen = S.burned.length;
  }
  // light & sky
  const sk = skyState(S), wx = S.wx;
  const ang = Math.PI * sk.dayFrac;
  const sunDir = new THREE.Vector3(Math.cos(ang) * .9, Math.max(-.2, Math.sin(ang)) * .75 + .05, -.45).normalize();
  const overcast = /rain|storm|overcast|snow|blizzard|thunder/.test(wx.desc) ? 1 : /partly|cumulus|haz/.test(wx.desc) ? .35 : 0;
  sun.position.copy(controls.target).addScaledVector(sunDir, 80); sun.target.position.copy(controls.target); sun.target.updateMatrixWorld();
  sun.intensity = (0.05 + 3.2 * sk.up) * (1 - overcast * .6);
  sun.color = C("#ff9a55", "#fff4e2", Math.min(1, sk.up * 2.2));
  hemi.intensity = .18 + 1.1 * sk.up * (1 - overcast * .3);
  hemi.color = C("#3a4a7a", "#cfe3ff", sk.up);
  const night = 1 - Math.min(1, sk.up * 4 + sk.twilight * .5);
  const top = C("#060b1c", "#3f73b5", sk.up), hor = C("#141c33", "#c4d7e8", sk.up);
  hor.lerp(new THREE.Color("#f0955f"), sk.twilight * .7); top.lerp(new THREE.Color("#4b3f6e"), sk.twilight * .35);
  if (overcast) { const grey = new THREE.Color(sk.up > .1 ? "#9aa3ab" : "#1a1f27"); top.lerp(grey, overcast * .8); hor.lerp(grey, overcast * .7); }
  skyU.top.value.copy(top); skyU.horizon.value.copy(hor); skyU.bottom.value.copy(hor).multiplyScalar(.6);
  skyU.sunDir.value.copy(sunDir); skyU.sunCol.value.copy(C("#ff8a4a", "#fff2d6", Math.min(1, sk.up * 2))); skyU.sunAmt.value = (1 - overcast * .9) * (sunDir.y > -.05 ? 1 : 0);
  skyDome.position.copy(camera.position);
  stars.material.opacity = night * (1 - overcast);
  scene.fog.color.copy(hor);
  scene.fog.density = wx.fog ? .05 : wx.raining || wx.snowing ? .022 : .0105;
  for (const c of clouds) {
    c.material.opacity = (.35 + overcast * .6) * (1 - night * .6);
    c.material.color.copy(C("#2a3048", "#ffffff", sk.up)).lerp(new THREE.Color("#ffb38a"), sk.twilight * .5);
    c.position.x += Math.sin((wx.wind_from + 180) * Math.PI / 180) * .01 * (1 + wx.wind_mph / 10);
    c.position.z -= Math.cos((wx.wind_from + 180) * Math.PI / 180) * .01 * (1 + wx.wind_mph / 10);
    if (c.position.x > W / 2 + 170) c.position.x -= 340; if (c.position.x < W / 2 - 170) c.position.x += 340;
    if (c.position.z > H / 2 + 170) c.position.z -= 340; if (c.position.z < H / 2 - 170) c.position.z += 340;
  }
  // shadows follow what you're looking at
  const dist = camera.position.distanceTo(controls.target), ext = Math.max(5, Math.min(48, dist * .8));
  const sc = sun.shadow.camera; if (sc.right !== ext) { sc.left = -ext; sc.right = ext; sc.top = ext; sc.bottom = -ext; sc.updateProjectionMatrix(); }
  treeTime.value = now / 1000; windAmp.value = .08 + wx.wind_mph / 40;
  groundRock.value = S.snow ? .45 : 1;
  water.material.color.copy(S.snow ? new THREE.Color("#dce8ef") : C("#1f4a66", "#3d7396", sk.up).lerp(hor, .15));
  const dark = sk.up < .08;
  cabWin.material.emissive.set(dark && (!S.agent.asleep || S.thinking) ? "#ffcc66" : "#000000");
  // Julian
  const a = S.agent, pos = window.__spritePos ? window.__spritePos() : a.pos;
  julian.visible = !a.asleep;
  if (julian.visible) {
    const [tx, ty] = window.__MAP.tower, dTower = Math.hypot(pos[0] - (tx + .5), pos[1] - (ty + .4));
    const inTower = dTower < .55 && (a.loc === "tower_cab" || a.loc === "catwalk");
    julian.position.set(pos[0], inTower ? tower.position.y + .435 : heightAt(pos[0], pos[1]), pos[1]);
    julian.userData.legs.scale.y = a.travel_from ? (1 + Math.sin(now / 120) * .25) : 1;
    if (window.__spritePrevPos) { const pp = window.__spritePrevPos(); const dx = pos[0] - pp[0], dz = pos[1] - pp[1]; if (Math.hypot(dx, dz) > 1e-4) julian.rotation.y = Math.atan2(dx, dz); }
  }
  // fires: flames + smoke + glow (pooled objects; nothing is created per frame)
  let fi = 0, li = 0;
  for (const f of Object.values(S.fires)) {
    for (const [k, cell] of Object.entries(f.cells)) {
      const [x, y] = k.split(",").map(Number), cx = x + .5, cz = y + .5, h = heightAt(cx, cz);
      const show = cell.st === "burn" ? f.visible : window.__god;
      if (!show || fi >= flames.length) continue;
      const fl = flames[fi++]; fl.visible = true;
      const smold = cell.st !== "burn";
      fl.material = smold ? flameMats[2] : flameMats[Math.random() < .5 ? 0 : 1];
      fl.scale.set(smold ? .3 : 1 + Math.random() * .3, smold ? .3 : .8 + Math.random() * .6, smold ? .3 : 1 + Math.random() * .3);
      fl.position.set(cx + (Math.random() - .5) * (smold ? 0 : .3), h + .13, cz + (Math.random() - .5) * (smold ? 0 : .3));
      if (smold) continue;
      if (li < fireLights.length) { const L = fireLights[li++]; L.position.set(cx, h + .5, cz); L.intensity = 1.5 + Math.random(); }
      if (Math.random() < .3 && smoke.length < 260) {
        const sp = new THREE.Sprite(smokeMat.clone());
        sp.position.set(cx + (Math.random() - .5) * .4, h + .3, cz + (Math.random() - .5) * .4); sp.scale.setScalar(.4); sp.userData.life = 1; scene.add(sp); smoke.push(sp);
      }
    }
  }
  for (; fi < flames.length; fi++) flames[fi].visible = false;
  for (; li < fireLights.length; li++) fireLights[li].intensity = 0;
  const to = (wx.wind_from + 180) * Math.PI / 180, ws = .004 + wx.wind_mph * .0006;
  smoke = smoke.filter(sp => {
    sp.position.x += Math.sin(to) * ws * 4; sp.position.z -= Math.cos(to) * ws * 4; sp.position.y += .01;
    sp.scale.multiplyScalar(1.008); sp.userData.life *= .992; sp.material.opacity = .55 * sp.userData.life;
    if (sp.userData.life < .05) { scene.remove(sp); sp.material.dispose(); return false; }
    return true;
  });
  // rain / snow follow the camera target
  for (const [pts, on, speed] of [[rain, wx.raining, .35], [snowP, wx.snowing, .05]]) {
    pts.visible = !!on; if (!on) continue;
    pts.position.set(controls.target.x, controls.target.y, controls.target.z);
    const p = pts.geometry.attributes.position;
    for (let i = 0; i < p.count; i++) { let y = p.getY(i) - speed; if (y < -2) y = 10; p.setY(i, y); if (speed < .1) p.setX(i, p.getX(i) + Math.sin(now / 900 + i) * .003); }
    p.needsUpdate = true;
  }
}

// ---------- labels (HTML on top of the canvas) ----------
function label(id, html, cls) {
  let el = labels[id];
  if (!el) { el = labels[id] = document.createElement("div"); el.className = "lbl3d " + (cls || ""); overlay.appendChild(el); }
  if (el._h !== html) { el.innerHTML = html; el._h = html; }
  el._seen = true; return el;
}
function place(el, x, y, z, dy = 0) {
  tmpV.set(x, y, z).project(camera);
  const vis = tmpV.z < 1 && Math.abs(tmpV.x) < 1.1 && Math.abs(tmpV.y) < 1.1;
  el.style.display = vis ? "" : "none";
  if (vis) el.style.transform = `translate(${(tmpV.x * .5 + .5) * overlay.clientWidth}px, ${(-tmpV.y * .5 + .5) * overlay.clientHeight + dy}px) translate(-50%, -100%)`;
}
function updateLabels(S) {
  Object.values(labels).forEach(el => el._seen = false);
  const esc = window.__esc, a = S.agent, [tx, ty] = window.__MAP.tower;
  const evById = Object.fromEntries(S.events.map(e => [e.id, e]));
  for (const sg of S.sightings) {
    if (sg.status !== "open") continue; const e = evById[sg.event]; if (!e) continue;
    place(label("s" + sg.id, `◎ ${sg.id}${sg.report ? " ✓" : ""}`, "sight"), e.x + .5, heightAt(e.x + .5, e.y + .5) + .5, e.y + .5);
  }
  if (window.__god) for (const e of S.events) place(label("g" + e.id, esc(e.key), "god"), e.x + .5, heightAt(e.x + .5, e.y + .5) + .25, e.y + .5);
  const dist = camera.position.distanceTo(controls.target);
  if (a.asleep) place(label("zzz", "z z z", "zzz"), tower.position.x + .2, tower.position.y + .75, tower.position.z);
  else if (dist < 18) {
    const lastV = S.voice.length ? S.voice[S.voice.length - 1] : null;
    let txt = null, thought = false;
    if (lastV && lastV.from === "julian" && S.minute - lastV.t < 30) { txt = lastV.text; thought = true; }
    else if (a.say && S.minute - a.say_t < 40) txt = a.say;
    if (txt) place(label("bubble", esc(txt), "bubble" + (thought ? " thought" : "")), julian.position.x, julian.position.y + .2, julian.position.z, -6);
  }
  Object.entries(labels).forEach(([k, el]) => { if (!el._seen) { el.remove(); delete labels[k]; } });
}

// ---------- camera ----------
function groundY(x, z) { return (x >= 0 && x <= W && z >= 0 && z <= H) ? heightAt(x, z) : outerHeight(x, z); }
export function flyTo(where) {
  const [tx, ty] = window.__MAP.tower;
  const target = where === "summit" ? new THREE.Vector3(tx + .5, heightAt(tx + .5, ty + .5) + .3, ty + .6) : new THREE.Vector3(W / 2, 1, H / 2 + 2);
  const dir = camera.position.clone().sub(controls.target).normalize();
  const dist = where === "summit" ? 3.4 : 50;
  flying = { t0: performance.now(), fromT: controls.target.clone(), fromP: camera.position.clone(), toT: target, toP: target.clone().add(dir.multiplyScalar(dist)) };
}

// ---------- setup ----------
export function init(container) {
  const MAP = window.__MAP; tiles = MAP.tiles; elev = MAP.elev; H = tiles.length; W = tiles[0].length;
  // water level: just below the lowest shore around the lake
  let shore = Infinity;
  for (let y = 0; y < H; y++) for (let x = 0; x < W; x++) if (tiles[y][x] === "W")
    for (const [dx, dy] of [[1, 0], [-1, 0], [0, 1], [0, -1]]) { const nx = x + dx, ny = y + dy; if (tiles[ny] && tiles[ny][nx] && tiles[ny][nx] !== "W") shore = Math.min(shore, elev[ny][nx] * K); }
  waterTiles = [];
  for (let y = 0; y < H; y++) for (let x = 0; x < W; x++) if (tiles[y][x] === "W") waterTiles.push([x, y]);
  let low = Infinity; for (const [x, y] of waterTiles) low = Math.min(low, elev[y][x] * K);
  waterH = Math.min(isFinite(shore) ? shore : 0, isFinite(low) ? low + .25 : 0) - .03;

  renderer = new THREE.WebGLRenderer({ antialias: true });
  renderer.setPixelRatio(Math.min(window.devicePixelRatio, 1.5));
  renderer.shadowMap.enabled = true; renderer.shadowMap.type = THREE.PCFSoftShadowMap;
  renderer.toneMapping = THREE.ACESFilmicToneMapping; renderer.toneMappingExposure = 1.05;
  container.appendChild(renderer.domElement);
  overlay = document.createElement("div"); overlay.className = "overlay3d"; container.appendChild(overlay);
  scene = new THREE.Scene(); scene.fog = new THREE.FogExp2("#c4d7e8", .0105);
  camera = new THREE.PerspectiveCamera(45, 1, .05, 900);
  camera.position.set(W / 2, 30, H / 2 + 34);
  controls = new MapControls(camera, renderer.domElement);
  controls.target.set(W / 2, 0, H / 2 + 2);
  controls.enableDamping = true; controls.dampingFactor = .08; controls.screenSpacePanning = false;
  controls.minDistance = .8; controls.maxDistance = 95; controls.maxPolarAngle = 1.45; controls.zoomSpeed = 1.2;
  hemi = new THREE.HemisphereLight("#cfe3ff", "#3b3326", .9); scene.add(hemi);
  sun = new THREE.DirectionalLight("#fff6e8", 2); scene.add(sun); scene.add(sun.target);
  sun.castShadow = true; sun.shadow.mapSize.set(2048, 2048); sun.shadow.bias = -.0004; sun.shadow.normalBias = .03;
  sun.shadow.camera.near = 1; sun.shadow.camera.far = 220;
  buildSky(); buildTerrain(); buildOuter(); buildWater(); buildPaths(); buildTrees(); buildTower(window.__S); buildJulian(); buildWeather();
  tower.traverse(o => { if (o.isMesh && o !== cabWin) { o.castShadow = true; o.receiveShadow = true; } });
  julian.traverse(o => { if (o.isMesh) o.castShadow = true; });
  smokeTex = smokeTexture();
  smokeMat = new THREE.SpriteMaterial({ map: smokeTex, transparent: true, opacity: .55, depthWrite: false });
  flameMats = [new THREE.MeshBasicMaterial({ color: "#ff7a1a", transparent: true, opacity: .85 }),
               new THREE.MeshBasicMaterial({ color: "#ffb52e", transparent: true, opacity: .85 }),
               new THREE.MeshBasicMaterial({ color: "#ff8800" })];
  const flameGeo = new THREE.ConeGeometry(.17, .3, 6);
  for (let i = 0; i < 90; i++) { const m = new THREE.Mesh(flameGeo, flameMats[0]); m.visible = false; scene.add(m); flames.push(m); }
  for (let i = 0; i < 4; i++) { const L = new THREE.PointLight("#ff8a30", 0, 4, 1.5); scene.add(L); fireLights.push(L); }

  // click (not drag) on the ground -> leave something there
  let down = null;
  renderer.domElement.addEventListener("pointerdown", e => { down = [e.clientX, e.clientY]; });
  renderer.domElement.addEventListener("pointerup", e => {
    if (!down || Math.hypot(e.clientX - down[0], e.clientY - down[1]) > 5 || e.button !== 0) return;
    const r = renderer.domElement.getBoundingClientRect();
    const ray = new THREE.Raycaster(); ray.setFromCamera(new THREE.Vector2((e.clientX - r.left) / r.width * 2 - 1, -(e.clientY - r.top) / r.height * 2 + 1), camera);
    const hit = ray.intersectObject(ground)[0];
    if (hit && window.__openPlant) window.__openPlant(Math.floor(hit.point.x), Math.floor(hit.point.z), e.clientX, e.clientY);
  });
  const resize = () => { const w = container.clientWidth, h = container.clientHeight; renderer.setSize(w, h, false); camera.aspect = w / h; camera.updateProjectionMatrix(); };
  new ResizeObserver(resize).observe(container); resize();

  const loop = now => {
    requestAnimationFrame(loop);
    if (window.__mapMode !== "3d" || !window.__S) return;
    if (flying) {
      const k = Math.min(1, (now - flying.t0) / 900), e = k < .5 ? 2 * k * k : 1 - Math.pow(-2 * k + 2, 2) / 2;
      controls.target.lerpVectors(flying.fromT, flying.toT, e); camera.position.lerpVectors(flying.fromP, flying.toP, e);
      if (k >= 1) flying = null;
    }
    controls.update();
    const gy = groundY(controls.target.x, controls.target.z);
    if (Math.abs(controls.target.y - gy) > .01) { const dy = gy - controls.target.y; controls.target.y += dy * .25; camera.position.y += dy * .25; }
    const cy = groundY(camera.position.x, camera.position.z) + .25;
    if (camera.position.y < cy) camera.position.y = cy;
    update(window.__S, now);
    renderer.render(scene, camera);
    updateLabels(window.__S);
  };
  requestAnimationFrame(loop);
}
