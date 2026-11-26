/*
 * Head-less test for the cracked-keys table badges: loads static/app.js against
 * a stub DOM, feeds it two keys (one real, one proof-of-work) and checks what
 * lands in the password cells.
 *
 *     node dashboard/tests/key_badges.js
 *
 * The self test runs this automatically when node is installed.
 */
const fs = require("fs");
const path = require("path");
const vm = require("vm");

const SRC = path.join(__dirname, "..", "static", "app.js");
const code = fs.readFileSync(SRC, "utf8");

const cells = [];              // every <td> the app creates
const byId = new Map();        // stable element per id

const gradient = { addColorStop() {} };
const ctx = new Proxy({ createRadialGradient: () => gradient, createLinearGradient: () => gradient,
                        measureText: () => ({ width: 10 }) },
                      { get: (target, prop) => (prop in target ? target[prop] : () => {}),
                        set: (target, prop, value) => { target[prop] = value; return true; } });

function element(tag = "div") {
  const node = {
    tagName: tag, children: [], className: "", title: "", value: "", checked: false,
    style: {}, dataset: {}, width: 0, height: 0, clientWidth: 600, clientHeight: 200,
    scrollTop: 0, scrollHeight: 0, colSpan: 1, src: "", alt: "", disabled: false,
    handlers: {},
    classList: { add() {}, remove() {}, toggle() {}, contains: () => false },
    addEventListener(type, handler) { node.handlers[type] = handler; },
    removeEventListener() {}, focus() {}, remove() {},
    appendChild(child) { this.children.push(child); return child; },
    append(...kids) { this.children.push(...kids); },
    insertBefore(child) { this.children.push(child); return child; },
    removeChild() {}, querySelectorAll: () => [], getContext: () => ctx,
    querySelector: (selector) => byId.get(`tbody:${tag}:${selector}`) ||
                                  cache(`tbody:${tag}:${selector}`, element("tbody")),
  };
  Object.defineProperty(node, "textContent", {
    get() { return node._text || ""; },
    set(value) { node._text = String(value); node.children.length = 0; },  // like the DOM
  });
  if (tag === "td") cells.push(node);
  return node;
}

function cache(key, value) {
  byId.set(key, value);
  return value;
}

// the payload the app polls once at boot: two real keys and one proof-of-work key
// (invented values: a test must never carry anyone's real SSID, BSSID or password)
const real = { ts: 1795730000, bssid: "001122334455", ssid: "HomeNet",
               password: "hunter2", kind: "PMKID", pow: false, source: "keyfile" };
const duplicate = { ts: 1795730100, bssid: "001122334466", ssid: "OfficeNet",
                    password: "letmein1", kind: "PMKID", pow: false, source: "keyfile" };
const fake = { ts: 1795731000, bssid: "aabbccdd0011", ssid: "Decoy-1A2B",
               password: "decoypass", kind: "PMKID", pow: true, source: "keyfile" };
const payload = {
  now: 0,
  runner: { running: false, phase: "idle", progress: {}, gpu_only: true },
  store: { counts: { keys: 3, pow: 1, sessions: 1, total_crack_seconds: 0, durations: [] },
           keys: [fake, duplicate, real], hashes: [], package: null, stats: {} },
  settings: { remote_enabled: true, gpu_only: true },
  probe: null,
  logs: { lines: [], next: 0 },
};

// what /api/site/account answers: one of ours is credited to us, one is a duplicate
const account = {
  ok: true,
  verdicts: [{ bssid: real.bssid, password: real.password, verdict: "you" },
             { bssid: duplicate.bssid, password: duplicate.password, verdict: "someone-else" },
             { bssid: fake.bssid, password: fake.password, verdict: "pow" }],
  summary: { founds: 2, you: 1, someone_else: 1 },
};

const sandbox = {
  window: { devicePixelRatio: 1, addEventListener() {}, confirm: () => false },
  document: {
    getElementById: (id) => byId.get(id) || cache(id, element("div")),
    createElement: (tag) => element(tag),
    createTextNode: (text) => ({ text: String(text) }),
    querySelector: () => element("div"),
  },
  performance: { now: () => 0 },
  requestAnimationFrame: (cb) => { cb(0); return 1; },
  setTimeout: () => 0,
  clearTimeout: () => {},
  setInterval: () => 0,
  fetch: (url) => Promise.resolve({
    ok: true,
    json: () => Promise.resolve(String(url).includes("/api/site/account") ? account : payload),
  }),
  console, Map, Set, Math, JSON, Date, Number, Object, Array, String, Boolean, Promise,
  encodeURIComponent, decodeURIComponent,
};
vm.createContext(sandbox);
vm.runInContext(code, sandbox);

// the table body the app renders into (one shared node is enough here)
const body = cache("tbody:key-table:tbody", element("tbody"));
sandbox.document.getElementById("key-table").querySelector = () => body;

(async () => {
  const tick = () => new Promise((resolve) => setImmediate(resolve));
  await tick();
  await tick();

  const fail = [];
  const rowsOf = () => body.children.filter((child) => child.tagName === "tr");
  const badgesOf = () => {
    const found = [];
    for (const row of rowsOf()) {
      for (const cell of row.children) {
        for (const child of cell.children || []) {
          if (child.className && child.className.startsWith("tag ")) {
            found.push(`${child.textContent} [${child.className}]`);
          }
        }
      }
    }
    return found;
  };
  const nodeText = (node) => (node._text || node.text || "") +
    (node.children || []).map(nodeText).join("");
  const tableText = () => rowsOf()
    .map((row) => row.children.map(nodeText).join("|")).join("\n");

  // ------------------------------------------------- before any site lookup
  if (rowsOf().length !== 3) fail.push(`expected 3 rows, got ${rowsOf().length}`);
  let badges = badgesOf();
  const count = (text) => badges.filter((badge) => badge.startsWith(text)).length;
  if (count("your work") !== 2) fail.push(`'your work' should mark both real keys: ${badges}`);
  if (count("POW") !== 1) fail.push(`'POW' should mark the challenge only: ${badges}`);
  const text = tableText();
  if (!text.includes("hunter2") || !text.includes("decoypass")) {
    fail.push(`passwords missing from the table: ${text}`);
  }
  if (!/your work hunter2/.test(text)) {
    fail.push(`the real password should follow its badge: ${text}`);
  }

  // ------------------------------------- after clicking "site" (account data)
  const button = byId.get("btn-site");
  if (!button || !button.handlers.click) {
    fail.push("no click handler on the site button");
  } else {
    await button.handlers.click();
    await tick();
    badges = badgesOf();
    if (count("uploaded") !== 1) fail.push(`'uploaded' should mark the credited key: ${badges}`);
    if (count("site knew") !== 1) fail.push(`'site knew' should mark the duplicate: ${badges}`);
    if (count("your work") !== 2) fail.push(`'your work' lost after the site lookup: ${badges}`);
    if (count("POW") !== 1) fail.push(`'POW' lost after the site lookup: ${badges}`);
    if (/someone |nothing/.test(badges.join(" "))) fail.push(`stale badge text: ${badges}`);
  }

  console.log(fail.length ? `FAIL\n - ${fail.join("\n - ")}` : "all key badge checks passed");
  console.log(`badges: ${badges.join(", ")}`);
  process.exit(fail.length ? 1 : 0);
})();
