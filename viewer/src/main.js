import * as THREE from 'three';
import { OrbitControls } from 'three/examples/jsm/controls/OrbitControls.js';
import { PLYLoader } from 'three/examples/jsm/loaders/PLYLoader.js';
import { SparkRenderer, SplatMesh } from '@sparkjsdev/spark';

// ---------- data access ----------
const params = new URLSearchParams(location.search);
const API = params.get('api') ?? '';          // same origin as scanmap serve by default
const dataUrl = (sid, path) => `${API}/data/${encodeURIComponent(sid)}/${path}`;

async function getJSON(url) {
  const r = await fetch(url);
  if (!r.ok) throw new Error(`${url}: HTTP ${r.status}`);
  return r.json();
}
async function getText(url) {
  const r = await fetch(url);
  if (!r.ok) throw new Error(`${url}: HTTP ${r.status}`);
  return r.text();
}

export function parseCSV(text) {
  const lines = text.split(/\r?\n/).filter(Boolean);
  const header = lines.shift().split(',');
  return lines.map((line) => {
    const out = []; let cur = ''; let q = false;
    for (let i = 0; i < line.length; i++) {
      const c = line[i];
      if (q) {
        if (c === '"' && line[i + 1] === '"') { cur += '"'; i++; }
        else if (c === '"') q = false;
        else cur += c;
      } else if (c === '"') q = true;
      else if (c === ',') { out.push(cur); cur = ''; }
      else cur += c;
    }
    out.push(cur);
    return Object.fromEntries(header.map((h, i) => [h, out[i]]));
  });
}

function parseAsciiPly(text) {
  const end = text.indexOf('end_header');
  const head = text.slice(0, end);
  const n = +(/element vertex (\d+)/.exec(head)?.[1] ?? 0);
  const body = text.slice(end + 11).split('\n');
  const pos = new Float32Array(n * 3);
  for (let i = 0; i < n; i++) {
    const t = body[i].split(' ');
    pos[i * 3] = +t[0]; pos[i * 3 + 1] = +t[1]; pos[i * 3 + 2] = +t[2];
  }
  return pos;
}

// RdYlGn, -90 dBm (red) .. -30 dBm (green); matches the legend in index.html
const STOPS = [[0xd7, 0x19, 0x1c], [0xfd, 0xae, 0x61], [0xff, 0xff, 0xbf], [0xa6, 0xd9, 0x6a], [0x1a, 0x96, 0x41]];
export function rssiColor(rssi) {
  const t = Math.min(1, Math.max(0, (rssi + 90) / 60)) * (STOPS.length - 1);
  const i = Math.min(STOPS.length - 2, Math.floor(t)); const f = t - i;
  return STOPS[i].map((a, k) => (a + (STOPS[i + 1][k] - a) * f) / 255);
}

// ---------- scene ----------
const canvas = document.getElementById('view');
const renderer = new THREE.WebGLRenderer({ canvas, antialias: false });
renderer.setPixelRatio(Math.min(devicePixelRatio, 2));
const scene = new THREE.Scene();
scene.background = new THREE.Color(0x0f1419);
const camera = new THREE.PerspectiveCamera(60, 1, 0.05, 200);
camera.position.set(6, 8, 8);
const controls = new OrbitControls(camera, canvas);
controls.enableDamping = true;
const spark = new SparkRenderer({ renderer });
scene.add(spark);
scene.add(new THREE.AmbientLight(0xffffff, 0.8));
const sun = new THREE.DirectionalLight(0xffffff, 0.8); sun.position.set(3, 10, 4); scene.add(sun);
const gridHelper = new THREE.GridHelper(20, 20, 0x2b3640, 0x1d252e);
scene.add(gridHelper);

const groups = {
  points: new THREE.Group(), samples: new THREE.Group(), slice: new THREE.Group(),
  splat: new THREE.Group(), mesh: new THREE.Group(), path: new THREE.Group(), markers: new THREE.Group(),
};
Object.values(groups).forEach((g) => scene.add(g));

