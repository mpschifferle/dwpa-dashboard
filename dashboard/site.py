"""
Read-only client for the public wpa-sec pages (country lookup, account founds).

The cracking path never imports this module: it only serves the optional "site"
features. None of these calls carries the client's work token, and the country
lookup needs no key at all.
"""

from __future__ import annotations

import json
import os
import re
import threading
import time
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

BASE_URL = "https://wpa-sec.stanev.org/"
USER_AGENT = "hc-dashboard/1.0 (local help_crack dashboard)"

# <span class="fi fi-de fi" title="de"></span>
FLAG_RE = re.compile(r'class="fi fi-([a-z]{2})\b', re.IGNORECASE)
# ... fi fi-us fi"></span></td><td class="bssid">ac919baf0e1e</td>
NET_ROW_RE = re.compile(
    r'class="fi fi-([a-z]{2})[^"]*"[^>]*>\s*</span>\s*</td>\s*<td class="bssid">([0-9a-f]{12})',
    re.IGNORECASE)
BSSID_RE = re.compile(r"^[0-9a-f]{12}$")

GEO_TTL = 30 * 24 * 3600       # a network does not move countries
GEO_TTL_UNKNOWN = 24 * 3600    # it may get geolocated later
WORLD_INTERVAL = 60            # the "last 20 nets" firehose, sampled once a minute
WORLD_RETRY = 120              # after a failed sample, wait before trying again
WATCH_LIMIT = 4000             # networks we keep an eye out for on that page


