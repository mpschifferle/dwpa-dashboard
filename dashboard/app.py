"""
Web dashboard for the dwpa client help_crack.py.

Run it with:   python run_dashboard.py      (or)   python -m dashboard
Then open:     http://127.0.0.1:8666/

The dashboard is a local control panel: it spawns help_crack.py, streams its
console output, and parses the artifacts it leaves in the working directory.
"""

from __future__ import annotations

import argparse
import csv
import io
import json
import os
import re
import sys
import time
import webbrowser
from pathlib import Path

if __package__ in (None, ""):  # allow "python dashboard/app.py"
    sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from flask import Flask, Response, jsonify, render_template, request  # noqa: E402

from dashboard import landdata  # noqa: E402
from dashboard.config import GEO_FILE, KEYS_FILE, LAND_FILE, SESSION_LOG, Settings  # noqa: E402
from dashboard.runner import Runner  # noqa: E402
from dashboard.site import SiteClient  # noqa: E402
from dashboard.store import Store  # noqa: E402

VERSION = "1.1.0"


def _reconcile(keys, founds):
    """compare locally cracked keys with the nets the site credits to this key"""
    by_bssid = {row["bssid"]: row for row in founds}
    verdicts = []
    you = someone_else = 0
    for key in keys:
        bssid = key.get("bssid") or ""
        password = key.get("password") or ""
        if key.get("pow"):
            verdict = "pow"
        elif bssid not in by_bssid:
            verdict = "not-yours"
        elif by_bssid[bssid].get("password") == password:
            verdict = "you"
            you += 1
        else:
            verdict = "someone-else"
            someone_else += 1
        verdicts.append({"bssid": bssid, "password": password, "verdict": verdict})
    return verdicts, {"founds": len(founds), "you": you, "someone_else": someone_else}