function clearGroup(g) {
  for (const c of [...g.children]) {
    g.remove(c);
    c.traverse?.((o) => { o.geometry?.dispose?.(); o.material?.map?.dispose?.(); o.material?.dispose?.(); });
    c.dispose?.();
  }
}

function resize() {
  const w = innerWidth, h = innerHeight;
  renderer.setSize(w, h, false);
  camera.aspect = w / h; camera.updateProjectionMatrix();
}
addEventListener('resize', resize); resize();

// WASD walking: move camera and orbit target together, along the view direction on the floor plane
const keys = new Set();
addEventListener('keydown', (e) => { if (e.target.tagName !== 'INPUT') keys.add(e.key.toLowerCase()); });
addEventListener('keyup', (e) => keys.delete(e.key.toLowerCase()));
const clock = new THREE.Clock();
function walk(dt) {
  if (!keys.size) return;
  const fwd = new THREE.Vector3(); camera.getWorldDirection(fwd); fwd.y = 0; fwd.normalize();
  const right = new THREE.Vector3().crossVectors(fwd, camera.up).normalize();
  const v = new THREE.Vector3();
  if (keys.has('w')) v.add(fwd); if (keys.has('s')) v.sub(fwd);
  if (keys.has('d')) v.add(right); if (keys.has('a')) v.sub(right);
  if (keys.has('e')) v.y += 1; if (keys.has('q')) v.y -= 1;
  v.multiplyScalar(1.8 * dt);
  camera.position.add(v); controls.target.add(v);
}

renderer.setAnimationLoop(() => {
  walk(clock.getDelta());
  controls.update();
  renderer.render(scene, camera);
});

// ---------- UI state ----------
const $ = (id) => document.getElementById(id);
const state = { sid: null, samples: [], index: null, layer: null, grid: null, summary: null };

function setVisible() {
  groups.slice.visible = $('showSlice').checked;
  groups.samples.visible = $('showSamples').checked;
  groups.points.visible = $('showPoints').checked;
  groups.splat.visible = $('showSplat').checked;
  groups.mesh.visible = $('showMesh').checked;
  groups.path.visible = $('showPath').checked;
}
['showSlice', 'showSamples', 'showPoints', 'showSplat', 'showMesh', 'showPath'].forEach((id) => $(id).addEventListener('change', () => {
  setVisible();
  if (id === 'showMesh' && $('showMesh').checked && !groups.mesh.children.length) loadMesh();
  if (id === 'showSplat' && $('showSplat').checked && !groups.splat.children.length) loadSplat();
}));

async function loadSessions() {
  let sessions = [];
  try { sessions = await getJSON(`${API}/api/sessions`); } catch (e) { $('status').textContent = `Cannot reach the receiver: ${e.message}`; }
  const sel = $('session');
  sel.innerHTML = '';
  for (const s of sessions) {
    const o = document.createElement('option');
    o.value = s.id; o.textContent = s.id; sel.appendChild(o);
  }
  const want = params.get('session');
  if (want && !sessions.some((s) => s.id === want)) {
    const o = document.createElement('option'); o.value = want; o.textContent = want; sel.appendChild(o);
  }
  if (want) sel.value = want;
  if (sel.value) openSession(sel.value);
  else if (sessions.length === 0) $('status').textContent = 'No sessions yet. Record a walk on the phone and tap "Send to Fedora".';
}
$('session').addEventListener('change', (e) => {
  const u = new URL(location.href); u.searchParams.set('session', e.target.value); history.replaceState(null, '', u);
  openSession(e.target.value);
});

function renderStatus(s) {
  const st = s?.status ?? {};
  const parts = [];
  if (s?.device) parts.push(`${s.device}, ${Math.round(s.duration_s ?? 0)} s walk`);
  if (s?.counts) parts.push(`${s.counts.keyframes ?? '?'} keyframes, ${s.counts.samples ?? '?'} samples`);
  let line = st.state ? `Processing: <b>${st.state}</b>${st.current ? ` (${st.current})` : ''}` : 'Not processed yet';
  if (st.error) line += `<br><span class="bad">${st.error}</span>`;
  if (s?.alignment?.median_error_m != null) {
    const e = s.alignment.median_error_m;
    const cls = e < 0.05 ? 'ok' : e < 0.15 ? 'warn' : 'bad';
    line += `<br>Alignment: <span class="${cls}">${(e * 100).toFixed(1)} cm median</span> (${s.alignment.quality})`;
  }
  $('status').innerHTML = parts.join('<br>') + (parts.length ? '<br>' : '') + line;
}