class SiteClient():
    """Cached, polite access to the public pages of wpa-sec"""

    def __init__(self, cache_file, timeout=20, pause=0.25, world_file=None):
        self.cache_file = str(cache_file)
        self.world_file = str(world_file) if world_file else os.path.join(
            os.path.dirname(self.cache_file), "world.json")
        self.timeout = timeout
        self.pause = pause
        self._lock = threading.Lock()
        self._geo = self._load()
        self._world = self._load_world()
        self._world_busy = False     # a background sample is in flight
        self._world_next = 0.0       # when a new sample is due
        self._world_error = ""       # last sampling failure, for the UI
        self._watch = {}             # bssid -> when we started watching it

    # ------------------------------------------------------------------- cache

    def _load(self):
        try:
            with open(self.cache_file, "r", encoding="utf-8") as fd:
                data = json.load(fd)
        except (OSError, ValueError):
            return {}
        return data if isinstance(data, dict) else {}

    def _save(self):
        try:
            tmp = f"{self.cache_file}.tmp"
            with open(tmp, "w", encoding="utf-8") as fd:
                json.dump(self._geo, fd, sort_keys=True)
            os.replace(tmp, self.cache_file)
        except OSError:
            pass

    def _load_world(self):
        try:
            with open(self.world_file, "r", encoding="utf-8") as fd:
                data = json.load(fd)
        except (OSError, ValueError):
            data = {}
        if not isinstance(data, dict):
            data = {}
        data.setdefault("counts", {})
        data.setdefault("samples", 0)
        data.setdefault("updated", 0)
        data.setdefault("started", 0)
        return data

    def _save_world(self):
        try:
            tmp = f"{self.world_file}.tmp"
            with open(tmp, "w", encoding="utf-8") as fd:
                json.dump(self._world, fd, sort_keys=True)
            os.replace(tmp, self.world_file)
        except OSError:
            pass

    # ---------------------------------------------------------------- world map

    def recent_page(self):
        """country codes plus (country, bssid) rows of the recent-uploads page.

        The site is the only place that knows where a network is; a net drops out
        of `?search=` once the site prunes it, so we also read the bssid that sits
        next to each flag and keep it for the networks we care about.
        """
        html = self._request("?nets").decode("utf-8", "replace")
        codes = [match.group(1).lower() for match in FLAG_RE.finditer(html)]
        rows = [(code.lower(), bssid.lower()) for code, bssid in NET_ROW_RE.findall(html)]
        return codes, rows

    def watch(self, bssids):
        """networks whose country we would like to learn from that page"""
        with self._lock:
            for bssid in bssids or ():
                bssid = (bssid or "").lower()
                if BSSID_RE.match(bssid):
                    self._watch.setdefault(bssid, time.time())
            if len(self._watch) > WATCH_LIMIT:
                recent = sorted(self._watch.items(), key=lambda item: item[1])[-WATCH_LIMIT:]
                self._watch = dict(recent)

    def world(self, force=False):
        """accumulated country tally.

        Always answers from the saved state, so a dashboard that was closed for
        days paints its globe at once instead of waiting for the network; the
        recent-uploads page is re-sampled in the background when it is due.
        `force` (the `?refresh=1` API call) samples right now and blocks.
        """
        snapshot = self._world_snapshot()
        with self._lock:
            busy = self._world_busy
            next_due = self._world_next

        due = force or time.time() >= next_due
        if not due:
            return snapshot
        if force:
            if not busy:
                return self._refresh_world() or snapshot
            # a background sample is already running: wait for it instead of
            # handing back the old tally, so ?refresh=1 means what it says
            deadline = time.time() + 20
            while time.time() < deadline:
                time.sleep(0.05)
                with self._lock:
                    running = self._world_busy      # never nest the lock
                if not running:
                    return self._world_snapshot()
            return snapshot
        self._start_world_refresh()
        return snapshot

    def _world_snapshot(self):
        """the saved tally, copied out under the lock (never touches the network)"""
        with self._lock:
            return {
                "counts": dict(self._world.get("counts") or {}),
                "samples": int(self._world.get("samples") or 0),
                "updated": float(self._world.get("updated") or 0),
                "started": float(self._world.get("started") or 0),
                "error": self._world_error,
            }

    def _start_world_refresh(self):
        """kick off a background sample, unless one is already running"""
        with self._lock:
            if self._world_busy:
                return False
            self._world_busy = True
        thread = threading.Thread(target=self._refresh_world, name="world-sample", daemon=True)
        thread.start()
        return True

    def _refresh_world(self):
        """one sample of the recent-uploads page, merged into the saved tally"""
        try:
            codes, rows = self.recent_page()
        except Exception as exc:  # pylint: disable=broad-except
            with self._lock:
                self._world_busy = False
                self._world_next = time.time() + WORLD_RETRY
                self._world_error = str(exc) or exc.__class__.__name__
            return None

        with self._lock:
            counts = dict(self._world.get("counts") or {})
            for code in codes:
                counts[code] = counts.get(code, 0) + 1
            now = time.time()
            self._world = {
                "counts": counts,
                "samples": int(self._world.get("samples") or 0) + len(codes),
                "updated": now,
                "started": float(self._world.get("started") or 0) or now,
            }
            self._save_world()

            # a watched network that shows up here just told us its country
            learned = 0
            for code, bssid in rows:
                if code == "xx" or bssid not in self._watch:
                    continue
                previous = self._geo.get(bssid) or {}
                if previous.get("cc") == code:
                    continue
                self._geo[bssid] = {"cc": code, "known": True,
                                    "cracked": previous.get("cracked"), "ts": now}
                learned += 1
            if learned:
                self._save()

            self._world_busy = False
            self._world_next = now + WORLD_INTERVAL
            self._world_error = ""
        return self._world_snapshot()

    # ----------------------------------------------------------------- requests

    def _request(self, path, data=None, headers=None):
        """GET/POST a page, returns the raw body"""
        request = Request(BASE_URL + path, data=data,
                          headers={"User-Agent": USER_AGENT, **(headers or {})})
        with urlopen(request, timeout=self.timeout) as response:
            return response.read()

    def country(self, bssid):
        """2-letter country code of a BSSID, or None when the site has none"""
        return self.lookup(bssid).get("cc")

    def cached_country(self, bssid):
        """country from the local cache only (never touches the network)"""
        with self._lock:
            entry = self._geo.get((bssid or "").lower()) or {}
        return entry.get("cc")

    def lookup(self, bssid):
        """country + whether the site knows the net and has it cracked"""
        bssid = (bssid or "").lower()
        if not BSSID_RE.match(bssid):
            return {"cc": None, "known": None, "cracked": None}

        with self._lock:
            cached = self._geo.get(bssid)
        if cached and "known" in cached:
            ttl = GEO_TTL if cached.get("cc") else GEO_TTL_UNKNOWN
            if time.time() - cached.get("ts", 0) < ttl:
                return {"cc": cached.get("cc"), "known": cached.get("known"),
                        "cracked": cached.get("cracked")}

        try:
            html = self._request(f"?search={bssid}").decode("utf-8", "replace")
        except (HTTPError, URLError, OSError):
            return {"cc": None, "known": None, "cracked": None}  # transient

        match = FLAG_RE.search(html)
        code = match.group(1).lower() if match else None
        if code == "xx":
            code = None
        entry = {
            "cc": code,
            "known": 'class="bssid"' in html,
            "cracked": ">Found<" in html,
            "ts": time.time(),
        }

        with self._lock:
            self._geo[bssid] = entry
            self._save()
        return {"cc": entry["cc"], "known": entry["known"], "cracked": entry["cracked"]}

    def lookups(self, bssids, limit=200):
        """resolve a batch of BSSIDs, reusing the cache"""
        result = {}
        for bssid in list(bssids)[:limit]:
            bssid = (bssid or "").lower()
            if not BSSID_RE.match(bssid) or bssid in result:
                continue
            result[bssid] = self.lookup(bssid)
            time.sleep(self.pause)
        return result

    def countries(self, bssids, limit=200):
        """country codes only (kept for callers that do not need the rest)"""
        return {bssid: info.get("cc") for bssid, info in self.lookups(bssids, limit).items()}

    def founds(self, key):
        """?api&dl=1 - the account potfile: bssid:mac_sta:ssid:pass"""
        raw = self._request("?api&dl=1", headers={"Cookie": f"key={key}"})
        rows = []
        for line in raw.decode("utf-8", "replace").splitlines():
            parts = line.split(":", 3)
            if len(parts) != 4:
                continue
            bssid, sta, ssid, password = parts
            rows.append({"bssid": bssid.strip().lower(), "sta": sta.strip().lower(),
                         "ssid": ssid, "password": password})
        return rows

    def stats(self):
        """?stats as a {label: value} dict"""
        html = self._request("?stats").decode("utf-8", "replace")
        text = re.sub(r"(?i)<br\s*/?>", "\n", html)
        text = re.sub(r"<[^>]+>", "", text)
        out = {}
        for line in text.splitlines():
            line = line.strip()
            if ":" not in line or len(line) > 200:
                continue
            label, _, value = line.partition(":")
            label, value = label.strip(), value.strip()
            if label and value:
                out[label] = value

        # the keyspace progress is rendered as a <dd> inside a bar, not as text
        match = re.search(r'class="done"[^>]*>\s*([\d.]+%)\s*<', html)
        if match:
            out["Current keyspace progress"] = match.group(1)
        return out
