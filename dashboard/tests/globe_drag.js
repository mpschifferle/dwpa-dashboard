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

let clock = 0;                 // performance.now(), we control it
let pending = null;            // the queued requestAnimationFrame callback
const handlers = {};           // canvas pointer handlers
const ops = { points: [], land: null, frameTexts: [] };

const gradient = { addColorStop() {} };
const ctxTarget = {
  createRadialGradient: () => gradient,
  beginPath() {
    if (ops.points.length > 500) ops.land = ops.points[0];   // the landmass path
    ops.points = [];
  },
  moveTo(x, y) {
    ops.points.push([Math.round(x * 100) / 100, Math.round(y * 100) / 100]);
  },
  fillText(text) { ops.frameTexts.push(text); },
  arc() {}, fill() {}, stroke() {}, clearRect() {}, lineTo() {}, closePath() {},
  setTransform() {},
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

const sandbox = {
  window: { devicePixelRatio: 2, addEventListener() {} },
  document: {
    getElementById: element,
    createElement: element,
    createTextNode: (text) => ({ text }),
  },
  performance: { now: () => clock },
  requestAnimationFrame: (cb) => { pending = cb; return 1; },
  fetch: () => new Promise(() => {}),   // no traffic: the page stays on defaults
  setTimeout,
  setInterval: () => 0,                 // the wall clock ticker
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

// 1. it spins on its own from the first frame, without a hint
frame(0);
frame(66);
const spinning = ops.land;
frame(132);
if (same(spinning, ops.land)) fail.push("globe did not spin at boot");
if (ops.frameTexts.length) fail.push(`unexpected hint at boot: "${hint()}"`);

// 2. dragging right and down moves the view, and shows no countdown
handlers.pointerdown({ pointerId: 1, pointerType: "mouse", button: 0,
                       clientX: 400, clientY: 300, preventDefault() {} });
handlers.pointermove({ pointerId: 1, pointerType: "mouse",
                       clientX: 600, clientY: 380, preventDefault() {} });
frame(200);
const dragged = ops.land;
if (same(dragged, spinning)) fail.push("drag did not move the view");
if (ops.frameTexts.length) fail.push(`hint shown while dragging: "${hint()}"`);
handlers.pointerup({ pointerId: 1, pointerType: "mouse" });

// 3. parked: it holds still and counts down
frame(1000);
const parked = ops.land;
frame(5000);
if (!same(parked, ops.land)) fail.push("globe kept spinning after the drag");
if (!/spins again in \d+s/.test(hint())) fail.push(`no countdown while parked: "${hint()}"`);
frame(29500);
if (!/spins again in 1s/.test(hint())) fail.push(`countdown not counting down: "${hint()}"`);

// 4. thirty seconds after the drag it turns again, hint gone
frame(31000);
const resumed = ops.land;
frame(31250);
if (same(resumed, ops.land)) fail.push("spin did not resume after 30 s");
if (ops.frameTexts.length) fail.push(`hint still shown after resume: "${hint()}"`);

console.log(fail.length ? `FAIL\n - ${fail.join("\n - ")}` : "all globe interaction checks passed");
console.log(`view: spin ${JSON.stringify(spinning)} -> drag ${JSON.stringify(dragged)}` +
            ` -> parked ${JSON.stringify(parked)}`);
process.exit(fail.length ? 1 : 0);
