/*
 * Frontend for the help_crack dashboard: polls /api/state once a second and
 * renders process state, artifacts, cracked keys and the live log.
 */
(() => {
  "use strict";

  const $ = (id) => document.getElementById(id);
  const state = {
    logNext: 0,
    logLines: [],
    keys: [],
    hashes: [],
    keySig: null,
    hashSig: null,
    filter: "",
    settings: null,
    failures: 0,
    follow: true,
    level: "all",
    geo: {},
    geoPending: new Set(),
    verdicts: new Map(),
    account: null,
  };

  // ------------------------------------------------------------------ helpers

  const setText = (id, value) => {
    const el = $(id);
    if (el && el.textContent !== String(value)) el.textContent = value;
  };

  function fmtDuration(seconds) {
    const s = Math.max(0, Math.round(seconds));
    if (s < 60) return `${s}s`;
    if (s < 3600) return `${Math.floor(s / 60)}m ${s % 60}s`;
    return `${Math.floor(s / 3600)}h ${Math.floor((s % 3600) / 60)}m`;
  }

  const timeStr = (ts) =>
    new Date((ts || 0) * 1000).toLocaleTimeString([], { hour12: false });

  function pick(obj, pattern) {
    for (const [key, value] of Object.entries(obj || {})) {
      if (pattern.test(key)) return value;
    }
    return "—";
  }

  function toast(message, kind = "info", ttl = 5000) {
    const box = $("toasts");
    const el = document.createElement("div");
    el.className = `toast ${kind}`;
    el.textContent = message;
    box.appendChild(el);
    setTimeout(() => el.remove(), ttl);
  }

  async function api(path, body) {
    const options = body === undefined ? {} : {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(body),
    };
    const response = await fetch(path, options);
    const data = await response.json().catch(() => ({}));
    if (!response.ok || data.ok === false) {
      throw new Error(data.error || `request failed (${response.status})`);
    }
    return data;
  }

  // ------------------------------------------------------------------- render

  function renderState(data) {
    const runner = data.runner;
    const store = data.store;
    const stats = store.stats || {};

    const pill = $("state-pill");
    pill.textContent = runner.running ? "running" : (runner.exit_code === null ? "idle" : `stopped · ${runner.exit_code}`);
    pill.className = `pill ${runner.running ? "running" : (runner.exit_code === null ? "" : "stopped")}`;
    setText("phase-pill", runner.phase || "—");
    setText("meta-uptime", runner.uptime ? `up ${fmtDuration(runner.uptime)}` : "");
    setText("meta-pid", runner.pid ? `pid ${runner.pid}` : "");
    $("btn-start").disabled = runner.running;
    $("btn-stop").disabled = !runner.running;
    $("stdin-input").disabled = !runner.running;

    setText("cracker-name", stats.cracker ? `${stats.cracker} detected` : "cracker not detected yet");
    setText("session-command", runner.command || "");
    $("session-command").title = runner.command || "";

    renderProbe(data.probe, runner.gpu_only);
    renderProgress(runner.progress || {});
    state.counts = store.counts || {};
    renderSession(store, stats);
    renderPackage(store.package);
    renderHashes(store.hashes || []);
    renderKeys(store.keys || []);
    void ensureCountries();
    drawChart(store.keys || []);
    renderLog(data.logs);
  }

  function renderProbe(probe, gpuOnly) {
    $("gpu-pill").hidden = !gpuOnly;

    const el = $("k-devices");
    if (!probe || probe.pending) {
      setText("k-devices", "probing…");
      el.className = "kpi-value";
      el.title = "detecting hashcat / john devices";
      return;
    }

    const gpus = (probe.gpus || []).map((gpu) => gpu.name).filter(Boolean);
    const johnFormats = probe.john_gpu_formats || [];
    const cpus = probe.cpus || [];

    if (gpus.length) {
      setText("k-devices", gpus.join(" + "));
      el.className = "kpi-value ok";
    } else if (johnFormats.length) {
      setText("k-devices", `john ${johnFormats.join(" / ")}`);
      el.className = "kpi-value ok";
    } else if (cpus.length) {
      setText("k-devices", `cpu only · ${cpus.join(", ")}`);
      el.className = "kpi-value bad";
    } else if (probe.hashcat || probe.john) {
      setText("k-devices", "no gpu device found");
      el.className = "kpi-value bad";
    } else {
      setText("k-devices", "no cracker installed");
      el.className = "kpi-value bad";
    }

    const details = [];
    if (probe.hashcat) details.push(`hashcat: ${probe.hashcat}`);
    if (probe.john) details.push(`john: ${probe.john}`);
    for (const note of probe.notes || []) details.push(note);
    if (gpuOnly && !probe.gpu_capable) details.push("gpu only mode will block Start");
    el.title = details.join("\n");
  }

  function describeProbe(probe) {
    if (!probe) return "no probe result";
    const gpus = (probe.gpus || []).map((gpu) => gpu.name).filter(Boolean);
    if (gpus.length) return `GPU detected: ${gpus.join(", ")}`;
    const johnFormats = probe.john_gpu_formats || [];
    if (johnFormats.length) return `GPU detected via john: ${johnFormats.join(", ")}`;
    if ((probe.cpus || []).length) return `CPU only: ${probe.cpus.join(", ")}`;
    return "no GPU capable cracking backend found";
  }

  function renderProgress(progress) {
    setText("k-status", progress.Status || "—");
    setText("k-speed", pick(progress, /^Speed/));
    const recovered = progress.Recovered || "—";
    setText("k-recovered", recovered);
    // hashcat's absolute ETA clock is local-time and 12/24h ambiguous; the
    // parenthesised duration is the part that is always right
    const eta = progress["Time.Estimated"] || "";
    const etaMatch = /\(([^)]+)\)\s*$/.exec(eta);
    setText("k-eta", etaMatch ? etaMatch[1] : (eta || "—"));

    const hw = [pick(progress, /^(?:HWMon|Hardware\.Mon)/), progress.Temp, progress.Util]
      .filter((v) => v && v !== "—");
    setText("k-gpu", hw.length ? hw.join(" · ") : "—");

    const bar = $("k-progress-bar");
    const text = progress.Progress || "";
    setText("k-progress", text || "—");
    const match = /\(([\d.]+)%\)/.exec(text) || /^([\d.]+)%/.exec(text);
    bar.style.width = match ? `${Math.min(100, parseFloat(match[1]))}%` : "0%";
  }

  function renderSession(store, stats) {
    const pkg = store.package || {};
    const dicts = stats.dictcount ?? (pkg.dict_count ?? null);
    setText("s-keys", store.counts.keys);
    setText("s-sessions", stats.sessions || 0);
    setText("s-last", stats.last_duration === null || stats.last_duration === undefined
      ? "—" : fmtDuration(stats.last_duration));
    setText("s-dictcount", dicts === null || dicts === undefined ? "—" : dicts);
  }

  function renderPackage(pkg) {
    const badge = $("pkg-state");
    if (!pkg) {
      badge.textContent = "idle";
      badge.className = "pill subtle";
      setText("pkg-hkey", "—");
      setText("pkg-hashes", "—");
      setText("pkg-dicts", "—");
      setText("pkg-rules", "—");
      setText("pkg-prdict", "—");
      return;
    }

    if (pkg.error) {
      badge.textContent = "unreadable";
      badge.className = "pill stopped";
      setText("pkg-hkey", pkg.hkey || "—");
      setText("pkg-hashes", pkg.error);
      return;
    }

    badge.textContent = pkg.challenge ? "challenge" : "active";
    badge.className = `pill ${pkg.challenge ? "subtle" : "running"}`;
    setText("pkg-hkey", pkg.hkey || "—");
    setText("pkg-hashes", `${pkg.hash_count} hash${pkg.hash_count === 1 ? "" : "es"}`);
    setText("pkg-dicts", (pkg.dicts || []).length
      ? `${pkg.dict_count} · ${pkg.dicts.join(", ")}` : "none");
    setText("pkg-rules", pkg.has_rules ? "custom rule set" : "no");
    setText("pkg-prdict", pkg.has_prdict ? "yes" : "no");
  }

  function renderHashes(hashes) {
    state.hashes = hashes;
    const signature = `${hashes.length}:${hashes.map((h) => h.bssid + h.type).join(",")}`;
    if (signature === state.hashSig) return;
    state.hashSig = signature;

    setText("hash-count", hashes.length);
    const body = $("hash-table").querySelector("tbody");
    body.textContent = "";

    if (!hashes.length) {
      body.appendChild(emptyRow(5, "no hashes in the current job"));
      return;
    }

    for (const hash of hashes) {
      const row = document.createElement("tr");
      row.appendChild(flagCell((state.geo[hash.bssid] || {}).cc));
      const typeCell = cell(hash.type, "mono c-type", `tag ${hash.type}`.trim());
      if (hash.pow) {
        const tag = document.createElement("span");
        tag.className = "tag POW";
        tag.textContent = "pow";
        tag.title = "server proof-of-work challenge, not a network";
        typeCell.append(document.createTextNode(" "), tag);
      }
      row.appendChild(typeCell);
      row.appendChild(cell(hash.ssid || "—", "c-ssid"));
      row.appendChild(cell(hash.bssid || "—", "mono c-bssid"));
      row.appendChild(cell(hash.sta || "—", "mono c-sta"));
      body.appendChild(row);
    }
  }

  function renderKeys(keys) {
    state.keys = keys;
    const signature = keys.map((k) => `${k.bssid}:${k.password}:${k.cc || ""}`).join("|");
    if (signature === state.keySig) return;
    state.keySig = signature;

    const needle = state.filter.trim().toLowerCase();
    const visible = needle
      ? keys.filter((k) => `${k.ssid} ${k.bssid} ${k.password}`.toLowerCase().includes(needle))
      : keys;
    const realCount = keys.length;   // the site's challenges are not in this list
    let footer = `${realCount} key${realCount === 1 ? "" : "s"}`;
    const challenges = Number((state.counts || {}).pow || 0);
    if (challenges) {
      footer += ` · ${challenges} site challenge${challenges === 1 ? "" : "s"} (POW) solved`;
    }
    if (state.account) {
      footer += ` · site: ${state.account.summary.founds} founds, ${state.account.summary.you} yours`;
    }
    const countries = {};
    for (const key of keys) {
      const cc = key.cc || (state.geo[key.bssid] || {}).cc;
      if (key.pow || !cc || cc === "xx") continue;
      countries[cc] = (countries[cc] || 0) + 1;
    }
    const countryList = Object.entries(countries).sort((a, b) => b[1] - a[1]);
    if (countryList.length) {
      footer += ` · ${countryList.length} countr${countryList.length === 1 ? "y" : "ies"}`;
    }
    setText("key-count", footer);
    for (const [cc, total] of countryList.slice(0, 8)) {
      const img = document.createElement("img");
      img.className = "flag";
      img.src = `https://flagcdn.com/16x12/${cc}.png`;
      img.alt = cc.toUpperCase();
      img.title = `${cc.toUpperCase()}: ${total} key${total === 1 ? "" : "s"}`;
      $("key-count").append(document.createTextNode(" "), img);
    }
    const body = $("key-table").querySelector("tbody");
    body.textContent = "";

    if (!visible.length) {
      body.appendChild(emptyRow(5, keys.length ? "nothing matches the filter" : "no keys cracked yet"));
      return;
    }

    for (const key of visible) {
      const row = document.createElement("tr");
      const geo = state.geo[key.bssid] || {};
      row.appendChild(flagCell(key.cc || geo.cc));
      row.appendChild(cell(timeStr(key.ts), "mono muted c-when"));
      row.appendChild(cell(key.ssid || "—", "c-ssid"));

      const bssid = cell(key.bssid || "—", "mono c-bssid");
      bssid.title = key.hkey ? `hkey ${key.hkey}` : "";
      row.appendChild(bssid);

      const password = cell(key.password || "—", "mono pw c-pw");
      const badges = keyBadges(key);
      if (badges.length) {
        password.textContent = "";
        for (const badge of badges) {
          const tag = document.createElement("span");
          tag.className = `tag ${badge.cls}`;
          tag.textContent = badge.text;
          tag.title = badge.title;
          password.append(tag, document.createTextNode(" "));
        }
        password.append(document.createTextNode(key.password || ""));
      }
      password.title = "click to copy";
      password.style.cursor = "copy";
      password.addEventListener("click", () => {
        navigator.clipboard?.writeText(key.password || "").then(
          () => toast(`copied password for ${key.bssid}`, "ok", 2500),
          () => toast("clipboard not available", "error", 2500));
      });
      row.appendChild(password);
      body.appendChild(row);
    }
  }

  function cell(text, className, badge) {
    const td = document.createElement("td");
    td.textContent = text;
    if (className) td.className = className;
    if (badge) {
      td.textContent = "";
      const span = document.createElement("span");
      span.className = badge;
      span.textContent = text;
      td.appendChild(span);
    }
    return td;
  }

  function emptyRow(cols, message) {
    const row = document.createElement("tr");
    const td = document.createElement("td");
    td.colSpan = cols;
    td.className = "empty";
    td.textContent = message;
    row.appendChild(td);
    return row;
  }

  // every row here was cracked on this machine, so "your work" is the default and
  // the badges only spell out what is different about a row
  function keyBadges(key) {
    if (key.pow) {
      return [{ text: "POW", cls: "POW",
        title: "the site's proof-of-work challenge for this package: a fake PMKID, " +
               "not a real network — so it is not counted as your work" }];
    }

    const badges = [{ text: "your work", cls: "YOU",
      title: "cracked here, on your GPU: hashcat found this password on this machine" }];
    const verdict = state.verdicts.get(`${key.bssid}::${key.password}`);

    if (verdict === "someone-else") {
      badges.push({ text: "site knew", cls: "OTHER",
        title: "the site already had this net cracked with a different password — " +
               "someone else got there first, this one is still your crack" });
    } else if (verdict === "you") {
      badges.push({ text: "uploaded", cls: "YOU",
        title: "the site credits this exact net and password to your key" });
    }
    return badges;
  }

  function flagCell(code) {
    const td = document.createElement("td");
    td.className = "cc";
    if (!code || code === "xx") {
      td.textContent = "—";
      td.classList.add("muted");
      td.title = "the site has no country for this network";
      return td;
    }

    const img = document.createElement("img");
    img.className = "flag";
    img.src = `https://flagcdn.com/16x12/${code}.png`;
    img.srcset = `https://flagcdn.com/32x24/${code}.png 2x`;
    img.width = 16;
    img.height = 12;
    img.alt = code.toUpperCase();
    img.title = code.toUpperCase();
    img.loading = "lazy";
    img.addEventListener("error", () => { td.textContent = code.toUpperCase(); });
    td.appendChild(img);
    return td;
  }

  // countries are looked up on the public wpa-sec page, one request per unknown
  // BSSID, then cached server side (dashboard/geo.json) and here
  async function ensureCountries() {
    if (!state.settings || !state.settings.remote_enabled) return;
    const missing = [];
    const consider = (bssid) => {
      if (!bssid || bssid in state.geo || state.geoPending.has(bssid)) return;
      if (!missing.includes(bssid) && missing.length < 50) missing.push(bssid);
    };
    for (const key of state.keys) consider(key.bssid);
    for (const hash of state.hashes) consider(hash.bssid);
    if (!missing.length) return;

    missing.forEach((bssid) => state.geoPending.add(bssid));
    try {
      const result = await api("/api/site/geo", { bssids: missing });
      Object.assign(state.geo, result.lookups || {});
      state.keySig = null;
      state.hashSig = null;
      renderHashes(state.hashes);
      renderKeys(state.keys);
    } catch (err) {
      if (!String(err.message).includes("disabled")) {
        toast(`country lookup failed: ${err.message}`, "error", 6000);
      }
    } finally {
      missing.forEach((bssid) => state.geoPending.delete(bssid));
    }
  }

  // ---------------------------------------------------------------------- log

  function logElement(line) {
    if (state.level === "warn" && !["warn", "error"].includes(line.kind)) return null;
    if (state.level === "error" && line.kind !== "error") return null;

    const row = document.createElement("span");
    row.className = "line";
    const ts = document.createElement("span");
    ts.className = "ts";
    ts.textContent = `${timeStr(line.ts)} `;
    const msg = document.createElement("span");
    msg.className = line.kind || "info";

    // hashcat rewrites its progress with \r on a terminal; through a pipe the
    // fragments arrive glued together ("...please be patient...Counted lines").
    // Break only for display, the stored line is left untouched.
    const text = line.text || "";
    const parts = [];
    const boundary = /\.\.\.(?=[A-Z])/g;
    let last = 0;
    let match = boundary.exec(text);
    while (match) {
      parts.push(text.slice(last, match.index + match[0].length));
      last = match.index + match[0].length;
      match = boundary.exec(text);
    }
    parts.push(text.slice(last));

    parts.forEach((part, index) => {
      if (index) msg.appendChild(document.createElement("br"));
      msg.appendChild(document.createTextNode(part));
    });

    row.append(ts, msg);
    return row;
  }

  function renderLog(logs) {
    if (!logs) return;
    if (typeof logs.next === "number") state.logNext = logs.next;
    if (!logs.lines || !logs.lines.length) return;

    state.logLines.push(...logs.lines);
    if (state.logLines.length > 2000) state.logLines = state.logLines.slice(-2000);

    const pre = $("log");
    for (const line of logs.lines) {
      const el = logElement(line);
      if (el) pre.appendChild(el);
    }
    while (pre.childElementCount > 2000) pre.firstElementChild.remove();
    if (state.follow) pre.scrollTop = pre.scrollHeight;
  }

  function rerenderLog() {
    const pre = $("log");
    pre.textContent = "";
    for (const line of state.logLines) {
      const el = logElement(line);
      if (el) pre.appendChild(el);
    }
    if (state.follow) pre.scrollTop = pre.scrollHeight;
  }

  // -------------------------------------------------------------------- chart

  const BUCKETS = 48;
  const SPAN = 12 * 3600;

  function drawChart(keys) {
    const canvas = $("chart-keys");
    if (!canvas) return;

    const width = Math.max(240, canvas.getBoundingClientRect().width || 400);
    const height = 110;
    const dpr = window.devicePixelRatio || 1;
    canvas.width = Math.round(width * dpr);
    canvas.height = Math.round(height * dpr);

    const ctx = canvas.getContext("2d");
    ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
    ctx.clearRect(0, 0, width, height);

    const step = SPAN / BUCKETS;
    const now = Date.now() / 1000;
    const counts = new Array(BUCKETS).fill(0);
    for (const key of keys || []) {
      const age = now - (key.ts || 0);
      if (age < 0 || age > SPAN) continue;
      const index = BUCKETS - 1 - Math.floor(age / step);
      if (index >= 0 && index < BUCKETS) counts[index] += 1;
    }

    let total = 0;
    const cumulative = counts.map((count) => (total += count));

    ctx.strokeStyle = "rgba(29,39,51,.9)";
    ctx.lineWidth = 1;
    for (let i = 0; i <= 2; i += 1) {
      const y = 6 + (height - 20) * (i / 2) + 0.5;
      ctx.beginPath();
      ctx.moveTo(0, y);
      ctx.lineTo(width, y);
      ctx.stroke();
    }

    const peak = cumulative[cumulative.length - 1];
    const scale = peak || 1;
    const px = (i) => (i / (BUCKETS - 1)) * (width - 2) + 1;
    const py = (value) => height - 10 - (value / scale) * (height - 26);

    const gradient = ctx.createLinearGradient(0, 0, 0, height);
    gradient.addColorStop(0, "rgba(76,194,255,.30)");
    gradient.addColorStop(1, "rgba(76,194,255,0)");
    ctx.beginPath();
    ctx.moveTo(px(0), height - 10);
    cumulative.forEach((value, i) => ctx.lineTo(px(i), py(value)));
    ctx.lineTo(px(BUCKETS - 1), height - 10);
    ctx.closePath();
    ctx.fillStyle = gradient;
    ctx.fill();

    ctx.beginPath();
    cumulative.forEach((value, i) => (i === 0 ? ctx.moveTo(px(i), py(value)) : ctx.lineTo(px(i), py(value))));
    ctx.strokeStyle = "#4cc2ff";
    ctx.lineWidth = 1.8;
    ctx.stroke();

    ctx.font = "10px ui-monospace, monospace";
    ctx.fillStyle = "#7f8fa3";
    ctx.textAlign = "left";
    ctx.fillText(`peak ${peak}`, 2, 11);
    ctx.textAlign = "right";
    ctx.fillText("now", width - 2, height - 1);
    ctx.textAlign = "left";
    ctx.fillText("12h ago", 2, height - 1);
  }

  // ------------------------------------------------------------------ controls

  const OPTION_FIELDS = ["gpu_only", "client_update", "remote_enabled", "workdir", "python", "script", "coptions", "potfile", "additional", "poll_interval"];

  function wire() {
    $("btn-start").addEventListener("click", async () => {
      try {
        const result = await api("/api/start", {});
        toast(`started: ${result.command}`, "ok", 8000);
      } catch (err) {
        toast(err.message, "error", 9000);
      }
      poll();
    });

    const stopButton = $("btn-stop");
    stopButton.title = "clean stop (Ctrl-C equivalent) — hold Shift to force kill";
    stopButton.addEventListener("click", async (event) => {
      const force = event.shiftKey;
      try {
        await api("/api/stop", { force });
        toast(force ? "force killed process tree" : "stop requested", "ok");
      } catch (err) {
        toast(err.message, "error");
      }
    });

    const dialog = $("options-dialog");
    const form = $("options-form");

    $("btn-options").addEventListener("click", () => {
      const settings = state.settings || {};
      for (const field of OPTION_FIELDS) {
        const input = form.elements[field];
        if (!input) continue;
        if (input.type === "checkbox") input.checked = Boolean(settings[field]);
        else input.value = settings[field] ?? "";
      }
      dialog.showModal();
    });

    form.addEventListener("submit", async (event) => {
      // close immediately: the dialog must never be trapped by a bad value
      event.preventDefault();
      const payload = {};
      for (const field of OPTION_FIELDS) {
        const input = form.elements[field];
        if (!input) continue;
        if (input.type === "checkbox") {
          payload[field] = input.checked;
          continue;
        }
        if (field === "poll_interval") {
          const seconds = parseFloat(input.value);
          if (Number.isFinite(seconds)) payload[field] = Math.min(30, Math.max(0.2, seconds));
          continue; // empty or nonsense: keep the current value
        }
        payload[field] = input.value;
      }
      dialog.close();
      try {
        const result = await api("/api/settings", payload);
        state.settings = result.settings;
        const changed = Object.keys(result.applied || {}).length;
        toast(`options saved${changed ? ` (${changed} changed)` : ""}`, "ok", 3000);
      } catch (err) {
        toast(err.message, "error", 6000);
      }
    });

    $("btn-options-cancel").addEventListener("click", () => dialog.close());
    $("btn-options-x").addEventListener("click", () => dialog.close());
    dialog.addEventListener("click", (event) => {
      if (event.target !== dialog) return;
      const rect = dialog.getBoundingClientRect();
      const outside = event.clientX < rect.left || event.clientX > rect.right
        || event.clientY < rect.top || event.clientY > rect.bottom;
      if (outside) dialog.close();   // click on the backdrop
    });

    $("btn-probe").addEventListener("click", async () => {
      const button = $("btn-probe");
      button.disabled = true;
      button.textContent = "Detecting…";
      try {
        const result = await api("/api/probe", {});
        renderProbe(result.probe, Boolean(state.settings && state.settings.gpu_only));
        toast(describeProbe(result.probe), result.probe.gpu_capable ? "ok" : "error", 7000);
      } catch (err) {
        toast(err.message, "error", 7000);
      } finally {
        button.disabled = false;
        button.textContent = "Detect GPU";
      }
    });

    $("stdin-input").addEventListener("keydown", async (event) => {
      if (event.key !== "Enter") return;
      const text = event.target.value;
      if (!text) return;
      event.target.value = "";
      try {
        await api("/api/input", { text });
      } catch (err) {
        toast(err.message, "error", 4000);
      }
    });

    $("btn-site").addEventListener("click", async () => {
      const button = $("btn-site");
      button.disabled = true;
      button.textContent = "…";
      try {
        const result = await api("/api/site/account", {});
        state.account = result;
        state.verdicts = new Map((result.verdicts || [])
          .map((row) => [`${row.bssid}::${row.password}`, row.verdict]));
        state.keySig = null;
        renderKeys(state.keys);
        const summary = result.summary;
        toast(`site: ${summary.founds} founds · ${summary.you} yours · ` +
              `${summary.someone_else} by someone else`, "ok", 9000);
      } catch (err) {
        toast(err.message, "error", 9000);
      } finally {
        button.disabled = false;
        button.textContent = "site";
      }
    });

    $("btn-keys-clear").addEventListener("click", async () => {
      if (!window.confirm("Forget the whole cracked key history? This only affects the dashboard copy.")) return;
      try {
        await api("/api/keys/clear", {});
        state.keySig = null;
        toast("key history cleared", "ok");
      } catch (err) {
        toast(err.message, "error");
      }
    });

    $("btn-log-clear").addEventListener("click", () => {
      state.logLines = [];
      $("log").textContent = "";
    });

    $("key-filter").addEventListener("input", (event) => {
      state.filter = event.target.value;
      state.keySig = null;
      renderKeys(state.keys);
    });

    $("log-level").addEventListener("change", (event) => {
      state.level = event.target.value;
      rerenderLog();
    });

    $("log-follow").addEventListener("change", (event) => {
      state.follow = event.target.checked;
      if (state.follow) $("log").scrollTop = $("log").scrollHeight;
    });

    let resizeTimer = null;
    window.addEventListener("resize", () => {
      clearTimeout(resizeTimer);
      resizeTimer = setTimeout(() => drawChart(state.keys), 150);
    });
  }

  // --------------------------------------------------------------------- loop

  let timer = null;

  async function poll() {
    clearTimeout(timer);
    try {
      const response = await fetch(`/api/state?since=${state.logNext}`);
      if (!response.ok) throw new Error(`state request failed (${response.status})`);
      const data = await response.json();
      state.failures = 0;
      $("live-dot").className = "live-dot on";
      if (data.settings) state.settings = data.settings;
      renderState(data);
    } catch (err) {
      state.failures += 1;
      $("live-dot").className = "live-dot off";
      if (state.failures === 3) toast("dashboard backend unreachable, retrying…", "error", 6000);
    }
    const delay = state.failures ? Math.min(8000, 1000 * state.failures) : 1000;
    timer = setTimeout(poll, delay);
  }

  wire();
  poll();
})();


