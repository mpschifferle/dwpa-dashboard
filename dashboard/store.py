"""
Parsing of the local artifacts help_crack.py leaves behind, plus the persistent
history of cracked keys.

Nothing here touches the network: the dashboard observes what the client writes
(help_crack.res / hash / key) and what it prints, and keeps its own records.
"""

from __future__ import annotations

import json
import os
import re
import threading
import time

ANSI_RE = re.compile(r"\x1b\[[0-9;?]*[ -/]*[@-~]")
HEX12_RE = re.compile(r"[0-9a-fA-F]{12}")
JTR_HANDSHAKE_RE = re.compile(
    r"^(?P<ssid>[^:]*):\$WPAPSK\$[^:]*:(?P<sta>[0-9a-fA-F]{12}):(?P<ap>[0-9a-fA-F]{12}):"
    r"[0-9a-fA-F]{12}::(?P<ver>[^:]*):(?P<status>[^:]*):"
)

# help_crack.py log lines worth turning into statistics
KEY_LOG_RE = re.compile(r"Key for bssid ([0-9a-zA-Z]+) is: (.+)$")
DICTCOUNT_RE = re.compile(r"(?:In|De)crementing dictionary count to (\d+), last duration (\d+)s")


def strip_ansi(text):
    """remove terminal colour codes"""
    return ANSI_RE.sub("", text)


def unhex(value):
    """decode a hex encoded ssid for display"""
    try:
        return bytes.fromhex(value).decode("utf-8", "replace")
    except (TypeError, ValueError):
        return ""


def parse_res(path):
    """parse a work package resume file into a small summary"""
    try:
        with open(path, "r", encoding="utf-8") as fd:
            data = json.load(fd)
    except (OSError, ValueError) as exc:
        return {"error": str(exc)}

    if not isinstance(data, dict):
        return {"error": "unexpected resume file contents"}

    dicts = []
    for entry in data.get("dicts") or []:
        if isinstance(entry, dict):
            name = str(entry.get("dpath") or entry.get("dname") or "")
        else:
            name = str(entry)
        name = name.split("/")[-1].split("\\")[-1]
        if name:
            dicts.append(name)

    return {
        "hkey": data.get("hkey"),
        "hash_count": len(data.get("hashes") or []),
        "dicts": dicts,
        "dict_count": len(dicts),
        "has_rules": bool(data.get("rules")),
        "has_prdict": bool(data.get("prdict")),
        # the challenge package uses "dictname" and carries no hkey / dicts
        "challenge": "hkey" not in data,
        "mtime": os.path.getmtime(path),
    }



def parse_hashes(path):
    """parse help_crack.hash into displayable hash records"""
    records = []
    try:
        with open(path, "r", encoding="utf-8", errors="replace") as fd:
            lines = [line.strip() for line in fd if line.strip()]
    except OSError:
        return records

    for line in lines:
        if line.startswith("WPA*"):
            fields = line.split("*")
            if len(fields) < 6:
                continue
            pow_hash = (fields[1] == "01" and len(fields) > 8
                        and fields[6] == "" and fields[7] == "" and fields[8] == "01")
            records.append({
                "type": "PMKID" if fields[1] == "01" else "EAPOL" if fields[1] == "02" else fields[1],
                "bssid": fields[3].lower(),
                "sta": fields[4].lower(),
                "ssid": unhex(fields[5]) or fields[5],
                "pow": pow_hash,
                "raw": line,
            })
        elif "$WPAPSK$" in line:
            match = JTR_HANDSHAKE_RE.match(line)
            records.append({
                "type": "EAPOL (JtR)",
                "bssid": (match.group("ap") if match else "").lower(),
                "sta": (match.group("sta") if match else "").lower(),
                "ssid": match.group("ssid") if match else line.split(":", 1)[0],
                "raw": line,
            })
        else:
            records.append({"type": "unknown", "bssid": "", "sta": "", "ssid": "", "raw": line})

    return records