let pollTimer = null;
async function pollStatus() {
  clearTimeout(pollTimer);
  if (!state.sid) return;
  try {
    const s = await getJSON(`${API}/api/sessions/${encodeURIComponent(state.sid)}`);
    const before = state.summary?.status?.state;
    state.summary = s; renderStatus(s);
    if (['queued', 'running'].includes(s.status?.state)) pollTimer = setTimeout(pollStatus, 3000);
    else if (before && before !== s.status?.state) openSession(state.sid); // finished: reload outputs
  } catch { /* static hosting without API */ }
}

async function openSession(sid) {
  state.sid = sid;
  Object.values(groups).forEach(clearGroup);
  $('info').textContent = '';
  try { state.summary = await getJSON(`${API}/api/sessions/${encodeURIComponent(sid)}`); renderStatus(state.summary); }
  catch { state.summary = null; $('status').textContent = sid; }
  pollStatus();

  state.samples = parseCSV(await getText(dataUrl(sid, 'samples.csv'))).map((r) => ({
    t: +r.t_ns, kind: r.kind, id: r.id, name: r.name, rssi: +r.rssi_dbm, x: +r.x, y: +r.y, z: +r.z,
  }));
  loadPoints(sid);
  loadPath(sid);
  if ($('showSplat').checked) loadSplat();
  if ($('showMesh').checked) loadMesh();

  try { state.index = await getJSON(dataUrl(sid, 'signal/index.json')); }
  catch { state.index = null; }
  fillLayers();
  frameScene();
}

function frameScene() {
  if (!state.samples.length) return;
  const box = new THREE.Box3();
  for (const s of state.samples) box.expandByPoint(new THREE.Vector3(s.x, s.y, s.z));
  const c = box.getCenter(new THREE.Vector3()); const size = box.getSize(new THREE.Vector3()).length();
  controls.target.copy(c);
  camera.position.set(c.x + size * 0.4, c.y + size * 0.8, c.z + size * 0.6);
  gridHelper.position.set(c.x, floorY(), c.z);
  const ys = state.samples.map((s) => s.y);
  const lo = Math.min(...ys) - 1.2, hi = Math.max(...ys) + 1.2;
  const h = $('height'); h.min = lo.toFixed(2); h.max = hi.toFixed(2); h.value = median(ys).toFixed(2);
}
const median = (a) => { const b = [...a].sort((x, y) => x - y); return b[b.length >> 1]; };
let pointsFloor = null;
function floorY() {
  if (pointsFloor != null) return pointsFloor;
  return Math.min(...state.samples.map((s) => s.y)) - 1.2;
}

async function loadPoints(sid) {
  pointsFloor = null;
  try {
    const pos = parseAsciiPly(await getText(dataUrl(sid, 'depth_points.ply')));
    const ys = []; for (let i = 1; i < pos.length; i += 3) ys.push(pos[i]);
    ys.sort((a, b) => a - b); pointsFloor = ys[Math.floor(ys.length * 0.02)] ?? null;
    if (pointsFloor != null) gridHelper.position.y = pointsFloor;
    const g = new THREE.BufferGeometry(); g.setAttribute('position', new THREE.BufferAttribute(pos, 3));
    const colors = new Float32Array(pos.length);
    const yr = (ys[ys.length - 1] - ys[0]) || 1;
    for (let i = 0; i < pos.length / 3; i++) {
      const v = 0.35 + 0.5 * (pos[i * 3 + 1] - ys[0]) / yr; colors.set([v * 0.8, v * 0.9, v], i * 3);
    }
    g.setAttribute('color', new THREE.BufferAttribute(colors, 3));
    groups.points.add(new THREE.Points(g, new THREE.PointsMaterial({ size: 0.03, vertexColors: true })));
  } catch { /* no point cloud */ }
}

