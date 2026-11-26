/* TV mode: big numbers, a spinning country globe, and the site's stats wall. */
(() => {
  "use strict";

  const $ = (id) => document.getElementById(id);
  const state = {
    keys: [],
    world: { counts: {}, samples: 0, updated: 0 },
    stats: null,
    rotation: 0,
    tilt: 0,
    lastInput: 0,
    failures: 0,
  };

  // approximate country centroids for the globe (lat, lon)
  const CENTROIDS = {
    de: [51.2, 10.4], us: [39.8, -98.6], gb: [54.0, -2.0], fr: [46.2, 2.2],
    nl: [52.1, 5.3], be: [50.5, 4.5], es: [40.5, -3.7], it: [41.9, 12.6],
    pt: [39.4, -8.2], ie: [53.4, -8.2], dk: [56.3, 9.5], se: [60.1, 18.6],
    no: [60.5, 8.5], fi: [61.9, 25.7], pl: [51.9, 19.1], cz: [49.8, 15.5],
    sk: [48.7, 19.7], at: [47.5, 14.6], ch: [46.8, 8.2], hu: [47.2, 19.5],
    ro: [45.9, 25.0], bg: [42.7, 25.5], gr: [39.1, 21.8], hr: [45.1, 15.2],
    si: [46.2, 14.8], rs: [44.0, 21.0], ba: [43.9, 17.7], mk: [41.6, 21.7],
    al: [41.2, 20.2], me: [42.7, 19.4], ua: [48.4, 31.2], by: [53.7, 27.9],
    ru: [61.5, 105.3], lt: [55.2, 23.9], lv: [56.9, 24.6], ee: [58.6, 25.0],
    tr: [39.0, 35.2], il: [31.0, 34.9], sa: [23.9, 45.1], ae: [23.4, 53.8],
    qa: [25.3, 51.2], kw: [29.3, 47.5], jo: [30.6, 36.2], lb: [33.9, 35.9],
    eg: [26.8, 30.8], ma: [31.8, -7.1], dz: [28.0, 1.7], tn: [33.9, 9.5],
    ly: [26.3, 17.2], ng: [9.1, 8.7], gh: [7.9, -1.0], sn: [14.5, -14.5],
    ci: [7.5, -5.5], cm: [7.4, 12.4], ke: [-0.0, 37.9], tz: [-6.4, 34.9],
    ug: [1.4, 32.3], et: [9.1, 40.5], za: [-30.6, 22.9], so: [5.2, 46.2],
    mz: [-18.7, 35.5], zw: [-19.0, 29.2], zm: [-13.1, 27.8], ao: [-11.2, 17.9],
    na: [-22.9, 18.5], bw: [-22.3, 24.7], mg: [-18.8, 46.9], mu: [-20.3, 57.6],
    in: [20.6, 79.0], pk: [30.4, 69.3], bd: [23.7, 90.4], lk: [7.9, 80.8],
    np: [28.4, 84.1], cn: [35.9, 104.2], jp: [36.2, 138.3], kr: [35.9, 127.8],
    tw: [23.7, 121.0], hk: [22.3, 114.2], sg: [1.4, 103.8], my: [4.2, 101.9],
    id: [-0.8, 113.9], th: [15.9, 101.0], vn: [14.1, 108.3], ph: [12.9, 121.8],
    au: [-25.3, 133.8], nz: [-40.9, 174.9], br: [-14.2, -51.9], ar: [-38.4, -63.6],
    cl: [-35.7, -71.5], co: [4.6, -74.3], pe: [-9.2, -75.0], ve: [6.4, -66.6],
    ec: [-1.8, -78.2], uy: [-32.5, -55.8], py: [-23.4, -58.4], bo: [-16.3, -63.6],
    mx: [23.6, -102.6], gt: [15.8, -90.2], cr: [9.7, -83.8], pa: [8.5, -80.8],
    cu: [21.5, -77.8], do: [18.7, -70.2], pr: [18.2, -66.4], jm: [18.1, -77.3],
    ca: [56.1, -106.3], gl: [71.7, -42.6], is: [64.9, -19.0], cy: [35.1, 33.4],
    mt: [35.9, 14.4], lu: [49.8, 6.1], md: [47.4, 28.4], ge: [42.3, 43.4],
    am: [40.1, 45.0], az: [40.1, 47.6], kz: [48.0, 66.9], uz: [41.4, 64.6],
    kg: [41.2, 74.8], af: [33.9, 67.7], ir: [32.4, 53.7], iq: [33.2, 43.7],
    sy: [34.8, 39.0], ye: [15.6, 48.5], sd: [12.9, 30.2], mr: [21.0, -10.9],
    ml: [17.6, -4.0], bf: [12.2, -1.6], ne: [17.6, 8.1], td: [15.5, 18.7],
    tt: [10.7, -61.2], bs: [25.0, -77.4], bz: [17.2, -88.5], sv: [13.8, -88.9],
    hn: [15.2, -86.2], ni: [12.9, -85.2], mo: [22.2, 113.5], bn: [4.5, 114.7],
  };

  // Coarse land mask, one entry per 5-degree latitude band (90N -> 90S), each a
  // list of inclusive column ranges (5-degree longitude cells, 0 = 180W).
  // Deliberately low-res: it only has to read as "continents" on a wall display.
  const LAND = [
    [],                                                                   // 90..85N
    [[13, 21], [24, 31], [38, 40], [46, 47], [55, 56]],
    [[12, 22], [24, 31], [38, 40], [46, 48], [55, 57]],
    [[2, 9], [11, 22], [25, 31], [36, 41], [44, 71]],
    [[1, 9], [11, 24], [26, 30], [35, 42], [44, 70]],
    [[1, 9], [11, 25], [27, 29], [31, 34], [36, 42], [44, 70]],
    [[2, 8], [11, 26], [33, 35], [37, 43], [44, 70]],
    [[2, 6], [9, 27], [33, 35], [36, 44], [45, 69], [70, 70]],
    [[9, 27], [33, 35], [36, 44], [45, 66], [66, 67]],
    [[9, 28], [33, 44], [45, 66], [64, 66]],
    [[10, 28], [33, 35], [36, 44], [45, 63], [63, 66]],
    [[11, 27], [32, 44], [44, 50], [50, 62], [64, 65]],
    [[13, 22], [31, 44], [44, 49], [50, 60], [61, 61]],
    [[14, 17], [31, 44], [44, 49], [50, 56], [56, 60]],
    [[15, 21], [31, 44], [44, 47], [50, 54], [56, 60]],
    [[16, 21], [30, 42], [43, 46], [51, 52], [56, 61]],
    [[17, 21], [30, 42], [43, 45], [52, 52], [56, 61]],
    [[18, 24], [30, 45], [56, 63]],
    [[18, 26], [30, 45], [56, 64]],
    [[19, 27], [30, 44], [57, 65]],
    [[19, 27], [30, 43], [44, 45], [57, 65]],
    [[19, 27], [31, 43], [44, 45], [58, 65]],
    [[20, 27], [32, 42], [44, 45], [58, 66]],
    [[20, 26], [33, 41], [58, 66]],
    [[21, 25], [34, 40], [58, 66]],
    [[21, 24], [64, 66], [70, 71]],
    [[21, 23], [65, 66], [69, 71]],
    [[22, 23]],
    [[22, 23]],
    [],
    [],
    [[24, 28]],
    [[0, 71]],
    [[0, 71]],
    [[0, 71]],
    [[0, 71]],
  ];

  // Built-in 5-degree fallback; /api/map/land swaps in a 1-degree grid built
  // from Natural Earth (public domain) once it is ready.
  let landGrid = { step: 5, rows: LAND };
  let LAND_CELLS = [];

  function buildLandCells(grid) {
    const perQuad = Math.max(1, Math.round(5 / grid.step));   // <= 5 degrees wide
    const unit = (lat, lon) => {
      const la = (lat * Math.PI) / 180;
      const lo = (lon * Math.PI) / 180;
      return [Math.cos(la) * Math.sin(lo), Math.sin(la), Math.cos(la) * Math.cos(lo)];
    };
    const cells = [];
    grid.rows.forEach((bands, row) => {
      const latHi = 90 - row * grid.step;
      const latLo = latHi - grid.step;
      for (const [from, to] of bands) {
        for (let start = from; start <= to; start += perQuad) {
          const end = Math.min(to, start + perQuad - 1);
          cells.push([
            unit(latHi, -180 + start * grid.step),
            unit(latHi, -180 + (end + 1) * grid.step),
            unit(latLo, -180 + (end + 1) * grid.step),
            unit(latLo, -180 + start * grid.step),
          ]);
        }
      }
    });
    return cells;
  }

  LAND_CELLS = buildLandCells(landGrid);

  // real coastlines: rings of unit vectors, filled and stroked as polygons
  let COAST_RINGS = null;
  const coastView = [];
  const coastClip = [];

  function rotateRing(ring, cosR, sinR, cosT, sinT) {
    coastView.length = 0;
    for (const [x, y, z] of ring) {
      const xr = x * cosR + z * sinR;
      const zr0 = z * cosR - x * sinR;
      coastView.push(xr, y * cosT - zr0 * sinT, y * sinT + zr0 * cosT);
    }
    return coastView;
  }

  // keep the part of a ring in front of the camera (Sutherland–Hodgman on z > 0)
  // and lift the cut points back onto the sphere so the edge follows the limb
  function clipToHorizon(points) {
    coastClip.length = 0;
    const count = points.length / 3;
    for (let index = 0; index < count; index += 1) {
      const a = index * 3;
      const b = ((index + 1) % count) * 3;
      const az = points[a + 2];
      const bz = points[b + 2];
      if (az > 0) coastClip.push(points[a], points[a + 1], az);
      if ((az > 0) !== (bz > 0)) {
        const t = az / (az - bz);
        const x = points[a] + (points[b] - points[a]) * t;
        const y = points[a + 1] + (points[b + 1] - points[a + 1]) * t;
        const len = Math.hypot(x, y) || 1;
        coastClip.push(x / len, y / len, 0);
      }
    }
    return coastClip;
  }

  const fmt = (value) => {
    const n = Number(value);
    if (!Number.isFinite(n)) return "—";
    if (n >= 1e9) return `${(n / 1e9).toFixed(2)}G`;
    if (n >= 1e6) return `${(n / 1e6).toFixed(2)}M`;
    if (n >= 1e3) return `${(n / 1e3).toFixed(1)}k`;
    return String(Math.round(n));
  };

  const seconds = (s) => {
    if (!Number.isFinite(Number(s))) return "—";
    const v = Math.max(0, Math.round(Number(s)));
    if (v < 60) return `${v}s`;
    if (v < 3600) return `${Math.floor(v / 60)}m ${v % 60}s`;
    return `${Math.floor(v / 3600)}h ${Math.floor((v % 3600) / 60)}m`;
  };

  // "4h", "22m": how old the saved site sample is
  const ago = (s) => {
    if (s >= 86400) return `${Math.round(s / 86400)}d`;
    if (s >= 3600) return `${Math.round(s / 3600)}h`;
    return `${Math.max(1, Math.round(s / 60))}m`;
  };

  function setText(id, value) {
    const el = $(id);
    if (el && el.textContent !== String(value)) el.textContent = value;
  }


  // ------------------------------------------------------------------ polling

  // hashcat puts a card's numbers on one long line, e.g.
  //   "Temp: 70c Fan: 54% Util: 96% Core:1920MHz Mem:7001MHz Bus:16"
  // one cell per number, so the tile never has to cut the line short
  const HW_ORDER = ["temp", "fan", "util", "core", "mem", "bus", "power"];
  const HW_NAMES = { pwr: "power" };

  function parseHardware(text) {
    const cells = [];
    const pattern = /([A-Za-z]+)\s*:\s*([\d.]+)\s*(%|c|C|W|MHz)?/g;
    let match = pattern.exec(String(text || ""));
    while (match) {
      const unit = match[3] || "";
      const value = unit === "c" || unit === "C" ? `${match[2]}°`
        : unit === "MHz" ? match[2] : `${match[2]}${unit}`;
      cells.push([HW_NAMES[match[1].toLowerCase()] || match[1].toLowerCase(), value]);
      match = pattern.exec(String(text || ""));
    }
    const rank = (name) => {
      const index = HW_ORDER.indexOf(name);
      return index < 0 ? HW_ORDER.length : index;
    };
    return cells.sort((a, b) => rank(a[0]) - rank(b[0])).slice(0, 6);
  }

  function renderGpu(progress) {
    const box = $("k-gpu");
    if (!box) return;
    const keys = Object.keys(progress)
      .filter((key) => /^(?:Hardware\.Mon|HWMon\.GPU)\b/i.test(key))
      .sort();
    box.textContent = "";
    if (!keys.length) {
      const dash = document.createElement("span");
      dash.className = "dim";
      dash.textContent = "—";
      box.appendChild(dash);
      return;
    }

    keys.forEach((key, device) => {
      const row = document.createElement("div");
      row.className = "hw";
      if (keys.length > 1) {
        const name = document.createElement("span");
        name.className = "dev";
        name.textContent = `gpu ${device + 1}`;
        row.appendChild(name);
      }
      for (const [label, value] of parseHardware(progress[key])) {
        const cell = document.createElement("span");
        const caption = document.createElement("i");
        caption.textContent = label;
        const measure = document.createElement("b");
        measure.textContent = value;
        cell.append(caption, measure);
        row.appendChild(cell);
      }
      box.appendChild(row);
    });
  }

  function renderRunner(data) {
    const runner = data.runner || {};
    const store = data.store || {};
    const progress = runner.progress || {};
    const counts = store.counts || {};

    const pill = $("pill");
    pill.textContent = runner.running ? "running" : "idle";
    pill.className = `pill ${runner.running ? "running" : "stopped"}`;
    setText("phase", runner.phase || "");

    const speed = progress["Speed.#01"] || progress["Speed.#1"] || "";
    const speedMatch = /([\d.]+)\s*([kKmMgG]?H\/s)/.exec(speed);
    setText("k-speed", speedMatch ? `${speedMatch[1]} ${speedMatch[2]}` : "—");

    const pct = /\(([\d.]+)%\)/.exec(progress.Progress || "");
    setText("k-progress", pct ? `${pct[1]}%` : "—");
    $("k-bar").style.width = pct ? `${Math.min(100, parseFloat(pct[1]))}%` : "0%";

    const eta = /\(([^)]+)\)\s*$/.exec(progress["Time.Estimated"] || "");
    setText("k-eta", eta ? eta[1] : "—");
    setText("k-uptime", seconds(runner.uptime));
    setText("k-keys", fmt(counts.keys || 0));
    setText("k-pow", fmt(counts.pow || 0));

    const recovered = /(\d+\/\d+)\s*\(([\d.]+)%\)/.exec(progress.Recovered || "");
    setText("k-recovered", recovered
      ? `${recovered[1]} (${Math.round(parseFloat(recovered[2]))}%)` : "—");

    renderGpu(progress);

    renderKeys(store.keys || []);
    if (data.settings) state.settings = data.settings;
    renderChrome();

    const lines = data.logs && data.logs.lines;
    if (lines && lines.length) {
      const last = lines[lines.length - 1];
      setText("ticker", `${new Date(last.ts * 1000).toLocaleTimeString([], { hour12: false })}  ${last.text}`);
    }
  }

  function renderKeys(keys) {
    state.keys = keys;
    const list = $("keys");
    const real = keys.filter((key) => !key.pow);
    const shown = (real.length ? real : keys).slice(0, 5);
    list.textContent = "";

    if (!shown.length) {
      const li = document.createElement("li");
      li.className = "dim";
      li.textContent = "nothing cracked yet";
      list.appendChild(li);
      return;
    }

    for (const key of shown) {
      const li = document.createElement("li");
      if (key.cc) {
        const img = document.createElement("img");
        img.src = `https://flagcdn.com/16x12/${key.cc}.png`;
        img.alt = key.cc.toUpperCase();
        img.width = 16;
        img.height = 12;
        li.appendChild(img);
      }
      const ssid = document.createElement("span");
      ssid.textContent = key.ssid || key.bssid || "?";
      const pw = document.createElement("span");
      pw.className = "pw";
      pw.textContent = key.password || "";
      li.append(ssid, pw);
      if (key.pow) {
        const tag = document.createElement("span");
        tag.className = "tag";
        tag.textContent = "POW";
        li.appendChild(tag);
      }
      list.appendChild(li);
    }
  }

  const WALL_24H = [
    ["processed nets", "Last 24h processed nets"],
    ["performance", "Last 24h performance"],
    ["submissions", "Last 24h submissions"],
    ["founds", "Last 24h founds"],
    ["contributors", "Current contributors count"],
    ["keyspace", "Current keyspace progress"],
  ];
  const WALL_ALL = [
    ["nets", "Total nets"],
    ["cracked nets", "Total cracked nets"],
    ["success rate", "Total success rate"],
    ["pmkid rate", "PMKID success rate"],
    ["geolocated", "Geolocated nets"],
    ["round ends in", "Current round ends in"],
  ];

  function renderStats(stats) {
    state.stats = stats;
    // "413 years, 5 months, 27 days, 3 hours" -> "413y 5mo 27d 3h"
    const compact = (value) => String(value)
      .replace(/(\d+)\s+years?/gi, "$1y")
      .replace(/(\d+)\s+months?/gi, "$1mo")
      .replace(/(\d+)\s+days?/gi, "$1d")
      .replace(/(\d+)\s+hours?/gi, "$1h")
      .replace(/,\s*/g, " ");

    const fill = (target, rows) => {
      const box = $(target);
      box.textContent = "";
      for (const [label, key] of rows) {
        const div = document.createElement("div");
        const span = document.createElement("span");
        span.textContent = label;
        const b = document.createElement("b");
        b.textContent = compact(stats[key] || "—");
        b.title = stats[key] || "";
        div.append(span, b);
        box.appendChild(div);
      }
    };
    fill("wall", WALL_24H);
    fill("wall-all", WALL_ALL);
  }

  function mineCountries() {
    const counts = {};
    for (const key of state.keys) {
      if (key.pow || !key.cc) continue;
      counts[key.cc] = (counts[key.cc] || 0) + 1;
    }
    return counts;
  }

  function countryChip(code, count, cssClass) {
    const span = document.createElement("span");
    span.className = cssClass;
    const img = document.createElement("img");
    img.src = `https://flagcdn.com/16x12/${code}.png`;
    img.alt = code.toUpperCase();
    img.width = 16;
    img.height = 12;
    span.append(img, document.createTextNode(`${code} ${count}`));
    return span;
  }

  function updateCaption() {
    const counts = state.world.counts || {};
    const siteKnown = Object.keys(counts).filter((cc) => cc && cc !== "xx");
    const mine = mineCountries();
    const mineNets = Object.values(mine).reduce((sum, n) => sum + n, 0);
    const samples = state.world.samples || 0;
    const since = state.world.started
      ? ` since ${new Date(state.world.started * 1000).toLocaleTimeString([], { hour12: false })}`
      : "";

    let text = `${fmt(samples)} site nets${since}`;
    if (siteKnown.length) text += ` · ${siteKnown.length} countries`;
    if (mineNets) {
      text += ` · yours: ${mineNets} in ${Object.keys(mine).length}`;
    } else if (state.keys.some((key) => !key.pow)) {
      text += " · yours: not geolocated";
    } else {
      text += " · yours: none yet";
    }
    // after a restart the tally comes from disk: say how old that sample is
    const updated = state.world.updated || 0;
    const age = updated ? Math.max(0, Date.now() / 1000 - updated) : 0;
    if (age > 900) text += ` · saved ${ago(age)} ago`;
    setText("caption", text);
  }

  // your own cracks (green) first, then the site-wide sample
  function renderChrome() {
    const box = $("countries");
    box.textContent = "";

    const mine = Object.entries(mineCountries()).sort((a, b) => b[1] - a[1]);
    for (const [cc, count] of mine) box.appendChild(countryChip(cc, count, "mine"));
    if (mine.length) {
      const sep = document.createElement("span");
      sep.className = "sep";
      sep.textContent = "·";
      box.appendChild(sep);
    }

    const site = Object.entries(state.world.counts || {})
      .filter(([cc]) => cc && cc !== "xx")
      .sort((a, b) => b[1] - a[1])
      .slice(0, 8);
    for (const [cc, count] of site) box.appendChild(countryChip(cc, count, "site"));

    const unknown = Number((state.world.counts || {}).xx || 0);
    if (unknown) {
      const span = document.createElement("span");
      span.className = "muted";
      span.textContent = `no country ${unknown}`;
      box.appendChild(span);
    }
    updateCaption();
  }

  function renderWorld(data) {
    state.world = {
      counts: data.counts || {},
      samples: data.samples || 0,
      updated: data.updated || 0,
      started: data.started || 0,
    };
    renderChrome();
  }

  let logNext = 0;
  const geoAsked = new Set();

  // make sure the server's geo cache knows your cracked BSSIDs, so "yours" shows up
  async function warmGeo() {
    if (!state.settings || !state.settings.remote_enabled) return;
    const missing = [];
    for (const key of state.keys) {
      if (!key.bssid || key.cc || geoAsked.has(key.bssid)) continue;
      geoAsked.add(key.bssid);
      missing.push(key.bssid);
      if (missing.length >= 25) break;
    }
    if (!missing.length) return;
    try {
      await fetch("/api/site/geo", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ bssids: missing }),
      });
    } catch (err) {
      /* the next state poll picks up whatever got cached */
    }
  }

  // smooth coastlines: real polygons, built once server-side from Natural Earth
  async function loadCoast() {
    try {
      const response = await fetch("/api/map/coast");
      if (!response.ok) return;
      const data = await response.json();
      if (!data.ok || !Array.isArray(data.rings) || !data.rings.length) return;
      const scale = Number(data.scale) || 100;
      COAST_RINGS = data.rings.map((ring) => {
        const points = new Array(ring.length / 2);
        for (let index = 0; index < ring.length; index += 2) {
          const lat = ((ring[index + 1] / scale) * Math.PI) / 180;
          const lon = ((ring[index] / scale) * Math.PI) / 180;
          points[index / 2] = [Math.cos(lat) * Math.sin(lon), Math.sin(lat),
                               Math.cos(lat) * Math.cos(lon)];
        }
        return points;
      });
    } catch (err) {
      /* the grid fallback stays in place */
    }
  }

  // the 1-degree grid, only needed when the polygon data is unavailable
  async function loadLand() {
    try {
      const response = await fetch("/api/map/land");
      if (!response.ok) return;
      const data = await response.json();
      if (!data.ok || !Array.isArray(data.rows) || !data.rows.length) return;
      landGrid = { step: Number(data.step) || 1, rows: data.rows };
      LAND_CELLS = buildLandCells(landGrid);
    } catch (err) {
      /* keep the built-in 5-degree mask */
    }
  }

  async function pollState() {
    try {
      const response = await fetch(`/api/state?since=${logNext}`);
      if (!response.ok) throw new Error(String(response.status));
      const data = await response.json();
      if (data.logs && typeof data.logs.next === "number") logNext = data.logs.next;
      renderRunner(data);
      state.failures = 0;
      void warmGeo();
    } catch (err) {
      state.failures += 1;
      setText("ticker", `dashboard unreachable (${err.message}) — retrying`);
    }
    setTimeout(pollState, state.failures ? 3000 : 1000);
  }

  async function pollWorld() {
    try {
      const response = await fetch("/api/site/world");
      if (response.status === 409) {
        setText("caption", "globe: site lookups are disabled in Options");
        return;
      }
      const data = await response.json();
      if (data.ok) renderWorld(data);
    } catch (err) {
      setText("caption", "globe: site unreachable");
    } finally {
      setTimeout(pollWorld, 20000);
    }
  }

  async function pollStats() {
    try {
      const response = await fetch("/api/site/stats");
      if (response.ok) {
        const data = await response.json();
        if (data.ok) renderStats(data.stats || {});
      }
    } catch (err) {
      /* keep the last numbers */
    } finally {
      setTimeout(pollStats, 60000);
    }
  }


  // -------------------------------------------------------------------- globe

  const canvas = $("globe");
  const ctx = canvas.getContext("2d");
  let lastFrame = performance.now();

  // dragging pauses the spin for this long, then it eases back into its turn
  const SPIN_RESUME_MS = 30000;
  const drag = { active: false, pointerId: null, x: 0, y: 0 };

  function resize() {
    const dpr = window.devicePixelRatio || 1;
    const width = Math.max(220, canvas.clientWidth || 400);
    const height = Math.max(220, canvas.clientHeight || Math.round(width * 0.7));
    canvas.width = Math.round(width * dpr);
    canvas.height = Math.round(height * dpr);
    ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
    state.size = { width, height };
  }

  // yaw (state.rotation, wraps) then pitch (state.tilt, from dragging)
  function project(lat, lon) {
    const la = (lat * Math.PI) / 180;
    const lo = ((lon + state.rotation) * Math.PI) / 180;
    const x = Math.cos(la) * Math.sin(lo);
    const y = Math.sin(la);
    const z = Math.cos(la) * Math.cos(lo);
    if (!state.tilt) return { x, y, z };
    const tilt = (state.tilt * Math.PI) / 180;
    return {
      x,
      y: y * Math.cos(tilt) - z * Math.sin(tilt),
      z: y * Math.sin(tilt) + z * Math.cos(tilt),
    };
  }

  // a full sweep of the sphere should follow the pointer, roughly
  function degreesPerPixel() {
    const size = state.size || { width: 400, height: 300 };
    const radius = Math.min(size.width * 0.46, size.height * 0.46, 440);
    return 180 / Math.max(120, radius);
  }

  function onPointerDown(event) {
    if (event.pointerType === "mouse" && event.button !== 0) return;
    drag.active = true;
    drag.pointerId = event.pointerId;
    drag.x = event.clientX;
    drag.y = event.clientY;
    canvas.classList.add("dragging");
    if (canvas.setPointerCapture) canvas.setPointerCapture(event.pointerId);
    state.lastInput = performance.now();
    event.preventDefault();
  }

  function onPointerMove(event) {
    if (!drag.active || event.pointerId !== drag.pointerId) return;
    const perPixel = degreesPerPixel();
    const dx = event.clientX - drag.x;
    const dy = event.clientY - drag.y;
    drag.x = event.clientX;
    drag.y = event.clientY;
    state.rotation = ((state.rotation + dx * perPixel) % 360 + 360) % 360;
    state.tilt = Math.max(-80, Math.min(80, state.tilt + dy * perPixel * 0.8));
    state.lastInput = performance.now();
    event.preventDefault();
  }

  function onPointerUp(event) {
    if (!drag.active) return;
    if (event.pointerId !== undefined && event.pointerId !== drag.pointerId) return;
    drag.active = false;
    canvas.classList.remove("dragging");
    if (canvas.releasePointerCapture && drag.pointerId !== null) {
      canvas.releasePointerCapture(drag.pointerId);
    }
    drag.pointerId = null;
    state.lastInput = performance.now();
  }

  canvas.addEventListener("pointerdown", onPointerDown);
  canvas.addEventListener("pointermove", onPointerMove);
  canvas.addEventListener("pointerup", onPointerUp);
  canvas.addEventListener("pointercancel", onPointerUp);

  function strokePath(points) {
    ctx.beginPath();
    let started = false;
    for (const point of points) {
      if (point.z <= 0) {
        started = false;
        continue;
      }
      if (!started) {
        ctx.moveTo(point.sx, point.sy);
        started = true;
      } else {
        ctx.lineTo(point.sx, point.sy);
      }
    }
    ctx.stroke();
  }

  function draw(now) {
    requestAnimationFrame(draw);

    // a drag parks the globe where you left it; 30 s later it turns again
    const idleFor = state.lastInput ? now - state.lastInput : SPIN_RESUME_MS + 1;
    const spinning = !drag.active && idleFor > SPIN_RESUME_MS;

    // ~30 fps while it turns, a lazy redraw while it is parked
    if (now - lastFrame < (spinning || drag.active ? 30 : 250)) return;
    const delta = Math.min(100, now - lastFrame) / 1000;
    lastFrame = now;

    if (spinning) {
      state.rotation = (state.rotation + delta * 6) % 360;
      if (Math.abs(state.tilt) > 0.5) {   // level it out on the way
        state.tilt -= Math.sign(state.tilt) * Math.min(Math.abs(state.tilt), delta * 20);
      }
    }

    const { width, height } = state.size || { width: 400, height: 300 };
    ctx.clearRect(0, 0, width, height);
    const cx = width / 2;
    const cy = height / 2;
    const radius = Math.min(width * 0.46, height * 0.46, 440);

    const sphere = ctx.createRadialGradient(cx - radius * 0.3, cy - radius * 0.35,
                                            radius * 0.15, cx, cy, radius);
    sphere.addColorStop(0, "rgba(28,74,102,.55)");
    sphere.addColorStop(1, "rgba(5,12,19,.92)");
    ctx.beginPath();
    ctx.arc(cx, cy, radius, 0, Math.PI * 2);
    ctx.fillStyle = sphere;
    ctx.fill();
    ctx.strokeStyle = "rgba(76,194,255,.28)";
    ctx.lineWidth = 1;
    ctx.stroke();

    // landmass. With the Natural Earth rings this is a real polygon fill with a
    // coastline stroke, clipped at the horizon; the 1-degree grid is only the
    // fallback when that data is unavailable.
    const cosR = Math.cos((state.rotation * Math.PI) / 180);
    const sinR = Math.sin((state.rotation * Math.PI) / 180);
    const cosT = Math.cos((state.tilt * Math.PI) / 180);
    const sinT = Math.sin((state.tilt * Math.PI) / 180);

    if (COAST_RINGS) {
      ctx.beginPath();
      for (const ring of COAST_RINGS) {
        const view = rotateRing(ring, cosR, sinR, cosT, sinT);
        const clipped = clipToHorizon(view);
        const count = clipped.length / 3;
        if (count < 3) continue;
        for (let index = 0; index < count; index += 1) {
          const x = cx + clipped[index * 3] * radius;
          const y = cy - clipped[index * 3 + 1] * radius;
          if (index === 0) ctx.moveTo(x, y);
          else ctx.lineTo(x, y);
        }
        ctx.closePath();
      }
      ctx.fillStyle = "rgba(96,186,224,.19)";
      ctx.fill("evenodd");                  // lakes stay open
      ctx.strokeStyle = "rgba(122,206,242,.45)";
      ctx.lineWidth = 1;
      ctx.stroke();
    } else {
      // fallback: visible cells into one path, so shared edges do not double-blend
      ctx.beginPath();
      for (const cell of LAND_CELLS) {
        let facing = true;
        const sx = [];
        const sy = [];
        for (const [x, y, z] of cell) {
          const xr = x * cosR + z * sinR;
          const zr0 = z * cosR - x * sinR;
          const yr = y * cosT - zr0 * sinT;
          const zr = y * sinT + zr0 * cosT;
          if (zr <= 0.02) {
            facing = false;
            break;
          }
          sx.push(cx + xr * radius);
          sy.push(cy - yr * radius);
        }
        if (!facing) continue;
        ctx.moveTo(sx[0], sy[0]);
        ctx.lineTo(sx[1], sy[1]);
        ctx.lineTo(sx[2], sy[2]);
        ctx.lineTo(sx[3], sy[3]);
        ctx.closePath();
      }
      ctx.fillStyle = "rgba(96,186,224,.21)";
      ctx.fill();
    }

    ctx.strokeStyle = "rgba(76,194,255,.13)";
    for (let lat = -60; lat <= 60; lat += 30) {
      const points = [];
      for (let lon = -180; lon <= 180; lon += 4) {
        const p = project(lat, lon);
        points.push({ ...p, sx: cx + p.x * radius, sy: cy - p.y * radius });
      }
      strokePath(points);
    }
    for (let lon = -180; lon < 180; lon += 30) {
      const points = [];
      for (let lat = -90; lat <= 90; lat += 4) {
        const p = project(lat, lon);
        points.push({ ...p, sx: cx + p.x * radius, sy: cy - p.y * radius });
      }
      strokePath(points);
    }

    const counts = state.world.counts || {};
    const markers = Object.entries(counts)
      .filter(([cc]) => CENTROIDS[cc])
      .sort((a, b) => a[1] - b[1]);
    const peak = Math.max(1, ...markers.map((entry) => entry[1]));

    for (const [cc, count] of markers) {
      const [lat, lon] = CENTROIDS[cc];
      const p = project(lat, lon);
      const x = cx + p.x * radius;
      const y = cy - p.y * radius;
      const size = 2 + 10 * Math.sqrt(count / peak);

      if (p.z <= 0.02) {
        // far side of the globe: a dim dot so the map never looks empty
        ctx.beginPath();
        ctx.arc(x, y, Math.max(1.2, size * 0.2), 0, Math.PI * 2);
        ctx.fillStyle = "rgba(130,180,210,0.22)";
        ctx.fill();
        continue;
      }

      const alpha = 0.3 + 0.7 * p.z;

      const glow = ctx.createRadialGradient(x, y, 0, x, y, size * 2.4);
      glow.addColorStop(0, `rgba(76,194,255,${0.28 * alpha})`);
      glow.addColorStop(1, "rgba(76,194,255,0)");
      ctx.beginPath();
      ctx.arc(x, y, size * 2.4, 0, Math.PI * 2);
      ctx.fillStyle = glow;
      ctx.fill();

      ctx.beginPath();
      ctx.arc(x, y, Math.max(1.6, size * 0.42), 0, Math.PI * 2);
      ctx.fillStyle = `rgba(170,235,255,${alpha})`;
      ctx.fill();

      ctx.font = "600 11px ui-monospace, Menlo, monospace";
      ctx.textAlign = "left";
      ctx.fillStyle = `rgba(190,235,255,${0.45 + 0.55 * p.z})`;
      ctx.fillText(`${cc.toUpperCase()} ${count}`, x + Math.max(5, size), y + 4);
    }

    // your own cracks: green rings, drawn last so they stay visible
    const mine = mineCountries();
    const minePeak = Math.max(1, ...Object.values(mine));
    for (const [cc, count] of Object.entries(mine)) {
      const centre = CENTROIDS[cc];
      if (!centre) continue;
      const p = project(centre[0], centre[1]);
      if (p.z <= 0.02) continue;
      const x = cx + p.x * radius;
      const y = cy - p.y * radius;
      const ring = 4 + 9 * Math.sqrt(count / minePeak);
      ctx.beginPath();
      ctx.arc(x, y, ring, 0, Math.PI * 2);
      ctx.strokeStyle = `rgba(82,216,138,${0.5 + 0.5 * p.z})`;
      ctx.lineWidth = 2;
      ctx.stroke();
      ctx.beginPath();
      ctx.arc(x, y, 2, 0, Math.PI * 2);
      ctx.fillStyle = "rgba(170,255,205,.95)";
      ctx.fill();
    }

    // while the spin is paused, say when it comes back
    if (!spinning && !drag.active) {
      const left = Math.max(0, Math.ceil((SPIN_RESUME_MS - idleFor) / 1000));
      ctx.font = "600 11px ui-monospace, Menlo, monospace";
      ctx.textAlign = "right";
      ctx.fillStyle = "rgba(124,143,164,.75)";
      ctx.fillText(`spins again in ${left}s`, width - 12, height - 10);
      ctx.textAlign = "left";
    }
  }


  function tickClock() {
    const now = new Date();
    setText("clock", `${now.toLocaleDateString()} ${now.toLocaleTimeString([], { hour12: false })}`);
  }

  window.addEventListener("resize", resize);
  resize();
  tickClock();
  setInterval(tickClock, 1000);
  pollState();
  pollWorld();
  pollStats();
  void (async () => {
    await loadCoast();                 // smooth polygons first
    if (!COAST_RINGS) await loadLand();  // the grid only if those are unavailable
  })();
  requestAnimationFrame(draw);
})();