def parse_potline(line):
    """parse one hashcat / john potfile line into a key record"""
    line = line.strip()
    if not line:
        return None

    # hashcat -m 22000 outfile: "WPA*02*mic*bssid*sta*ssid*...:<password>"
    if line.startswith("WPA*"):
        head, sep, password = line.partition(":")
        if not sep:
            return None
        fields = head.split("*")
        if len(fields) < 6:
            return None
        # get_work.php injects a proof-of-work hash each round:
        #   WPA*01*<pmkid>*<random ap>*<random sta>*<real ssid>***01
        # a PMKID with an empty nonce/eapol pair and "01" as the pmkid info is
        # that challenge, not a real network.
        pow_challenge = (fields[1] == "01" and len(fields) > 8
                         and fields[6] == "" and fields[7] == "" and fields[8] == "01")
        return {
            "bssid": fields[3].lower(),
            "sta": fields[4].lower(),
            "ssid": unhex(fields[5]) or fields[5],
            "kind": "PMKID" if fields[1] == "01" else "handshake",
            "password": password,
            "pow": pow_challenge,
        }

    # john wpapsk potfile: "ssid:$WPAPSK$...#b64:sta:ap:ap::ver:status:/dev/null:<password>"
    if "$WPAPSK$" in line:
        match = JTR_HANDSHAKE_RE.match(line)
        rest = line[match.end():] if match else ""
        if rest.startswith("/dev/null:"):
            rest = rest[len("/dev/null:"):]
        if not rest:
            rest = line.rsplit(":", 1)[-1]
        return {
            "bssid": (match.group("ap") if match else "").lower(),
            "sta": (match.group("sta") if match else "").lower(),
            "ssid": match.group("ssid") if match else "",
            "kind": "handshake",
            "password": rest,
        }

    # legacy hccap potfile: "essid:ap:sta:mic:<password>"
    fields = line.split(":", 4)
    if len(fields) == 5 and HEX12_RE.fullmatch(fields[1] or "") and HEX12_RE.fullmatch(fields[2] or ""):
        return {
            "bssid": fields[1].lower(),
            "sta": fields[2].lower(),
            "ssid": fields[0],
            "kind": "handshake",
            "password": fields[4],
        }

    return None