async function loadPath(sid) {
  try {
    const rows = parseCSV(await getText(dataUrl(sid, 'poses.csv')));
    const pts = [];
    rows.forEach((r, i) => { if (r.tracking_state === 'tracking' && i % 6 === 0) pts.push(new THREE.Vector3(+r.tx, +r.ty, +r.tz)); });
    if (pts.length < 2) return;
    const g = new THREE.BufferGeometry().setFromPoints(pts);
    groups.path.add(new THREE.Line(g, new THREE.LineBasicMaterial({ color: 0x5aa9ff, transparent: true, opacity: 0.6 })));
  } catch { /* none */ }
}

function loadSplat() {
  if (!state.summary?.outputs?.splat) return;
  const splat = new SplatMesh({ url: dataUrl(state.sid, 'splat/splat.ply') });
  groups.splat.add(splat);
}

function loadMesh() {
  if (!state.summary?.outputs?.mesh) return;
  new PLYLoader().load(dataUrl(state.sid, 'mesh/mesh.ply'), (geo) => {
    geo.computeVertexNormals();
    const mat = new THREE.MeshStandardMaterial({ vertexColors: geo.hasAttribute('color'), color: 0xcccccc, side: THREE.DoubleSide });
    groups.mesh.add(new THREE.Mesh(geo, mat));
  });
}

// ---------- layers ----------
function layerList() {
  if (state.index?.layers?.length) return state.index.layers;
  // no signal grids yet: offer raw sample layers per transmitter
  const by = new Map();
  for (const s of state.samples) {
    const k = (s.kind === 'ble' ? 'ble_' : 'wifi_') + s.id;
    if (!by.has(k)) by.set(k, { id: k, kind: s.kind === 'ble' ? 'ble' : 'wifi', name: `${s.name || '(unnamed)'} · ${s.id}`, samples: 0, members: [] });
    by.get(k).samples++;
  }
  return [...by.values()].sort((a, b) => b.samples - a.samples);
}

function fillLayers() {
  const sel = $('layer'); const f = $('layerFilter').value.toLowerCase();
  const prev = sel.value;
  sel.innerHTML = '';
  for (const l of layerList()) {
    if (f && !l.name.toLowerCase().includes(f) && !l.id.toLowerCase().includes(f)) continue;
    const o = document.createElement('option');
    o.value = l.id;
    o.textContent = `${l.kind === 'ble' ? 'BLE' : 'Wi-Fi'} · ${l.name} (${l.samples})`;
    sel.appendChild(o);
  }
  sel.value = prev && [...sel.options].some((o) => o.value === prev) ? prev : sel.options[0]?.value ?? '';
  selectLayer(sel.value);
}
$('layerFilter').addEventListener('input', fillLayers);
$('layer').addEventListener('change', (e) => selectLayer(e.target.value));

async function selectLayer(id) {
  const layer = layerList().find((l) => l.id === id);
  state.layer = layer; state.grid = null;
  clearGroup(groups.samples); clearGroup(groups.slice); clearGroup(groups.markers);
  if (!layer) return;
  drawSamples(layer);
  if (layer.file && state.index?.grid) {
    const r = await fetch(dataUrl(state.sid, `signal/${layer.file}`));
    if (r.ok && state.layer === layer) {
      state.grid = new Float32Array(await r.arrayBuffer());
      drawSlice();
    }
  }
  if (layer.estimate?.position) {
    const [x, y, z] = layer.estimate.position;
    const m = new THREE.Mesh(new THREE.OctahedronGeometry(0.15), new THREE.MeshStandardMaterial({ color: 0xffffff, emissive: 0x3ddc84 }));
    m.position.set(x, y, z); groups.markers.add(m);
  }
  const e = layer.estimate;
  $('info').innerHTML = [
    `<b>${layer.name}</b>`,
    `${layer.samples} samples` + (layer.max_rssi != null ? `, strongest ${layer.max_rssi} dBm, median ${Math.round(layer.median_rssi)} dBm` : ''),
    e ? `Estimated transmitter position (white diamond): ${e.position.map((v) => v.toFixed(1)).join(', ')} m, fit ±${e.rms_db.toFixed(1)} dB` : '',
    state.index ? '' : 'Signal grids not built yet: showing raw samples only.',
  ].filter(Boolean).join('<br>');
}

