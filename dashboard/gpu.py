"""
Detection of the local cracking tools and of the GPUs available to them.

The dashboard's "GPU only" mode uses this to refuse a session when no GPU
backend is visible, and to steer both hashcat (-D 2, GPU device types only) and
john (wpapsk-opencl / wpapsk-cuda formats) away from the CPU.
"""

from __future__ import annotations

import os
import re
import shutil
import subprocess
import threading
import time

GPU_TYPES = ("GPU", "ACCELERATOR")

TYPE_RE = re.compile(r"^(?:OpenCL\.Device\.Type|Backend\.Device\.Type|Type)\.*\s*:\s*(\S+)", re.I)
NAME_RE = re.compile(r"^(?:OpenCL\.Device\.Name|Backend\.Device\.Name|Name)\.*\s*:\s*(.+)$", re.I)
BLOCK_RE = re.compile(r"^(?:Backend Device ID|OpenCL\.Device\.ID)", re.I)
NO_DEVICE_RE = re.compile(r"no devices found|no opencl|cannot find any", re.I)


def which(program, extra_dirs=()):
    """locate an executable, preferring the given directories over PATH"""
    names = [program]
    if os.name == "nt" and not program.lower().endswith(".exe"):
        names = [program + ".exe", program]

    for name in names:
        for directory in extra_dirs:
            if not directory:
                continue
            candidate = os.path.join(directory, name)
            if os.path.isfile(candidate) and os.access(candidate, os.X_OK):
                return candidate
        found = shutil.which(name)
        if found:
            return found
    return None


def parse_hashcat_info(output):
    """parse the text output of `hashcat -I` into {devices, no_devices}"""
    devices = []
    current = {}

    for line in output.splitlines():
        text = line.strip()
        if BLOCK_RE.match(text):
            if current.get("type"):
                devices.append(current)
            current = {}
            continue
        match = TYPE_RE.match(text)
        if match and "type" not in current:
            current["type"] = match.group(1).upper()
            continue
        match = NAME_RE.match(text)
        if match and "name" not in current:
            current["name"] = match.group(1).strip()

    if current.get("type"):
        devices.append(current)

    return {"devices": devices, "no_devices": bool(NO_DEVICE_RE.search(output))}


def hashcat_devices(path, timeout=30):
    """run `hashcat -I` and parse its output"""
    try:
        result = subprocess.run([path, "-I"], capture_output=True, timeout=timeout,
                                text=True, errors="replace", check=False)
    except (OSError, subprocess.SubprocessError) as exc:
        return {"devices": [], "error": str(exc), "no_devices": False}

    parsed = parse_hashcat_info((result.stdout or "") + (result.stderr or ""))
    return {"devices": parsed["devices"], "error": None, "no_devices": parsed["no_devices"]}


def john_supports(path, fmt, timeout=120):
    """true when john can actually run the given wpapsk format"""
    try:
        result = subprocess.run([path, f"--format={fmt}", "--test=0"],
                                capture_output=True, timeout=timeout, check=False)
    except (OSError, subprocess.SubprocessError):
        return False
    output = (result.stdout or b"") + (result.stderr or b"")
    return b"PASS" in output and b"PMKID" in output


def gpu_block_reason(probe):
    """explain why GPU only mode cannot start, or None when a GPU is usable"""
    if not probe or probe.get("gpu_capable"):
        return None
    if not probe.get("hashcat") and not probe.get("john"):
        return ("GPU only mode is on, but neither hashcat nor john was found"
                " (Options → Detect GPU re-scans)")

    reasons = []
    if probe.get("hashcat"):
        cpus = probe.get("cpus") or []
        if cpus:
            reasons.append("hashcat sees only CPU device(s): " + ", ".join(cpus))
        elif probe.get("device_types_known"):
            reasons.append("hashcat sees no GPU device")
        else:
            reasons.append("hashcat reports no usable GPU device (run `hashcat -I` to inspect)")
    if probe.get("john"):
        reasons.append("john offers no wpapsk-opencl / wpapsk-cuda format")
    return "GPU only mode is on, but " + "; ".join(reasons)


class Probe():
    """Cached tool and GPU detection"""

    def __init__(self, settings, ttl=300):
        self.settings = settings
        self.ttl = ttl
        self._lock = threading.Lock()
        self._result = None
        self._at = 0.0
        self._running = False

    def cached(self):
        """last detection result, or None while it has never completed"""
        with self._lock:
            return dict(self._result) if self._result else None

    def invalidate(self):
        with self._lock:
            self._result = None
            self._at = 0.0

    def snapshot(self, force=False):
        """current detection result (cached for `ttl` seconds)"""
        with self._lock:
            fresh = self._result and time.time() - self._at < self.ttl
            if not force and fresh:
                return dict(self._result)
            if self._running:
                return dict(self._result) if self._result else {"pending": True}
            self._running = True

        result = None
        try:
            result = self._detect()
        finally:
            with self._lock:
                self._running = False
                if result is not None:
                    self._result = result
                    # A scan that found no tools runs no subprocesses and can be
                    # transient (installer/AV holding the binary, half-extracted
                    # folder), so never cache it - the next call retries.
                    found_tool = result.get("hashcat") or result.get("john")
                    self._at = time.time() if found_tool else 0.0

        return dict(result or {})

    def warm(self):
        """run the detection in the background (used at dashboard startup)"""
        def worker():
            try:
                self.snapshot()
            except Exception:  # pylint: disable=broad-except
                pass

        threading.Thread(target=worker, daemon=True).start()

    def _detect(self):
        script = self.settings.get("script") or "help_crack.py"
        search_dirs = []
        for candidate in (os.path.dirname(os.path.abspath(script)),
                          self.settings.get("workdir") or ""):
            if candidate and os.path.isdir(candidate) and candidate not in search_dirs:
                search_dirs.append(candidate)
                # also look inside versioned tool folders like hashcat-8.0.0/
                try:
                    entries = sorted(os.listdir(candidate))
                except OSError:
                    entries = []
                for entry in entries:
                    path = os.path.join(candidate, entry)
                    if os.path.isdir(path) and entry.lower().startswith(("hashcat", "john")):
                        search_dirs.append(path)

        hashcat = which("hashcat", search_dirs) or which("hashcat.bin", search_dirs)
        john = which("john", search_dirs)

        result = {
            "hashcat": hashcat,
            "john": john,
            "gpus": [],
            "cpus": [],
            "john_gpu_formats": [],
            "device_types_known": False,
            "notes": [],
            "checked_at": time.time(),
        }

        if hashcat:
            info = hashcat_devices(hashcat)
            if info["error"]:
                result["notes"].append(f"hashcat -I failed: {info['error']}")
            for device in info["devices"]:
                kind = (device.get("type") or "").upper()
                name = device.get("name") or "unnamed device"
                if kind in GPU_TYPES:
                    result["gpus"].append({"type": kind.title(), "name": name})
                elif kind == "CPU":
                    result["cpus"].append(name)
            result["device_types_known"] = bool(info["devices"])
            if info["no_devices"]:
                result["notes"].append("hashcat reports no usable devices")

        if john:
            for fmt in ("wpapsk-opencl", "wpapsk-cuda"):
                if john_supports(john, fmt):
                    result["john_gpu_formats"].append(fmt)

        result["gpu_capable"] = bool(result["gpus"] or result["john_gpu_formats"])
        result["cpu_only"] = not result["gpu_capable"] and bool(hashcat or john)
        return result