class Store():
    """Watches help_crack.py artifacts and keeps the key history"""

    def __init__(self, settings, keys_file, keep_history=5000):
        self.settings = settings
        self.keys_file = keys_file
        self.keep_history = keep_history
        self._lock = threading.RLock()
        self.package = None
        self.hashes = []
        self.keys = []
        self.stats = {
            "sessions": 0,
            "last_duration": None,
            "total_crack_seconds": 0,
            "durations": [],
            "dictcount": None,
            "cracker": None,
        }
        self._seen = set()
        self._signatures = {}
        self._key_offset = 0
        self._load_history()

    # ------------------------------------------------------------------ history

    def _load_history(self):
        """restore the cracked key history from disk"""
        entries = []
        try:
            with open(self.keys_file, "r", encoding="utf-8") as fd:
                for line in fd:
                    line = line.strip()
                    if not line:
                        continue
                    try:
                        entry = json.loads(line)
                    except ValueError:
                        continue
                    if isinstance(entry, dict) and entry.get("password") is not None:
                        entries.append(entry)
        except OSError:
            return

        with self._lock:
            for entry in entries[-self.keep_history:]:
                self._append(entry, persist=False)

    def clear_keys(self):
        """forget all cracked keys, on disk and in memory"""
        with self._lock:
            self.keys = []
            self._seen = set()
            try:
                with open(self.keys_file, "w", encoding="utf-8"):
                    pass
            except OSError:
                pass

    def all_keys(self):
        """every remembered network key, oldest first

        The site's proof-of-work challenges are deliberately left out: they are
        fake PMKIDs, not networks, and after a few hundred work packages they would
        otherwise drown the real keys in every list and export.
        """
        with self._lock:
            return [entry for entry in self.keys if not entry.get("pow")]

    def _append(self, entry, persist=True):
        """add a key record, ignoring duplicates"""
        marker = (entry.get("bssid", ""), entry.get("password", ""))
        if marker in self._seen:
            return False
        self._seen.add(marker)
        self.keys.append(entry)
        if len(self.keys) > self.keep_history:
            self.keys = self.keys[-self.keep_history:]
        if persist:
            try:
                with open(self.keys_file, "a", encoding="utf-8") as fd:
                    fd.write(json.dumps(entry, sort_keys=True) + "\n")
            except OSError:
                pass
        return True

    def add_key(self, record, source="keyfile", hkey=None):
        """record a freshly cracked key (deduplicated)"""
        bssid = (record.get("bssid") or "").lower()
        password = record.get("password") or ""
        # help_crack.py cracks a known test handshake before starting; it is not
        # a real network, so keep it out of the history just like the client does
        if password == "aaaa1234" and bssid in ("1c7ee5e2f2d0", "0026c72e4900"):
            return False
        with self._lock:
            marker = (bssid, password)
            if marker in self._seen:
                # the pot file can prove that a log-derived entry was the server's
                # proof-of-work challenge, upgrade it if so
                if record.get("pow"):
                    self._mark_pow(bssid, password)
                return False
            entry = {
                "ts": round(time.time(), 3),
                "bssid": bssid,
                "ssid": record.get("ssid") or "",
                "password": password,
                "kind": record.get("kind") or "",
                "pow": bool(record.get("pow")),
                "hkey": hkey,
                "source": source,
            }
            return self._append(entry)

    def _mark_pow(self, bssid, password):
        """upgrade an already recorded entry to proof-of-work"""
        changed = False
        for entry in self.keys:
            if (entry.get("bssid") == bssid and entry.get("password") == password
                    and not entry.get("pow")):
                entry["pow"] = True
                changed = True
        if changed:
            self._rewrite_history()
        return changed

    def _rewrite_history(self):
        """persist the in-memory history (used when an entry is upgraded)"""
        try:
            tmp = f"{self.keys_file}.tmp"
            with open(tmp, "w", encoding="utf-8") as fd:
                for entry in self.keys:
                    fd.write(json.dumps(entry, sort_keys=True) + "\n")
            os.replace(tmp, self.keys_file)
        except OSError:
            pass

    # ---------------------------------------------------------------- log events

    def note_event(self, text, hkey=None):
        """translate a help_crack.py log line into statistics"""
        with self._lock:
            match = KEY_LOG_RE.search(text)
            if match:
                bssid = match.group(1)
                # help_crack.py reports the hash-mode field ("01"/"02") as the BSSID
                # for hashcat -m 22000 outfiles; the pot file parse below has the
                # real one, so only trust well-formed BSSIDs here
                if HEX12_RE.fullmatch(bssid):
                    self.add_key({"bssid": bssid, "password": match.group(2), "kind": "log"},
                                 source="log", hkey=hkey)

            match = DICTCOUNT_RE.search(text)
            if match:
                self.stats["dictcount"] = int(match.group(1))
                durations = self.stats["durations"]
                durations.append(int(match.group(2)))
                del durations[:-100]
                self.stats["last_duration"] = durations[-1]

            lowered = text.lower()
            if self.stats["cracker"] is None and "hashcat" in lowered:
                self.stats["cracker"] = "hashcat"
            elif self.stats["cracker"] is None and "john" in lowered:
                self.stats["cracker"] = "john"

            self.stats["sessions"] = len(self.stats["durations"])
            self.stats["total_crack_seconds"] = sum(self.stats["durations"])

    # ------------------------------------------------------------------- polling

    def _changed(self, path, key):
        """detect artifact changes by (mtime, size)"""
        try:
            stat = os.stat(path)
        except OSError:
            self._signatures.pop(key, None)
            return False
        signature = (stat.st_mtime_ns, stat.st_size)
        if self._signatures.get(key) == signature:
            return False
        self._signatures[key] = signature
        return True

    def refresh(self, hkey=None):
        """re-read the artifacts help_crack.py maintains"""
        workdir = self.settings.get("workdir")
        res_path = os.path.join(workdir, self.settings.get("res_file"))
        hash_path = os.path.join(workdir, self.settings.get("hash_file"))
        key_path = os.path.join(workdir, self.settings.get("key_file"))

        with self._lock:
            if self._changed(res_path, "res"):
                package = parse_res(res_path)
                if "error" in package:
                    package["hkey"] = (self.package or {}).get("hkey")
                self.package = package
            elif not os.path.exists(res_path):
                self.package = None

            if self._changed(hash_path, "hash"):
                self.hashes = parse_hashes(hash_path)
            elif not os.path.exists(hash_path):
                self.hashes = []

            self._refresh_keys(key_path, hkey)

    def _refresh_keys(self, path, hkey):
        """tail the pot file; help_crack.py deletes it after uploading"""
        try:
            size = os.path.getsize(path)
        except OSError:
            self._key_offset = 0
            return

        if size < self._key_offset:
            self._key_offset = 0
        if size == self._key_offset:
            return

        try:
            with open(path, "r", encoding="utf-8", errors="replace") as fd:
                fd.seek(self._key_offset)
                while True:
                    start = fd.tell()
                    line = fd.readline()
                    if not line:
                        break
                    if not line.endswith("\n"):
                        # incomplete write, retry on the next poll
                        fd.seek(start)
                        break
                    record = parse_potline(line)
                    if record:
                        self.add_key(record, source="keyfile", hkey=hkey)
                self._key_offset = fd.tell()
        except OSError:
            self._key_offset = 0

    # ------------------------------------------------------------------ snapshot

    def snapshot(self):
        """serialisable view of everything the UI needs"""
        with self._lock:
            real = [entry for entry in self.keys if not entry.get("pow")]
            return {
                "package": self.package,
                "hashes": self.hashes,
                "keys": list(reversed(real))[:500],
                "stats": dict(self.stats, durations=list(self.stats["durations"])),
                "counts": {
                    "keys": len(real),
                    "pow": len(self.keys) - len(real),
                    "hashes": len(self.hashes),
                    "session_hashes": (self.package or {}).get("hash_count", 0),
                },
            }

