#!/usr/bin/env python3
"""
End to end self test for the dashboard.

Boots the real Flask app against dashboard/tests/mock_help_crack.py in a
temporary directory, then drives the public HTTP API and asserts the results.
hashcat is not needed and nothing is sent over the network.

    python dashboard/tests/selftest.py
"""

import json
import ntpath
import os
import shlex
import shutil
import subprocess
import sys
import tempfile
import threading
import time
import urllib.error
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from werkzeug.serving import make_server  # noqa: E402

from dashboard.app import create_app  # noqa: E402
from dashboard import landdata  # noqa: E402
from dashboard.config import Settings  # noqa: E402
from dashboard.gpu import Probe, parse_hashcat_info  # noqa: E402
from dashboard.runner import Runner  # noqa: E402
from dashboard.site import SiteClient  # noqa: E402

MOCK = Path(__file__).resolve().parent / "mock_help_crack.py"

GPU_AVAILABLE = {
    "hashcat": "C:\\tools\\hashcat.exe",
    "john": None,
    "gpus": [{"type": "Gpu", "name": "NVIDIA GeForce RTX 4070"}],
    "cpus": [],
    "john_gpu_formats": [],
    "device_types_known": True,
    "notes": [],
    "gpu_capable": True,
    "cpu_only": False,
    "checked_at": 0.0,
}

CPU_ONLY = {
    "hashcat": "C:\\tools\\hashcat.exe",
    "john": None,
    "gpus": [],
    "cpus": ["AMD Ryzen 9 5900X 12-Core Processor"],
    "john_gpu_formats": [],
    "device_types_known": True,
    "notes": [],
    "gpu_capable": False,
    "cpu_only": True,
    "checked_at": 0.0,
}

# trimmed but realistic `hashcat -I` output, used to test the device parser
GPU_INFO = """
hashcat (v6.2.6) starting in backend information mode

CUDA Info:
==========

CUDA.Version.: 12.2

Backend Device ID #1 (Alias: #2)
  Name...........: NVIDIA GeForce RTX 4070
  Processor(s)...: 46
  Clock..........: 1920
  Memory.Total...: 12282 MB (limited to 3063 MB allocatable in one block)

OpenCL Info:
============

OpenCL.Platform.ID...: 1
OpenCL.Platform.Name.: NVIDIA CUDA

OpenCL.Device.ID....: 2
OpenCL.Device.Type..: GPU
OpenCL.Device.Name..: NVIDIA GeForce RTX 4070
OpenCL.Device.Vendor.: NVIDIA Corporation
"""

CPU_INFO = """
hashcat (v6.2.6) starting in backend information mode

OpenCL Info:
============

OpenCL.Platform.ID...: 1
OpenCL.Platform.Name.: AMD Accelerated Parallel Processing

OpenCL.Device.ID....: 1
OpenCL.Device.Type..: CPU
OpenCL.Device.Name..: AMD Ryzen 9 5900X 12-Core Processor
OpenCL.Device.Vendor.: Advanced Micro Devices, Inc.
"""

NO_DEVICE_INFO = """
hashcat (v6.2.6) starting in backend information mode

No devices found.
"""

# hashcat 8.x layout, including the platform "Name...." line that must not be
# mistaken for a device name
HASHCAT8_INFO = """
hashcat (v8.0.0) starting in backend information mode

OpenCL Info:
============

OpenCL Platform ID #1
  Vendor..: NVIDIA Corporation
  Name....: NVIDIA CUDA
  Version.: OpenCL 3.0 CUDA 13.5.49

  Backend Device ID #01
    Type...........: GPU
    Vendor.ID......: 32
    Vendor.........: NVIDIA Corporation
    Name...........: NVIDIA GeForce RTX 3060
    Processor(s)...: 28
    Clock..........: 1777
"""


class FakeProbe():
    """stand-in for dashboard.gpu.Probe so the test controls GPU detection"""

    def __init__(self, result):
        self.result = result

    def cached(self):
        return dict(self.result)

    def snapshot(self, force=False):  # pylint: disable=unused-argument
        return dict(self.result)

    def invalidate(self):
        pass

    def warm(self):
        pass


class FakeStdin():
    """captures what the dashboard writes to the client's stdin"""

    def __init__(self):
        self.data = b""

    def write(self, chunk):
        self.data += chunk

    def flush(self):
        pass


class CountingProbe(Probe):
    """Probe with a stubbed detection, to check the caching rules"""

    def __init__(self, settings, result):
        super().__init__(settings)
        self.result = result
        self.calls = 0

    def _detect(self):
        self.calls += 1
        return dict(self.result)