def create_app(settings=None, store=None, keys_file=None, session_log=None,
               probe=None, site=None, land_file=None):
    """build the Flask application (factory so tests can inject settings)"""
    settings = settings or Settings()
    store = store or Store(settings, keys_file or KEYS_FILE)
    runner = Runner(settings, store, session_log=str(session_log or SESSION_LOG), probe=probe)
    site = site or SiteClient(GEO_FILE)
    land_file = land_file or LAND_FILE

    app = Flask(__name__, template_folder="templates", static_folder="static")
    app.json.sort_keys = False
    app.extensions["dashboard"] = {"settings": settings, "store": store,
                                   "runner": runner, "site": site}

    # tool / GPU detection is slow (hashcat -I, john self tests), so warm it up
    runner.gpu.warm()

    @app.get("/")
    def index():
        return render_template("index.html", version=VERSION)

    @app.get("/api/state")
    def state():
        """single polling endpoint: runner state, artifacts, keys and new logs"""
        try:
            since = int(request.args.get("since", "0"))
        except (TypeError, ValueError):
            since = 0
        snapshot = store.snapshot()
        for entry in snapshot["keys"]:
            entry["cc"] = site.cached_country(entry.get("bssid"))
        return jsonify({
            "now": round(time.time(), 3),
            "runner": runner.status(),
            "store": snapshot,
            "settings": settings.as_dict(),
            "probe": runner.gpu.cached(),
            "logs": runner.logs(since),
        })

    @app.post("/api/start")
    def start():
        payload = request.get_json(silent=True) or {}
        extra = payload.get("extra")
        if extra is not None and not isinstance(extra, list):
            return jsonify({"ok": False, "error": "extra must be a list of arguments"}), 400
        try:
            command = runner.start(extra)
        except RuntimeError as exc:
            return jsonify({"ok": False, "error": str(exc)}), 409
        except OSError as exc:
            return jsonify({"ok": False, "error": f"could not start: {exc}"}), 500
        return jsonify({"ok": True, "command": " ".join(command)})

    @app.post("/api/stop")
    def stop():
        payload = request.get_json(silent=True) or {}
        stopped = runner.stop(force=bool(payload.get("force")))
        return jsonify({"ok": stopped})

    @app.post("/api/settings")
    def update_settings():
        payload = request.get_json(silent=True) or {}
        applied = settings.update(payload)
        try:
            settings.save()
        except OSError as exc:
            return jsonify({"ok": False, "error": f"could not save settings: {exc}"}), 500
        if applied:
            runner.gpu.invalidate()
        return jsonify({"ok": True, "applied": applied, "settings": settings.as_dict()})

    @app.post("/api/probe")
    def probe_tools():
        """re-run tool / GPU detection (`hashcat -I`, john format self tests)"""
        return jsonify({"ok": True, "probe": runner.gpu.snapshot(force=True)})

    @app.post("/api/input")
    def send_input():
        """write a line to the running client's stdin"""
        payload = request.get_json(silent=True) or {}
        text = str(payload.get("text") or "")
        if not text:
            return jsonify({"ok": False, "error": "text is required"}), 400
        if not runner.send_input(text):
            return jsonify({"ok": False, "error": "client is not running"}), 409
        return jsonify({"ok": True})

    @app.post("/api/keys/clear")
    def clear_keys():
        store.clear_keys()
        return jsonify({"ok": True})

    @app.get("/api/keys/export")
    def export_keys():
        fmt = (request.args.get("fmt") or "csv").lower()
        keys = store.all_keys()
        stamp = time.strftime("%Y%m%d-%H%M%S")
        if fmt == "json":
            body = json.dumps(keys, indent=2)
            return Response(body, mimetype="application/json",
                            headers={"Content-Disposition": f"attachment; filename=wpa-keys-{stamp}.json"})

        buf = io.StringIO()
        fields = ["time", "bssid", "ssid", "password", "kind", "hkey"]
        writer = csv.DictWriter(buf, fieldnames=fields, extrasaction="ignore")
        writer.writeheader()
        for key in keys:
            row = dict(key)
            row["time"] = time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(key.get("ts") or 0))
            writer.writerow(row)
        return Response(buf.getvalue(), mimetype="text/csv",
                        headers={"Content-Disposition": f"attachment; filename=wpa-keys-{stamp}.csv"})

    @app.post("/api/site/geo")
    def site_geo():
        """country codes for BSSIDs, from the public net search page (cached)"""
        if not settings.get("remote_enabled"):
            return jsonify({"ok": False, "error": "site lookups are disabled in Options"}), 409
        payload = request.get_json(silent=True) or {}
        bssids = payload.get("bssids")
        if not isinstance(bssids, list):
            return jsonify({"ok": False, "error": "bssids must be a list"}), 400
        try:
            lookups = site.lookups([str(b) for b in bssids])
        except Exception as exc:  # pylint: disable=broad-except
            return jsonify({"ok": False, "error": f"lookup failed: {exc}"}), 502
        return jsonify({"ok": True, "lookups": lookups})

    @app.post("/api/site/account")
    def site_account():
        """nets the site credits to your key, plus a verdict per locally cracked key"""
        if not settings.get("remote_enabled"):
            return jsonify({"ok": False, "error": "site lookups are disabled in Options"}), 409
        key = (os.environ.get("WPASEC_KEY") or settings.get("site_key") or "").strip()
        if not key:
            return jsonify({"ok": False,
                            "error": "no site key: set WPASEC_KEY (setx WPASEC_KEY <key>) "
                                     "and restart the dashboard"}), 409
        if not re.fullmatch(r"[0-9a-fA-F]{32}", key):
            return jsonify({"ok": False, "error": "the site key must be 32 hex characters"}), 400
        try:
            founds = site.founds(key)
        except Exception as exc:  # pylint: disable=broad-except
            return jsonify({"ok": False, "error": f"founds download failed: {exc}"}), 502
        verdicts, summary = _reconcile(store.all_keys(), founds)
        return jsonify({"ok": True,
                        "key_source": "env" if os.environ.get("WPASEC_KEY") else "settings",
                        "summary": summary,
                        "verdicts": verdicts,
                        "founds": [{"bssid": row["bssid"], "ssid": row["ssid"]}
                                   for row in founds]})

    @app.get("/tv")
    def tv():
        return render_template("tv.html", version=VERSION)

    @app.get("/api/site/world")
    def site_world():
        """country tally accumulated from the site's recent-upload page"""
        if not settings.get("remote_enabled"):
            return jsonify({"ok": False, "error": "site lookups are disabled in Options"}), 409
        try:
            wanted = [entry.get("bssid") for entry in store.all_keys()]
            wanted += [entry.get("bssid") for entry in store.snapshot().get("hashes") or []]
            if hasattr(site, "watch"):
                site.watch(wanted)
            data = site.world(force=request.args.get("refresh") == "1")
        except Exception as exc:  # pylint: disable=broad-except
            return jsonify({"ok": False, "error": f"world lookup failed: {exc}"}), 502
        payload = {"ok": True, "counts": data.get("counts") or {},
                   "samples": data.get("samples") or 0,
                   "updated": data.get("updated") or 0,
                   "started": data.get("started") or 0}
        if data.get("error"):
            payload["error"] = data["error"]       # sampling failed, saved tally is shown
        return jsonify(payload)

    @app.get("/api/map/land")
    def map_land():
        """1-degree land grid for the globe, built once from Natural Earth"""
        data = landdata.load(str(land_file))
        built = False
        if data is None:
            if not settings.get("remote_enabled"):
                return jsonify({"ok": False,
                                "error": "land grid not built yet and downloads are off"}), 409
            try:
                data, built = landdata.ensure(str(land_file))
            except Exception as exc:  # pylint: disable=broad-except
                return jsonify({"ok": False, "error": f"land data failed: {exc}"}), 502
            if not data:
                return jsonify({"ok": False, "error": "land data unavailable"}), 503
        return jsonify({"ok": True, "built": built, "step": data.get("step") or 1,
                        "rows": data.get("rows") or []})

    @app.get("/api/site/stats")
    def site_stats():
        """the public statistics page as label/value pairs"""
        if not settings.get("remote_enabled"):
            return jsonify({"ok": False, "error": "site lookups are disabled in Options"}), 409
        try:
            return jsonify({"ok": True, "stats": site.stats()})
        except Exception as exc:  # pylint: disable=broad-except
            return jsonify({"ok": False, "error": f"stats failed: {exc}"}), 502

    @app.get("/api/health")
    def health():
        return jsonify({"ok": True, "version": VERSION, "running": runner.is_running()})

    return app