function drawSamples(layer) {
  const ids = new Set(layer.members?.length ? layer.members : [layer.id.replace(/^(wifi|ble|ssid)_/, '')]);
  const wantBle = layer.kind === 'ble';
  const pick = state.samples.filter((s) => ids.has(s.id) && (s.kind === 'ble') === wantBle);
  const pos = new Float32Array(pick.length * 3); const col = new Float32Array(pick.length * 3);
  pick.forEach((s, i) => { pos.set([s.x, s.y, s.z], i * 3); col.set(rssiColor(s.rssi), i * 3); });
  const g = new THREE.BufferGeometry();
  g.setAttribute('position', new THREE.BufferAttribute(pos, 3));
  g.setAttribute('color', new THREE.BufferAttribute(col, 3));
  groups.samples.add(new THREE.Points(g, new THREE.PointsMaterial({ size: 0.07, vertexColors: true })));
}

function drawSlice() {
  clearGroup(groups.slice);
  const hdr = state.index?.grid; const vals = state.grid;
  if (!hdr || !vals) return;
  const [nx, ny, nz] = hdr.dims; const v = hdr.voxel; const [ox, oy, oz] = hdr.origin;
  const h = +$('height').value;
  $('heightVal').textContent = `${h.toFixed(2)} m`;
  const iy = Math.min(ny - 1, Math.max(0, Math.floor((h - oy) / v)));
  const img = new Uint8Array(nx * nz * 4);
  const alpha = Math.round(+$('opacity').value * 255);
  for (let iz = 0; iz < nz; iz++) {
    for (let ix = 0; ix < nx; ix++) {
      const val = vals[ix + nx * (iy + ny * iz)];
      const o = (ix + nx * iz) * 4;
      if (Number.isNaN(val)) { img[o + 3] = 0; continue; }
      const c = rssiColor(val); const a = alpha / 255;
      // premultiplied so linear filtering fades to transparent instead of to black at unknown cells
      img[o] = c[0] * 255 * a; img[o + 1] = c[1] * 255 * a; img[o + 2] = c[2] * 255 * a; img[o + 3] = alpha;
    }
  }
  const tex = new THREE.DataTexture(img, nx, nz, THREE.RGBAFormat);
  tex.magFilter = THREE.LinearFilter; tex.premultiplyAlpha = false; tex.needsUpdate = true; tex.colorSpace = THREE.SRGBColorSpace;
  const plane = new THREE.Mesh(
    new THREE.PlaneGeometry(nx * v, nz * v),
    new THREE.MeshBasicMaterial({
      map: tex, transparent: true, side: THREE.DoubleSide, depthWrite: false,
      blending: THREE.CustomBlending, blendSrc: THREE.OneFactor, blendDst: THREE.OneMinusSrcAlphaFactor,
    }),
  );
  // PlaneGeometry lies in XY; rotate so texture u -> +X and v -> +Z
  plane.rotation.x = Math.PI / 2;
  plane.position.set(ox + nx * v / 2, oy + (iy + 0.5) * v, oz + nz * v / 2);
  groups.slice.add(plane);
}
$('height').addEventListener('input', drawSlice);
$('opacity').addEventListener('input', drawSlice);

async function reprocess(mode) {
  if (!state.sid) return;
  const r = await fetch(`${API}/api/sessions/${encodeURIComponent(state.sid)}/process?mode=${mode}`, { method: 'POST' });
  if (!r.ok) { $('status').innerHTML += `<br><span class="bad">${(await r.json()).detail ?? r.status}</span>`; return; }
  state.summary = { ...state.summary, status: { state: 'queued' } };
  renderStatus(state.summary); pollStatus();
}
$('process').addEventListener('click', () => reprocess('photogrammetry'));
$('processAr').addEventListener('click', () => reprocess('ar_poses'));

setVisible();
loadSessions();