class FakeSite():
    """stand-in for dashboard.site.SiteClient (no network in tests)"""

    def __init__(self, lookups=None, founds=None):
        self.lookup_map = lookups or {}
        self.founds_rows = founds or []
        self.calls = 0
        self.watched = []

    def watch(self, bssids):
        self.watched = [bssid for bssid in bssids or [] if bssid]

    def lookups(self, bssids, limit=200):  # pylint: disable=unused-argument
        self.calls += 1
        return {bssid: self.lookup_map.get(bssid,
                                           {"cc": None, "known": False, "cracked": False})
                for bssid in bssids}

    def countries(self, bssids, limit=200):
        return {b: info.get("cc") for b, info in self.lookups(bssids, limit).items()}

    def stats(self):
        return {"Last 24h performance": "269.12K/s", "Current contributors count": "17"}

    def world(self, force=False):  # pylint: disable=unused-argument
        return {"counts": {"de": 12, "us": 7}, "samples": 19, "updated": 0}

    def cached_country(self, bssid):
        return (self.lookup_map.get(bssid) or {}).get("cc")

    def founds(self, key):  # pylint: disable=unused-argument
        return list(self.founds_rows)


def fetch(url):
    with urllib.request.urlopen(url, timeout=20) as response:
        return response.read()


def get_json(url):
    return json.loads(fetch(url))


def post_json(url, payload):
    request = urllib.request.Request(url, data=json.dumps(payload).encode(),
                                     headers={"Content-Type": "application/json"})
    with urllib.request.urlopen(request, timeout=20) as response:
        return json.loads(response.read())


class Checks():
    """tiny assertion collector so one failure does not abort the run"""

    def __init__(self):
        self.failed = []

    def __call__(self, label, condition, detail=""):
        if condition:
            print(f"  PASS  {label}")
        else:
            print(f"  FAIL  {label}")
            if detail:
                print(f"        {detail}")
            self.failed.append(label)


def run_node_harness(check, script, label):
    """run a head-less JS test with node, when it is installed"""
    node = shutil.which("node")
    if not node:
        print(f"  SKIP  {label} (node not installed)")
        return
    harness = Path(__file__).resolve().parent / script
    try:
        result = subprocess.run([node, str(harness)], capture_output=True, text=True,
                                timeout=120, check=False)
    except OSError as exc:
        check(label, False, str(exc))
        return
    output = (result.stdout + result.stderr).strip().splitlines()
    check(label, result.returncode == 0, "\n        ".join(output[-4:]))


def run_globe_harness(check):
    """the globe's drag/pause logic, exercised through node when it is installed"""
    run_node_harness(check, "globe_drag.js", "globe drag pauses and resumes the spin")


def run_badge_harness(check):
    """the cracked-key badges, rendered through the real app.js in node"""
    run_node_harness(check, "key_badges.js", "key badges mark your work and the site challenge")


def run_gpu_harness(check):
    """the TV gpu panel, rendered through the real tv.js in node"""
    run_node_harness(check, "tv_gpu.js", "tv gpu panel splits its numbers")


def wait_for(predicate, timeout=40):
    """poll the state endpoint until predicate(state) is true"""
    deadline = time.time() + timeout
    state = None
    while time.time() < deadline:
        state = get_json(f"{BASE}/api/state?since=0")
        if predicate(state):
            return state
        time.sleep(0.25)
    return state


