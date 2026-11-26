/*
 * Head-less test for the TV GPU panel: loads static/tv.js against a stub DOM and
 * checks that hashcat's single long hardware line is split into separate cells,
 * for one card and for two.
 *
 *     node dashboard/tests/tv_gpu.js
 *
 * The self test runs this automatically when node is installed.
 */
const fs = require("fs");
const path = require("path");
const vm = require("vm");

const SRC = path.join(__dirname, "..", "static", "tv.js");
const code = fs.readFileSync(SRC, "utf8");

const byId = new Map();
const timers = [];

const gradient = { addColorStop() {} };
const ctx = new Proxy({ createRadialGradient: () => gradient },
                      { get: (target, prop) => (prop in target ? target[prop] : () => {}),
                        set: (target, prop, value) => { target[prop] = value; return true; } });

function element(tag = "div") {
  const node = {
    tagName: tag, children: [], className: "", _text: "", id: "",
    style: {}, classList: { add() {}, remove() {} },
    addEventListener() {}, removeEventListener() {},
    appendChild(child) { node.children.push(child); return child; },
    append(...kids) { node.children.push(...kids); },
    getContext: () => ctx, clientWidth: 800, clientHeight: 600, width: 0, height: 0,
    querySelector: () => element("child"),
  };
  Object.defineProperty(node, "textContent", {
    get() { return node._text; },
    set(value) { node._text = String(value); node.children.length = 0; },  // like the DOM
  });
  return node;
}

const payload = (progress) => ({
  now: 0,
  runner: { running: true, phase: "cracking", progress, uptime: 61, gpu_only: true },
  store: { counts: { keys: 2, pow: 1, sessions: 1 }, keys: [], hashes: [],
           package: null, stats: {} },
  settings: { remote_enabled: false },
  probe: null,
  logs: { lines: [], next: 0 },
});

const ONE_CARD = { "Hardware.Mon.#01": "Temp: 70c Fan: 54% Util: 96% Core:1920MHz Mem:7001MHz Bus:16" };
const TWO_CARDS = {
  "Hardware.Mon.#01": "Temp: 63c Fan: 47% Util: 99%",
  "Hardware.Mon.#02": "Temp: 71c Fan: 51% Util: 98%",
};

let answer = payload(ONE_CARD);

const sandbox = {
  window: { devicePixelRatio: 2, addEventListener() {} },
  document: {
    getElementById: (id) => {
      if (!byId.has(id)) {
        const node = element("div");
        node.id = id;
        byId.set(id, node);
      }
      return byId.get(id);
    },
    createElement: element,
    createTextNode: (text) => ({ text: String(text) }),
  },
  performance: { now: () => 0 },
  requestAnimationFrame: () => 1,
  fetch: (url) => Promise.resolve({
    ok: true,
    json: async () => (String(url).includes("/api/state") ? answer : { ok: false }),
  }),
  setTimeout: (fn) => { timers.push(fn); return timers.length; },
  clearTimeout: () => {},
  setInterval: () => 0,
  console, Math, JSON, Date, Number, Object, Array, Set,
};
vm.createContext(sandbox);
vm.runInContext(code, sandbox);

const tick = () => new Promise((resolve) => setImmediate(resolve));

function cards() {
  const box = byId.get("k-gpu");
  return box.children.map((row) => ({
    className: row.className,
    device: (row.children[0] && row.children[0].className === "dev")
      ? row.children[0].textContent : null,
    cells: row.children
      .filter((cell) => cell.className !== "dev")
      .map((cell) => [cell.children[0].textContent, cell.children[1].textContent]),
  }));
}

(async () => {
  await tick();
  await tick();

  const fail = [];
  let shown = cards();
  if (shown.length !== 1) fail.push(`expected one gpu row, got ${shown.length}`);
  const cells = (shown[0] || { cells: [] }).cells;
  const expected = [["temp", "70°"], ["fan", "54%"], ["util", "96%"],
                    ["core", "1920"], ["mem", "7001"], ["bus", "16"]];
  if (JSON.stringify(cells) !== JSON.stringify(expected)) {
    fail.push(`cells are ${JSON.stringify(cells)}, expected ${JSON.stringify(expected)}`);
  }
  if (shown[0] && shown[0].device !== null) fail.push("a single card should not be labelled");

  // a second poll with two cards: one row each, captioned
  answer = payload(TWO_CARDS);
  const pending = timers.splice(0);
  for (const timer of pending) timer();
  await tick();
  await tick();

  shown = cards();
  if (shown.length !== 2) fail.push(`expected two gpu rows, got ${shown.length}`);
  if (shown[0] && shown[0].device !== "gpu 1") fail.push(`first caption: ${shown[0] && shown[0].device}`);
  if (shown[1] && shown[1].device !== "gpu 2") fail.push(`second caption: ${shown[1] && shown[1].device}`);
  if (shown[1] && JSON.stringify(shown[1].cells) !==
      JSON.stringify([["temp", "71°"], ["fan", "51%"], ["util", "98%"]])) {
    fail.push(`second card cells: ${JSON.stringify(shown[1].cells)}`);
  }

  console.log(fail.length ? `FAIL\n - ${fail.join("\n - ")}` : "all gpu panel checks passed");
  console.log(`one card: ${JSON.stringify(cells)}`);
  console.log(`two cards: ${shown.map((card) => `${card.device} ${card.cells.length} cells`).join(", ")}`);
  process.exit(fail.length ? 1 : 0);
})();