def main(argv=None):
    """command line entry point"""
    parser = argparse.ArgumentParser(description="web dashboard for the dwpa client help_crack.py")
    parser.add_argument("--host", help="listen address, default 127.0.0.1 (localhost only)")
    parser.add_argument("--port", type=int, help="listen port, default 8666")
    parser.add_argument("--workdir", help="directory to run help_crack.py in")
    parser.add_argument("--settings", help="path to an alternative settings.json")
    parser.add_argument("--open", action="store_true", help="open the dashboard in a browser")
    args = parser.parse_args(argv)

    settings = Settings(args.settings)
    overrides = {}
    if args.host:
        overrides["host"] = args.host
    if args.port:
        overrides["port"] = args.port
    if args.workdir:
        overrides["workdir"] = os.path.abspath(args.workdir)
    if overrides:
        settings.update(overrides)
        settings.save()

    app = create_app(settings)
    runner = app.extensions["dashboard"]["runner"]
    runner.start_watcher()

    host = settings.get("host")
    port = int(settings.get("port"))
    shown = "127.0.0.1" if host in ("0.0.0.0", "::", "") else host
    url = f"http://{shown}:{port}/"

    print(f"help_crack dashboard {VERSION}")
    print(f"  url        : {url}")
    print(f"  workdir    : {os.path.abspath(settings.get('workdir'))}")
    print(f"  interpreter: {settings.get('python')}")
    if host not in ("127.0.0.1", "localhost", "::1"):
        print("  warning    : listening on a non-loopback address, there is no authentication")
    print("  Ctrl-C to quit the dashboard (a running help_crack is stopped too)")

    if args.open:
        webbrowser.open(url)

    try:
        app.run(host=host, port=port, threaded=True, debug=False, use_reloader=False)
    finally:
        runner.stop()
        runner.stop_watcher()

    return 0


if __name__ == "__main__":
    sys.exit(main())