def main():
    global BASE  # pylint: disable=global-statement

    workdir = Path(tempfile.mkdtemp(prefix="hc-dashboard-"))
    settings = Settings(workdir / "settings.json")
    settings.update({
        "workdir": str(workdir),
        "script": str(MOCK),
        "poll_interval": 0.2,
        "python": sys.executable,
    })

    probe = FakeProbe(GPU_AVAILABLE)
    fake_site = FakeSite(
        lookups={"aabbccddeeff": {"cc": "de", "known": True, "cracked": True},
                 "1c7ee5e2f2d0": {"cc": "de", "known": True, "cracked": True}},
        founds=[
            {"bssid": "1c7ee5e2f2d0", "sta": "0026c72e4900", "ssid": "dlink",
             "password": "sunshine123"},
            {"bssid": "f81a67aa33ee", "sta": "9c5c8e112233", "ssid": "home-wifi",
             "password": "typo"},
        ])

    # real coastline grid: one square polygon instead of the Natural Earth download
    sample_land = {"type": "FeatureCollection", "features": [{
        "type": "Feature",
        "properties": {},
        "geometry": {"type": "Polygon",
                     "coordinates": [[[-10, 40], [10, 40], [10, 50], [-10, 50], [-10, 40]]]},
    }]}
    land_grid = landdata.rasterise(sample_land)
    land_file = workdir / "land.json"
    land_file.write_text(json.dumps(land_grid), encoding="utf-8")

    # the same square as coastline rings (hundredths of a degree, no duplicate close)
    coast_rings = landdata.coast_rings(sample_land)
    coast_file = workdir / "coast.json"
    coast_file.write_text(json.dumps({"scale": 100, "rings": coast_rings}), encoding="utf-8")

    app = create_app(settings,
                     keys_file=workdir / "keys.jsonl",
                     session_log=workdir / "session.log",
                     probe=probe,
                     site=fake_site,
                     land_file=land_file,
                     coast_file=coast_file)
    runner = app.extensions["dashboard"]["runner"]
    runner.start_watcher()

    server = make_server("127.0.0.1", 0, app, threaded=True)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    BASE = f"http://127.0.0.1:{server.server_port}"

    check = Checks()
    print(f"dashboard self test\n  workdir: {workdir}\n  server : {BASE}\n")

    try:
        page = fetch(f"{BASE}/").decode()
        check("index page renders", "chart-keys" in page and "log-follow" in page)
        check("gpu ui wired",
              all(marker in page for marker in
                  ('id="gpu-pill"', 'id="k-devices"', 'name="gpu_only"', 'name="client_update"',
                   'id="stdin-input"', 'id="btn-probe"')),
              [marker for marker in ('id="gpu-pill"', 'id="k-devices"', 'name="gpu_only"',
                                     'name="client_update"', 'id="stdin-input"', 'id="btn-probe"')
               if marker not in page])
        check("options dialog can always be closed",
              all(marker in page for marker in
                  ('novalidate', 'id="btn-options-cancel" type="button"',
                   'id="btn-options-x"', 'step="any"')),
              [marker for marker in ('novalidate', 'id="btn-options-cancel" type="button"',
                                     'id="btn-options-x"', 'step="any"') if marker not in page])
        check("stylesheet serves", len(fetch(f"{BASE}/static/style.css")) > 2000)
        check("javascript serves", b"renderState" in fetch(f"{BASE}/static/app.js"))
        check("health endpoint", get_json(f"{BASE}/api/health")["ok"] is True)

        state = get_json(f"{BASE}/api/state")
        check("idle before start",
              state["runner"]["running"] is False and state["runner"]["phase"] == "idle",
              state["runner"])

        # GPU only mode plumbing
        check("gpu detection exposed", (state["probe"] or {}).get("gpu_capable") is True, state["probe"])
        check("gpu only mode defaults on", state["runner"]["gpu_only"] is True, state["runner"].get("gpu_only"))
        check("gpu device filter injected into -co",
              "-D 2" in " ".join(runner._command_line(None, GPU_AVAILABLE)),  # pylint: disable=protected-access
              runner._command_line(None, GPU_AVAILABLE))  # pylint: disable=protected-access
        settings.update({"gpu_only": False})
        check("gpu device filter dropped when disabled",
              "-D 2" not in " ".join(runner._command_line(None, GPU_AVAILABLE)))  # pylint: disable=protected-access
        settings.update({"gpu_only": True})

        env, tool_dirs = runner._child_env(GPU_AVAILABLE)  # pylint: disable=protected-access
        wanted = [directory.replace("\\", "/").rstrip("/") for directory in tool_dirs]
        exported = [entry.replace("\\", "/").rstrip("/") for entry in env.get("PATH", "").split(os.pathsep)]
        check("probe tool dirs exported to PATH",
              bool(tool_dirs) and all(directory in exported for directory in wanted),
              f"wanted {wanted}, got {exported[:3]}")

        if os.name == "nt":
            # help_crack.py runs shlex.split(<path> -V), which eats backslashes
            win_env, _ = runner._child_env({"hashcat": "C:\\tools\\hashcat-8.0.0\\hashcat.exe"})
            entry = win_env["PATH"].split(os.pathsep)[0]
            rebuilt = ntpath.join(entry, "hashcat.exe")
            check("windows PATH entry survives shlex.split",
                  "\\" not in rebuilt and shlex.split(f"{rebuilt} -V") == [rebuilt, "-V"],
                  f"{entry!r} -> {rebuilt!r}")

        # the client self-update prompt is declined unless the option is enabled
        stdin_probe = FakeStdin()
        runner._stdin = stdin_probe  # pylint: disable=protected-access
        runner._handle_prompt("Update[y] or Show changelog[c]:")
        check("update prompt declined by default", stdin_probe.data == b"n\n", stdin_probe.data)
        settings.update({"client_update": True})
        stdin_probe.data = b""
        runner._handle_prompt("Update[y] or Show changelog[c]:")
        check("update prompt accepted when enabled", stdin_probe.data == b"y\n", stdin_probe.data)
        settings.update({"client_update": False})
        runner._stdin = None  # pylint: disable=protected-access

        # hashcat only prints status blocks on request; the dashboard asks for them
        status_cmd = " ".join(runner._command_line(None, GPU_AVAILABLE))  # pylint: disable=protected-access
        check("hashcat status flags injected",
              "--status" in status_cmd and "--status-timer=1" in status_cmd, status_cmd)
        settings.update({"coptions": "--status --status-timer=7"})
        custom_cmd = " ".join(runner._command_line(None, GPU_AVAILABLE))  # pylint: disable=protected-access
        check("existing --status respected",
              "--status-timer=7" in custom_cmd and "--status-timer=1" not in custom_cmd, custom_cmd)
        settings.update({"coptions": ""})
        john_cmd = " ".join(runner._command_line(  # pylint: disable=protected-access
            None, {"john": "john", "gpus": [], "john_gpu_formats": ["wpapsk-opencl"]}))
        check("no hashcat flags for john", "--status" not in john_cmd, john_cmd)

        # hashcat pads short field names with a single dot: "Hardware.Mon.#01.: ..."
        runner._handle_line(  # pylint: disable=protected-access
            "Hardware.Mon.#01.: Temp: 67c Fan: 47% Util:100% Core:1950MHz Mem:7701MHz Bus:16")
        check("hardware monitor line parsed",
              runner.status()["progress"].get("Hardware.Mon.#01", "").startswith("Temp: 67c"),
              runner.status()["progress"])

        probed = post_json(f"{BASE}/api/probe", {})
        check("gpu re-detect endpoint", probed["ok"] and probed["probe"]["gpu_capable"] is True, probed)

        # `hashcat -I` parsing (kept pure so it can be checked without hashcat)
        parsed_gpu = parse_hashcat_info(GPU_INFO)
        check("hashcat -I parses a gpu device",
              parsed_gpu["devices"] == [{"type": "GPU", "name": "NVIDIA GeForce RTX 4070"}],
              parsed_gpu["devices"])
        parsed_cpu = parse_hashcat_info(CPU_INFO)
        check("hashcat -I parses a cpu device",
              bool(parsed_cpu["devices"]) and parsed_cpu["devices"][0]["type"] == "CPU"
              and parsed_cpu["devices"][0]["name"].startswith("AMD Ryzen"), parsed_cpu["devices"])
        check("hashcat -I detects missing devices",
              parse_hashcat_info(NO_DEVICE_INFO)["no_devices"] is True
              and parse_hashcat_info(NO_DEVICE_INFO)["devices"] == [])
        check("hashcat 8.x device block parsed",
              parse_hashcat_info(HASHCAT8_INFO)["devices"]
              == [{"type": "GPU", "name": "NVIDIA GeForce RTX 3060"}],
              parse_hashcat_info(HASHCAT8_INFO)["devices"])

        # an empty scan may be transient (installer/AV) so it must not be cached
        empty_scan = dict(GPU_AVAILABLE, hashcat=None, john=None, gpus=[], cpus=[],
                          gpu_capable=False, cpu_only=False)
        retry_probe = CountingProbe(settings, empty_scan)
        retry_probe.snapshot()
        retry_probe.snapshot()
        check("scan without tools is not cached", retry_probe.calls == 2, retry_probe.calls)
        ok_probe = CountingProbe(settings, dict(GPU_AVAILABLE))
        ok_probe.snapshot()
        ok_probe.snapshot()
        check("scan with a tool is cached", ok_probe.calls == 1, ok_probe.calls)

        try:
            post_json(f"{BASE}/api/input", {"text": "1"})
            check("stdin rejected while idle", False, "input accepted with no client running")
        except urllib.error.HTTPError as exc:
            check("stdin rejected while idle", exc.code == 409, f"http {exc.code}")

        # the shipped client must resolve without touching the network
        real_settings = Settings(workdir / "settings-real.json")
        real_settings.update({"workdir": str(ROOT), "script": "help_crack.py", "python": sys.executable})
        probe_runner = Runner(real_settings, app.extensions["dashboard"]["store"])
        command = probe_runner._command_line()  # pylint: disable=protected-access
        check("real client command resolution",
              len(command) == 2 and command[1].endswith("help_crack.py") and Path(command[1]).is_file(),
              command)

        post_json(f"{BASE}/api/settings", {"script": "does-not-exist.py"})
        try:
            post_json(f"{BASE}/api/start", {})
            check("missing script rejected", False, "start accepted a missing script")
        except urllib.error.HTTPError as exc:
            check("missing script rejected", exc.code == 409, f"http {exc.code}")
        post_json(f"{BASE}/api/settings", {"script": str(MOCK)})

        # GPU only mode must refuse to start on a CPU-only host
        probe.result = CPU_ONLY
        try:
            post_json(f"{BASE}/api/start", {})
            check("gpu only blocks a cpu-only host", False, "start was accepted")
        except urllib.error.HTTPError as exc:
            body = exc.read().decode()
            check("gpu only blocks a cpu-only host",
                  exc.code == 409 and "GPU only" in body, f"http {exc.code}: {body[:140]}")
        probe.result = GPU_AVAILABLE

        started = post_json(f"{BASE}/api/start", {})
        check("start returns the command", started["ok"] and "mock_help_crack.py" in started["command"], started)
        check("spawned command pins the gpu",
              "-D 2" in runner.status()["command"], runner.status()["command"])
        check("spawned command asks for status blocks",
              "--status" in runner.status()["command"], runner.status()["command"])

        try:
            post_json(f"{BASE}/api/start", {})
            check("duplicate start refused", False, "second start was accepted")
        except urllib.error.HTTPError as exc:
            check("duplicate start refused", exc.code == 409, f"http {exc.code}")

        # ---------------------------------------------------------- live session
        mid = {}
        progress = {}

        def collected(state):
            if state["runner"]["progress"]:
                progress.update(state["runner"]["progress"])
            package = state["store"]["package"]
            if package and package.get("hkey"):
                mid.update(state["store"]["package"])
                mid["hashes"] = state["store"]["hashes"]
            return (state["store"]["counts"]["keys"] >= 2
                    and not state["runner"]["running"]
                    and state["runner"]["exit_code"] is not None)

        final = wait_for(collected)
        keys = final["store"]["keys"] if final else []

        check("two keys captured", len(keys) == 2, keys)
        passwords = sorted(key["password"] for key in keys)
        check("passwords parsed", passwords == ["00000000", "sunshine123"], passwords)
        check("key bssids parsed", all(len(key["bssid"]) == 12 for key in keys),
              [key["bssid"] for key in keys])

        check("work package parsed", mid.get("hkey") == "42f3a9c1d2e3f4a5b6c7d8e9f0a1b2c3", mid)
        check("package lists dictionaries", mid.get("dict_count") == 2, mid.get("dicts"))
        check("package hash count", mid.get("hash_count") == 2, mid.get("hash_count"))

        hashes = mid.get("hashes") or []
        check("hashes parsed from .hash file", len(hashes) == 2, hashes)
        check("ssid decoded from hex",
              sorted(hash_["ssid"] for hash_ in hashes) == ["dlink", "home-wifi"],
              [hash_["ssid"] for hash_ in hashes])
        check("hash types detected",
              sorted(hash_["type"] for hash_ in hashes) == ["EAPOL", "PMKID"],
              [hash_["type"] for hash_ in hashes])

        check("cracker progress captured", "Speed.#1" in progress, progress)
        check("progress percentage captured",
              "Progress" in progress and "%" in progress["Progress"], progress.get("Progress"))
        check("phase reached cracking or waiting",
              final["runner"]["phase"] in ("waiting", "stopped", "cracking", "preparing"),
              final["runner"]["phase"])

        stats = final["store"]["stats"]
        check("statistics collected",
              stats["cracker"] == "hashcat" and stats["dictcount"] == 2
              and stats["last_duration"] == 24, stats)

        logs = [line["text"] for line in final["logs"]["lines"]]
        check("log captured", any("Challenge cracker" in line for line in logs), logs[:5])
        check("tool prompt auto-answered",
              any("answered tool prompt with 0" in line for line in logs), logs[:12])
        check("mock honoured the selection", any("selected tool 0" in line for line in logs), logs[:12])
        check("stderr merged into log", any("Sleeping" in line or "exit" in line for line in logs),
              logs[-5:])

        # ------------------------------------------------------------ API extras
        csv_body = fetch(f"{BASE}/api/keys/export?fmt=csv").decode()
        check("csv export", csv_body.startswith("time,bssid,ssid,password") and "sunshine123" in csv_body,
              csv_body[:80])
        json_body = json.loads(fetch(f"{BASE}/api/keys/export?fmt=json"))
        check("json export", len(json_body) == 2, len(json_body))

        applied = post_json(f"{BASE}/api/settings", {"poll_interval": 1.5, "coptions": "--workload-profile 3"})
        check("settings update", applied["settings"]["poll_interval"] == 1.5
              and applied["settings"]["coptions"] == "--workload-profile 3", applied.get("applied"))

        history = (workdir / "keys.jsonl").read_text(encoding="utf-8").strip().splitlines()
        check("history persisted", len(history) == 2, len(history))

        # help_crack.py can log "Key for bssid 01" (hash-mode field) for hashcat
        # -m 22000 results; the dashboard must not record that as a network
        keys_store = app.extensions["dashboard"]["store"]
        keys_store.note_event("Key for bssid 01 is: sunshine123", hkey=None)
        check("hash-mode field rejected as bssid",
              not any(key["bssid"] == "01" for key in keys_store.all_keys()),
              [key["bssid"] for key in keys_store.all_keys()])
        keys_store.note_event("Key for bssid aabbccddeeff is: newpass", hkey=None)
        check("valid bssid from log accepted",
              any(key["bssid"] == "aabbccddeeff" and key["password"] == "newpass"
                  for key in keys_store.all_keys()))

        # the server injects a proof-of-work hash per package: random BSSID,
        # real SSID, trailing "***01". Those are not network cracks.
        # (invented values: a test must never carry anyone's real SSID or BSSID)
        pow_line = ("WPA*01*8ac36b891edca8eef49094b1afe061ac*0badc0ffee11*88cdcbbaa77f*"
                    "5465737454***01:powsecret")
        real_line = ("WPA*01*8ac36b891edca8eef49094b1afe061ac*aabbccddeeff*88cdcbbaa77f*"
                     "5465737454***:realsecret")
        from dashboard.store import parse_potline  # local import: test detail
        check("pow hash recognised", parse_potline(pow_line)["pow"] is True, parse_potline(pow_line))
        check("real hash not flagged as pow", parse_potline(real_line)["pow"] is False,
              parse_potline(real_line))
        keys_store.add_key(parse_potline(pow_line), source="keyfile")
        snap = keys_store.snapshot()
        check("pow keys excluded from cracked count",
              snap["counts"]["pow"] >= 1
              and not any(key["bssid"] == "0badc0ffee11" and not key.get("pow")
                          for key in snap["keys"]), snap["counts"])

        # country flags come from the public search page (cached server side)
        geo = post_json(f"{BASE}/api/site/geo", {"bssids": ["aabbccddeeff", "0badc0ffee11"]})
        check("country lookup endpoint",
              geo["ok"] and geo["lookups"]["aabbccddeeff"]["cc"] == "de"
              and geo["lookups"]["aabbccddeeff"]["cracked"] is True
              and geo["lookups"]["0badc0ffee11"]["known"] is False, geo)
        site_stats = get_json(f"{BASE}/api/site/stats")
        check("site stats endpoint",
              site_stats["ok"] and "performance" in str(site_stats["stats"]), site_stats)
        settings.update({"remote_enabled": False})
        try:
            post_json(f"{BASE}/api/site/geo", {"bssids": ["aabbccddeeff"]})
            check("site lookups can be disabled", False, "lookup accepted while disabled")
        except urllib.error.HTTPError as exc:
            check("site lookups can be disabled", exc.code == 409, f"http {exc.code}")
        settings.update({"remote_enabled": True})

        # account cross-check: did *I* provide the password, or someone else?
        settings.update({"site_key": ""})
        try:
            post_json(f"{BASE}/api/site/account", {})
            check("account needs a key", False, "accepted without a key")
        except urllib.error.HTTPError as exc:
            check("account needs a key", exc.code == 409, f"http {exc.code}")
        settings.update({"site_key": "a" * 32})
        account = post_json(f"{BASE}/api/site/account", {})
        verdict = {row["bssid"]: row["verdict"] for row in account["verdicts"]}
        check("account reconciliation",
              account["ok"] and verdict.get("1c7ee5e2f2d0") == "you"
              and verdict.get("f81a67aa33ee") == "someone-else"
              and account["summary"]["you"] == 1, account["summary"])
        settings.update({"site_key": ""})

        # TV mode: its own page plus the globe data
        tv_page = fetch(f"{BASE}/tv").decode()
        check("tv page renders",
              all(marker in tv_page for marker in
                  ('id="globe"', 'id="k-speed"', 'id="countries"', 'tv.js')),
              tv_page[:200])
        world = get_json(f"{BASE}/api/site/world")
        check("world tally endpoint",
              world["ok"] and world["counts"].get("de") == 12 and world["samples"] == 19, world)
        check("world tally watches our own networks",
              bool(fake_site.watched) and "1c7ee5e2f2d0" in fake_site.watched,
              fake_site.watched[:6])
        tv_js = fetch(f"{BASE}/static/tv.js").decode()
        check("tv globe loads real coastlines",
              "/api/map/land" in tv_js and "buildLandCells" in tv_js)
        check("tv globe separates your own cracks",
              "mineCountries" in tv_js and 'countryChip(cc, count, "mine")' in tv_js)
        check("tv globe can be dragged",
              all(marker in tv_js for marker in
                  ('"pointerdown"', '"pointermove"', "setPointerCapture", "state.tilt")),
              [marker for marker in ('"pointerdown"', '"pointermove"', "setPointerCapture",
                                     "state.tilt") if marker not in tv_js])
        check("tv spin resumes after 30 s",
              "SPIN_RESUME_MS = 30000" in tv_js and "spins again in" in tv_js)
        tv_css = fetch(f"{BASE}/static/tv.css").decode()
        check("globe canvas is draggable, not scrollable",
              "cursor: grab" in tv_css and "touch-action: none" in tv_css)
        run_globe_harness(check)
        run_badge_harness(check)
        run_gpu_harness(check)
        land = get_json(f"{BASE}/api/map/land")
        check("land grid endpoint",
              land["ok"] is True and land["step"] == 1 and len(land["rows"]) == 180, list(land)[:4])
        check("coastline rings extracted",
              len(coast_rings) == 1 and len(coast_rings[0]) == 8
              and coast_rings[0][:2] == [-1000, 4000], coast_rings[:1])
        coast = get_json(f"{BASE}/api/map/coast")
        check("coastline endpoint",
              coast["ok"] is True and coast["scale"] == 100
              and coast["rings"][0][:2] == [-1000, 4000], list(coast)[:4])
        check("tv globe draws the polygons",
              "/api/map/coast" in tv_js and "coastPaths" in tv_js)
        check("land rasteriser fills the polygon",
              any(start <= 180 <= end for start, end in land_grid["rows"][45]),
              land_grid["rows"][45])
        check("land rasteriser leaves the ocean empty", land_grid["rows"][90] == [],
              land_grid["rows"][90])
        settings.update({"remote_enabled": False})
        check("land grid still served from cache when lookups are off",
              get_json(f"{BASE}/api/map/land")["ok"] is True)
        settings.update({"remote_enabled": True})
        enriched = get_json(f"{BASE}/api/state")
        check("state enriches keys with country",
              any(entry.get("cc") == "de" for entry in enriched["store"]["keys"]),
              [entry.get("cc") for entry in enriched["store"]["keys"]])

        # page parsing, with the transport stubbed out
        client = SiteClient(str(workdir / "geo-test.json"), pause=0)
        client._request = lambda path, data=None, headers=None: (  # pylint: disable=protected-access
            b'<td class="bssid">x</td><span class="fi fi-de fi" title="de"></span>')
        check("flag parsed from search page", client.country("aabbccddeeff") == "de")
        client._request = lambda path, data=None, headers=None: (  # pylint: disable=protected-access
            b"1c7ee5e2f2d0:0026c72e4900:dlink:s3cret:with:colons\n")
        found_rows = client.founds("k" * 32)
        check("founds potfile parsed",
              bool(found_rows) and found_rows[0]["bssid"] == "1c7ee5e2f2d0"
              and found_rows[0]["password"] == "s3cret:with:colons", found_rows)
        client._request = lambda path, data=None, headers=None: (  # pylint: disable=protected-access
            b"<br>Total nets: 7195402<br>\nLast 24h performance: 269.12K/s<br>")
        parsed_stats = client.stats()
        check("stats page parsed",
              parsed_stats.get("Total nets") == "7195402"
              and parsed_stats.get("Last 24h performance") == "269.12K/s", parsed_stats)

        # ------------------------------------------------ restarts keep the data
        persist = workdir / "persist"
        persist.mkdir(exist_ok=True)
        geo_path = persist / "geo.json"
        world_path = persist / "world.json"

        def offline(path, data=None, headers=None):  # pylint: disable=unused-argument
            raise urllib.error.URLError("offline")

        sampled = SiteClient(str(geo_path), pause=0)
        sampled._request = lambda path, data=None, headers=None: (  # pylint: disable=protected-access
            b'<span class="fi fi-de fi"></span><span class="fi fi-us fi"></span>')
        tally = sampled.world(force=True)
        check("world sample accumulated",
              tally["counts"] == {"de": 1, "us": 1} and tally["samples"] == 2, tally)
        check("world tally written to disk", world_path.exists())

        revived = SiteClient(str(geo_path), pause=0)      # "restart", and now offline
        revived._request = offline
        began = time.time()
        restored = revived.world()
        took = time.time() - began
        check("world tally survives a restart", restored["counts"] == {"de": 1, "us": 1}, restored)
        check("restart answers from disk instead of waiting for the site", took < 0.5,
              f"{took * 1000:.0f} ms")

        deadline = time.time() + 10
        while time.time() < deadline and not revived.world()["error"]:
            time.sleep(0.05)
        after = revived.world()
        check("failed sample is reported, saved tally kept",
              "offline" in after["error"] and after["counts"] == {"de": 1, "us": 1}, after)

        geo_saved = SiteClient(str(geo_path), pause=0)
        geo_saved._request = lambda path, data=None, headers=None: (  # pylint: disable=protected-access
            b'<td class="bssid">x</td><span class="fi fi-nl fi"></span>')
        geo_saved.country("aabbccddeeff")
        geo_back = SiteClient(str(geo_path), pause=0)
        geo_back._request = offline
        check("country cache survives a restart",
              geo_back.cached_country("aabbccddeeff") == "nl",
              geo_back.cached_country("aabbccddeeff"))

        # the recent-uploads page carries bssid + flag: use it for our own nets
        learn_dir = persist / "learn"
        learn_dir.mkdir(exist_ok=True)
        rows_page = (b'<tr><td><span class="fi fi-us fi" title="us"></span></td>'
                     b'<td class="bssid">ac919baf0e1e</td><td>Verizon</td></tr>'
                     b'<tr><td><span class="fi fi-fr fi" title="fr"></span></td>'
                     b'<td class="bssid">001122334455</td><td>Orange</td></tr>')
        learner = SiteClient(str(learn_dir / "geo.json"), pause=0)
        learner._request = lambda path, data=None, headers=None: rows_page
        codes, rows = learner.recent_page()
        check("recent page rows parsed",
              codes == ["us", "fr"] and rows == [("us", "ac919baf0e1e"), ("fr", "001122334455")],
              (codes, rows))
        learner.watch(["ac919baf0e1e"])
        learner.world(force=True)
        check("watched network learns its country",
              learner.cached_country("ac919baf0e1e") == "us",
              learner.cached_country("ac919baf0e1e"))
        check("unwatched network is left alone",
              learner.cached_country("001122334455") is None,
              learner.cached_country("001122334455"))

        # the sampler runs in a background thread: hammering it must not deadlock
        thread_dir = persist / "threads"
        thread_dir.mkdir(exist_ok=True)
        fast_page = b"".join(b'<tr><td><span class="fi fi-de fi"></span></td>'
                             b'<td class="bssid">0011223344%02x</td></tr>' % index
                             for index in range(20))
        threaded = SiteClient(str(thread_dir / "geo.json"), pause=0)
        threaded._request = lambda path, data=None, headers=None: fast_page

        def hammer():
            for _ in range(25):
                threaded.world()

        workers = [threading.Thread(target=hammer) for _ in range(6)]
        for worker in workers:
            worker.start()
        for worker in workers:
            worker.join(timeout=15)
        check("world sampler survives concurrent polling",
              not any(worker.is_alive() for worker in workers))
        settled = threaded.world(force=True)
        check("world sampler does not double count",
              settled["samples"] in (20, 40)
              and settled["counts"] == {"de": settled["samples"]}, settled)

        from dashboard.store import Store  # local import: test detail
        restarted_store = Store(settings, workdir / "keys.jsonl")
        check("cracked keys survive a restart",
              any(key.get("password") == "sunshine123" for key in restarted_store.all_keys()),
              [key.get("password") for key in restarted_store.all_keys()])

        since = final["logs"]["next"]
        tail = get_json(f"{BASE}/api/state?since={since}")
        check("incremental log fetch", tail["logs"]["lines"] == [], tail["logs"]["lines"])

        # ------------------------------------------------------------- stop path
        post_json(f"{BASE}/api/settings", {"poll_interval": 0.2})
        post_json(f"{BASE}/api/start", {})
        time.sleep(0.4)
        stopped = post_json(f"{BASE}/api/stop", {"force": False})
        check("stop accepted", stopped["ok"] is True, stopped)
        after = wait_for(lambda state: not state["runner"]["running"], timeout=15)
        check("process stops on request", after["runner"]["running"] is False, after["runner"])

        cleared = post_json(f"{BASE}/api/keys/clear", {})
        check("keys cleared", cleared["ok"] is True and get_json(f"{BASE}/api/state")["store"]["counts"]["keys"] == 0)

        # a wiped working directory (Windows cleans %TEMP%) must be recreated
        gone = workdir / "gone" / "deeper"
        fresh_settings = Settings(workdir / "fresh-settings.json")
        fresh_settings.update(settings.as_dict())
        fresh_settings.update({"workdir": str(gone), "script": str(MOCK),
                               "poll_interval": 0.2, "python": sys.executable})
        fresh = Runner(fresh_settings, keys_store,
                       session_log=str(workdir / "fresh.log"),
                       probe=FakeProbe(GPU_AVAILABLE))
        try:
            fresh.start()
            check("wiped working directory is recreated", gone.is_dir(), str(gone))
            messages = [line.get("text", "") for line in fresh.logs(0).get("lines", [])]
            check("recreation is logged",
                  any("created the working directory" in text for text in messages),
                  messages[:4])
        finally:
            fresh.stop(force=True)

    finally:
        runner.stop(force=True)
        runner.stop_watcher()
        server.shutdown()

    if check.failed:
        print(f"\n{len(check.failed)} check(s) failed: {', '.join(check.failed)}")
        return 1
    print("\nall dashboard checks passed")
    return 0


if __name__ == "__main__":
    sys.exit(main())

