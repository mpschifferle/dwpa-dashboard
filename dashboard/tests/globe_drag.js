/*
 * Head-less test for the TV globe interaction: loads static/tv.js against a stub
 * DOM and canvas, then drives a drag and checks the spin parks and comes back.
 *
 *     node dashboard/tests/globe_drag.js
 *
 * The self test runs this automatically when node is installed.
 */
const fs = require("fs");
const path = require("path");
const vm = require("vm");

const SRC = path.join(__dirname, "..", "static", "tv.js");
const code = fs.readFileSync(SRC, "utf8");

const WIDTH = 800;
const HEIGHT = 600;
const RADIUS = Math.min(WIDTH * 0.46, HEIGHT * 0.46, 440);

let clock = 0;                 // performance.now(), we control it
let pending = null;            // the queued requestAnimationFrame callback
const handlers = {};           // canvas pointer handlers
const ops = { frame: [], paths: [], frameTexts: [] };

const gradient = { addColorStop() {} };
const ctxTarget = {
  createRadialGradient: () => gradient,
  clearRect() { ops.frame = []; ops.paths = []; },      // a fresh draw starts here
  beginPath() { this._path = []; },
  moveTo(x, y) { this._push(x, y); },
  lineTo(x, y) { this._push(x, y); },
  _push(x, y) {
    const point = [x, y];
    ops.frame.push(point);
    if (!this._path) this._path = [];
    this._path.push(point);
  },
  closePath() {
    if (this._path && this._path.length) ops.paths.push(this._path);
    this._path = [];
  },
  fillText(text) { ops.frameTexts.push(text); },
  arc() {}, fill() {}, stroke() {}, setTransform() {},
};
const ctx = new Proxy(ctxTarget, {
  get: (target, prop) => (prop in target ? target[prop] : () => {}),
  set: (target, prop, value) => { target[prop] = value; return true; },
});

const element = (id) => ({
  id,
  textContent: "",
  classList: { add() {}, remove() {} },
  addEventListener(type, fn) { handlers[type] = fn; },
  setPointerCapture() {},
  releasePointerCapture() {},
  getContext: () => ctx,
  clientWidth: 800,
  clientHeight: 600,
  width: 0,
  height: 0,
  append() {},
  appendChild() {},
  querySelector: () => element("child"),
});

// a coastline band around the equator: it crosses the horizon on both sides,
// so drawing it proves the clipping works
const band = [];
for (let lon = -180; lon <= 180; lon += 10) band.push(lon * 100, 0);
for (let lon = 180; lon >= -180; lon -= 10) band.push(lon * 100, 2000);
const coastPayload = { ok: true, scale: 100, rings: [band] };

let openCoast = () => {};
const coastGate = new Promise((resolve) => { openCoast = resolve; });

const sandbox = {
  window: { devicePixelRatio: 2, addEventListener() {} },
  document: {
    getElementById: element,
    createElement: element,
    createTextNode: (text) => ({ text }),
  },
  performance: { now: () => clock },
  requestAnimationFrame: (cb) => { pending = cb; return 1; },
  fetch: (url) => {
    if (String(url).includes("/api/map/coast")) {
      return coastGate.then(() => ({ ok: true, json: async () => coastPayload }));
    }
    return Promise.resolve({ ok: false, json: async () => ({ ok: false }) });
  },
  setTimeout: () => 0,
  setInterval: () => 0,
  console,
  Math, JSON, Date, Number, Object, Array, Set,
};
vm.createContext(sandbox);
vm.runInContext(code, sandbox);

function frame(t) {
  clock = t;
  const cb = pending;
  pending = null;
  ops.frameTexts = [];
  if (cb) cb(t);
}

const same = (a, b) => JSON.stringify(a) === JSON.stringify(b);
const fail = [];
const hint = () => ops.frameTexts.join(" ");
const tick = () => new Promise((resolve) => setImmediate(resolve));
const view = () => ops.frame[0];

(async () => {
  // 1. grid mode (polygons have not loaded yet): spins on its own, no hint
  frame(0);
  frame(66);
  const spinning = view();
  frame(132);
  if (same(spinning, view())) fail.push("globe did not spin at boot");
  if (ops.frameTexts.length) fail.push(`unexpected hint at boot: "${hint()}"`);

  // 2. dragging right and down moves the view, and shows no countdown
  handlers.pointerdown({ pointerId: 1, pointerType: "mouse", button: 0,
                         clientX: 400, clientY: 300, preventDefault() {} });
  handlers.pointermove({ pointerId: 1, pointerType: "mouse",
                         clientX: 600, clientY: 380, preventDefault() {} });
  frame(200);
  const dragged = view();
  if (same(dragged, spinning)) fail.push("drag did not move the view");
  if (ops.frameTexts.length) fail.push(`hint shown while dragging: "${hint()}"`);
  handlers.pointerup({ pointerId: 1, pointerType: "mouse" });

  // 3. parked: it holds still and counts down
  frame(1000);
  const parked = view();
  frame(5000);
  if (!same(parked, view())) fail.push("globe kept spinning after the drag");
  if (!/spins again in \d+s/.test(hint())) fail.push(`no countdown while parked: "${hint()}"`);
  frame(29500);
  if (!/spins again in 1s/.test(hint())) fail.push(`countdown not counting down: "${hint()}"`);

  // 4. thirty seconds after the drag it turns again, hint gone
  frame(31000);
  const resumed = view();
  frame(31250);
  if (same(resumed, view())) fail.push("spin did not resume after 30 s");
  if (ops.frameTexts.length) fail.push(`hint still shown after resume: "${hint()}"`);

  // 5. the coastline polygons arrive: one filled path, clipped at the horizon
  openCoast();
  await tick();
  await tick();
  frame(31300);
  const shape = ops.paths.filter((ring) => ring.length >= 8)
    .sort((a, b) => b.length - a.length)[0];
  if (!shape) {
    fail.push(`no coastline polygon drawn (paths: ${ops.paths.map((ring) => ring.length)})`);
  } else {
    const distances = shape.map(([x, y]) => Math.hypot(x - WIDTH / 2, y - HEIGHT / 2));
    const furthest = Math.max(...distances);
    const outside = distances.filter((distance) => distance > RADIUS + 2).length;
    if (outside) fail.push(`${outside} coastline points landed outside the globe`);
    if (furthest < RADIUS - 6) fail.push(`coastline never reaches the limb (max ${furthest.toFixed(1)})`);
    if (shape.length < 20) fail.push(`clipped ring too small: ${shape.length} points`);
  }

  console.log(fail.length ? `FAIL\n - ${fail.join("\n - ")}` : "all globe interaction checks passed");
  console.log(`view: spin ${JSON.stringify(spinning)} -> drag ${JSON.stringify(dragged)}` +
              ` -> parked ${JSON.stringify(parked)}`);
  console.log(`clip: ${shape ? shape.length : 0} points in, furthest ` +
              `${shape ? Math.max(...shape.map(([x, y]) =>
                Math.hypot(x - WIDTH / 2, y - HEIGHT / 2))).toFixed(1) : "-"} of ${RADIUS.toFixed(0)}`);
  process.exit(fail.length ? 1 : 0);
})();
