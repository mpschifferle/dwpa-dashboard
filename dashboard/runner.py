"""
Supervises the help_crack.py process and turns its console output into state
the dashboard can poll.

The client is started in its own process group so that a Stop request reaches
hashcat / john as well (the same thing Ctrl-C does in a terminal).
"""

from __future__ import annotations

import os
import re
import signal
import subprocess
import sys
import tempfile
import threading
import time
from collections import deque

from dashboard.config import BASE_DIR
from dashboard.gpu import Probe, gpu_block_reason
from dashboard.store import strip_ansi

CREATE_NEW_PROCESS_GROUP = getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0x00000200)

# hashcat/john print "Key........: value" blocks; they are progress, not log lines.
# A single dot is legal padding too: "Hardware.Mon.#01.: Temp: 67c" is 16 chars
# of name, so hashcat pads it with one dot instead of a full run.
STATUS_RE = re.compile(r"^([A-Za-z][A-Za-z0-9._#]*?)\.+:\s?(.*)$")

# help_crack.py prompts: tool menu ("Index:") and the self-update question.
# Both are printed without a trailing newline, so they are matched on the tail
# of the read buffer.
PROMPT_RE = re.compile(r"(Index:|changelog\[c\]:)\s*$", re.IGNORECASE)
MENU_RE = re.compile(r"^(\d+):\s+(.+)$")
# explicit device flags the user already set, we must not fight over them
DEVICE_FLAG_RE = re.compile(r"(?:^|\s)(?:-D|--backend-devices(?:-types)?)(?:\s|=|$)")
# hashcat only prints periodic status blocks when asked to; without them the
# dashboard has nothing to show while the GPU is busy
STATUS_FLAG_RE = re.compile(r"(?:^|\s)(?:--status|--status-json)(?:\s|=|$)")
QUIET_FLAG_RE = re.compile(r"(?:^|\s)(?:-q|--quiet)(?:\s|=|$)")

PHASE_RULES = (
    (re.compile(r"challenge cracker", re.IGNORECASE), "challenge"),
    (re.compile(r"session resume", re.IGNORECASE), "resume"),
    (re.compile(r"downloading|extracting|preparing dictionaries", re.IGNORECASE), "preparing"),
    (re.compile(r"no suitable nets|sleeping", re.IGNORECASE), "waiting"),
    (re.compile(r"user exit requested", re.IGNORECASE), "stopping"),
    (re.compile(r"\bfail\b|couldn't|died with code", re.IGNORECASE), "error"),
    (re.compile(r"^(?:hashcat|john) \(|starting autotune|initializing bridges", re.IGNORECASE), "cracking"),
)

SESSION_LOG_LIMIT = 5 * 1024 * 1024


class Runner():
    """Owns the help_crack.py subprocess, its log buffer and the artifact watcher"""

    def __init__(self, settings, store, session_log=None, probe=None):
        self.settings = settings
        self.store = store
        self.session_log = session_log
        self.gpu = probe or Probe(settings)
        self._lock = threading.RLock()
        self._proc = None
        self._stdin = None
        self._stop_requested = False
        self._logs = deque(maxlen=max(100, int(settings.get("log_capacity") or 5000)))
        self._next_id = 1
        self._phase = "idle"
        self._phase_since = time.time()
        self._progress = {}
        self._last_progress_at = None
        self._started_at = None
        self._exit_code = None
        self._command = []
        self._probe = None
        self._tool_menu = {}
        self._watch_stop = threading.Event()
        self._watcher = None

    # ------------------------------------------------------------------- helpers

    @property
    def current_hkey(self):
        package = self.store.package or {}
        return package.get("hkey")

    def is_running(self):
        with self._lock:
            return self._proc is not None and self._proc.poll() is None

    def _set_phase(self, phase):
        with self._lock:
            if phase != self._phase:
                self._phase = phase
                self._phase_since = time.time()

    def _log(self, text, kind="info"):
        with self._lock:
            entry = {"id": self._next_id, "ts": round(time.time(), 3), "text": text, "kind": kind}
            self._next_id += 1
            self._logs.append(entry)
        if self.session_log:
            try:
                with open(self.session_log, "a", encoding="utf-8") as fd:
                    fd.write(f"{time.strftime('%Y-%m-%d %H:%M:%S')} [{kind}] {text}\n")
            except OSError:
                pass

    def _command_line(self, extra=None, probe=None):
        """build the help_crack.py command from the saved settings"""
        python = self.settings.get("python") or sys.executable
        script = self.settings.get("script") or "help_crack.py"
        if not os.path.isabs(script):
            candidate = os.path.join(BASE_DIR, script)
            if not os.path.exists(candidate):
                candidate = os.path.join(self.settings.get("workdir"), script)
            script = candidate

        cmd = [python, script]
        coptions = self._effective_coptions(probe or {})
        if coptions:
            # the "=" form keeps values that start with a dash unambiguous
            cmd.append(f"--coptions={coptions}")
        for option, key in (("--potfile", "potfile"),
                            ("--additional", "additional")):
            value = (self.settings.get(key) or "").strip()
            if value:
                cmd.append(f"{option}={value}")
        if extra:
            cmd.extend(extra)
        return cmd

    def _effective_coptions(self, probe):
        """user options, plus what the dashboard needs (GPU pinning, live status)"""
        coptions = (self.settings.get("coptions") or "").strip()
        if not probe.get("hashcat"):
            # without hashcat, john is steered to an -opencl/-cuda format instead
            return coptions

        extra = []
        if self.settings.get("gpu_only") and not DEVICE_FLAG_RE.search(coptions):
            extra.append("-D 2")
        if not STATUS_FLAG_RE.search(coptions) and not QUIET_FLAG_RE.search(coptions):
            extra.extend(["--status", "--status-timer=1"])
        if not extra:
            return coptions
        return " ".join([coptions] + extra).strip()

    def _child_env(self, probe):
        """environment for the client, with the probe-discovered tools on PATH"""
        env = dict(os.environ)
        env["PYTHONUNBUFFERED"] = "1"
        tool_dirs = []
        for tool in (probe.get("hashcat"), probe.get("john")):
            if not tool:
                continue
            directory = os.path.dirname(os.path.abspath(tool))
            if directory and directory not in tool_dirs:
                tool_dirs.append(directory)

        if tool_dirs:
            if os.name == "nt":
                # help_crack.py builds its cracker command with shlex.split(), which
                # swallows backslashes ("C:\\x\\hashcat.exe" becomes "C:xhashcat.exe").
                # A forward-slash PATH entry ending in "/" makes os.path.join() return
                # "C:/x/hashcat.exe", which survives the round trip.
                entries = [directory.replace("\\", "/").rstrip("/") + "/" for directory in tool_dirs]
            else:
                entries = list(tool_dirs)
            env["PATH"] = os.pathsep.join(entries + [os.environ.get("PATH", "")])

        return env, tool_dirs

    # --------------------------------------------------------------------- start

    def start(self, extra=None):
        """spawn help_crack.py; returns the command line used"""
        with self._lock:
            if self._proc is not None and self._proc.poll() is None:
                raise RuntimeError("help_crack is already running")

        workdir = os.path.abspath(self.settings.get("workdir"))
        if not os.path.isdir(workdir):
            # a workdir under %TEMP% gets wiped by the OS between runs; a missing
            # folder is no reason to refuse to start, just make it again
            try:
                os.makedirs(workdir, exist_ok=True)
            except OSError as exc:
                raise RuntimeError(f"working directory does not exist and cannot be "
                                   f"created: {workdir} ({exc})") from exc
            self._log(f"created the working directory {workdir}", "ok")
        if os.path.abspath(workdir).startswith(os.path.abspath(tempfile.gettempdir())):
            # worth saying out loud: the OS may delete this mid-run
            self._log("note: the working directory is inside the temp folder, "
                      "which Windows may clean up; pick a normal folder in Options",
                      "warn")

        # GPU only mode: never start the client unless a GPU backend is visible
        probe = self.gpu.snapshot()
        if self.settings.get("gpu_only"):
            reason = gpu_block_reason(probe)
            if reason:
                raise RuntimeError(reason)

        cmd = self._command_line(extra, probe)
        if not os.path.exists(cmd[1]):
            raise RuntimeError(f"help_crack script not found: {cmd[1]}")

        env, tool_dirs = self._child_env(probe)

        kwargs = {}
        if os.name == "nt":
            kwargs["creationflags"] = CREATE_NEW_PROCESS_GROUP
        else:
            kwargs["start_new_session"] = True

        proc = subprocess.Popen(  # pylint: disable=consider-using-with
            cmd, cwd=workdir, env=env,
            stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
            bufsize=0, **kwargs)

        with self._lock:
            self._proc = proc
            self._stdin = proc.stdin
            self._probe = probe
            self._tool_menu = {}
            self._command = cmd
            self._started_at = time.time()
            self._exit_code = None
            self._stop_requested = False
            self._progress = {}
            self._last_progress_at = None
            self._phase = "starting"
            self._phase_since = time.time()

        self._rotate_session_log()
        self._log(f"started pid {proc.pid} in {workdir}", "ok")
        self._log(" ".join(cmd), "cmd")
        if self.settings.get("gpu_only"):
            names = [gpu.get("name", "") for gpu in probe.get("gpus") or []]
            if not names:
                names = [f"john --format={fmt}" for fmt in probe.get("john_gpu_formats") or []]
            self._log(f"GPU only mode, devices: {', '.join(names) or 'none detected'}", "ok" if names else "warn")
        else:
            self._log("GPU only mode is off, the cracker may use the CPU", "warn")
        if tool_dirs:
            self._log(f"tool directories added to PATH: {', '.join(tool_dirs)}", "info")
        threading.Thread(target=self._read_loop, args=(proc,), daemon=True).start()
        return cmd

    def _rotate_session_log(self):
        if not self.session_log:
            return
        try:
            if os.path.getsize(self.session_log) > SESSION_LOG_LIMIT:
                os.replace(self.session_log, self.session_log + ".1")
        except OSError:
            pass

    def stop(self, force=False):
        """ask help_crack (and its cracker) to exit"""
        with self._lock:
            proc = self._proc
            if proc is None or proc.poll() is not None:
                return False
            self._stop_requested = True
            self._phase = "stopping"
            self._phase_since = time.time()

        if force:
            self._log("force killing process tree", "warn")
        else:
            self._log("stop requested, sending interrupt to process group", "warn")
        self._signal_group(proc, force)
        return True

    def _signal_group(self, proc, force):
        """signal the whole process group (help_crack + hashcat/john)"""
        try:
            if os.name == "nt":
                if force:
                    subprocess.run(["taskkill", "/F", "/T", "/PID", str(proc.pid)],
                                   capture_output=True, check=False)
                else:
                    proc.send_signal(signal.CTRL_BREAK_EVENT)
            else:
                os.killpg(os.getpgid(proc.pid), signal.SIGKILL if force else signal.SIGINT)
        except (OSError, ValueError, AttributeError):
            try:
                proc.kill()
            except OSError:
                pass

    # ---------------------------------------------------------------- output pump

    def _read_loop(self, proc):
        """consume stdout, splitting on both \n and \r (progress bars)"""
        buf = b""
        while True:
            try:
                chunk = proc.stdout.read(4096)
            except (OSError, ValueError):
                break
            if not chunk:
                break
            buf += chunk
            parts = re.split(rb"[\r\n]+", buf)
            buf = parts.pop()
            for part in parts:
                line = part.decode("utf-8", "replace")
                if line.strip():
                    self._handle_line(line)
            tail = buf.decode("utf-8", "replace")
            if tail and PROMPT_RE.search(tail):
                # help_crack waits on input() for these, answer them right away
                self._handle_prompt(tail.strip())
                buf = b""
            if len(buf) > 8192:
                self._handle_line(buf.decode("utf-8", "replace"))
                buf = b""

        if buf.strip() and not PROMPT_RE.search(buf.decode("utf-8", "replace")):
            self._handle_line(buf.decode("utf-8", "replace"))

        code = proc.wait()
        try:
            if proc.stdin:
                proc.stdin.close()
        except (OSError, ValueError):
            pass

        with self._lock:
            if self._proc is proc:
                self._stdin = None
        with self._lock:
            if self._proc is not proc:
                return
            self._exit_code = code
            self._started_at = self._started_at or time.time()
            self._phase = "stopped"
            self._phase_since = time.time()
            self._progress = {}

        if code == 0:
            self._log("help_crack exited cleanly", "ok")
        elif self._stop_requested:
            self._log(f"help_crack stopped (exit code {code})", "warn")
        else:
            self._log(f"help_crack died with exit code {code}", "error")

    def _handle_line(self, raw):
        """classify one line of help_crack / cracker output"""
        text = strip_ansi(raw).strip()
        if not text:
            return

        menu = MENU_RE.match(text)
        if menu:
            with self._lock:
                self._tool_menu[int(menu.group(1))] = menu.group(2).strip()

        status = STATUS_RE.match(text)
        if status:
            self._set_progress(status.group(1).rstrip(" ."), status.group(2))
            return

        lowered = text.lower()
        if any(word in lowered for word in ("fail", "exception", "died", "traceback", "error")):
            kind = "error"
        elif any(word in lowered for word in ("warning", "not found", "mismatch", "unsupported")):
            kind = "warn"
        elif "key for bssid" in lowered or "updated" in lowered or "cleanly" in lowered:
            kind = "ok"
        else:
            kind = "info"

        self._log(text, kind)
        if "hashcat or john not found" in lowered:
            self._log("the client could not resolve the cracker binary; check the "
                      "tool path reported by Options → Detect GPU", "error")
        if "outdated nvidia nvrtc" in lowered or "falling back to opencl" in lowered:
            self._log("hashcat is on the slower OpenCL backend; if you just installed a "
                      "newer CUDA toolkit, restart the dashboard so it inherits the new PATH", "warn")
        if re.match(r"key for bssid (?:01|02) is:", lowered):
            self._log("note: help_crack.py reported the hash-mode field as the BSSID; "
                      "this build mis-parses hashcat -m 22000 outfiles, so the site "
                      "cannot attribute this key (the dashboard shows the real BSSID)", "warn")
        for pattern, phase in PHASE_RULES:
            if pattern.search(text):
                self._set_phase(phase)
                break
        self.store.note_event(text, hkey=self.current_hkey)

    def _set_progress(self, key, value):
        """merge one cracker status field, e.g. Speed.#1 = 120 kH/s"""
        value = (value or "")[:300]
        with self._lock:
            previous = self._progress.get(key)
            if len(self._progress) < 80 or key in self._progress:
                self._progress[key] = value
            self._last_progress_at = time.time()
            if self._phase != "cracking":
                self._phase = "cracking"
                self._phase_since = time.time()
        if key == "Status" and previous != value:
            self._log(f"cracker status: {value}", "info")

    # --------------------------------------------------------------- interactive

    def send_input(self, text):
        """write a line to the client's stdin (manual prompt answering)"""
        return self._write_stdin(text)

    def _write_stdin(self, text):
        with self._lock:
            stream = self._stdin
        if stream is None:
            return False
        try:
            stream.write(f"{text}\n".encode("utf-8"))
            stream.flush()
            return True
        except (OSError, ValueError):
            return False

    def _handle_prompt(self, text):
        """answer help_crack's interactive prompts so the client never stalls"""
        self._log(text, "warn")
        lowered = text.lower()

        if "index:" in lowered:
            choice, label = self._pick_tool()
            if choice is None:
                self._log("no GPU capable cracker in the menu, stopping instead of"
                          " falling back to the CPU", "error")
                self.stop(force=True)
                return
            self._write_stdin(str(choice))
            self._log(f"answered tool prompt with {choice} ({label})", "ok")
            return

        if self.settings.get("client_update"):
            self._write_stdin("y")
            self._log("accepted the help_crack.py self-update, restart the client to use it", "warn")
            return

        self._write_stdin("n")
        self._log("declined the help_crack.py self-update; enable 'allow client self-update' "
                  "in Options, or run help_crack.py once from a terminal", "warn")

    def _pick_tool(self):
        """choose the GPU cracking tool from help_crack's selection menu"""
        with self._lock:
            menu = dict(self._tool_menu)
            probe = dict(self._probe or {})
        if not menu:
            return 0, "default"

        gpu_only = bool(self.settings.get("gpu_only"))
        has_hashcat_gpu = bool(probe.get("gpus"))
        candidates = []
        for index in sorted(menu):
            entry = menu[index].lower()
            if "hashcat" in entry:
                if not gpu_only or has_hashcat_gpu:
                    candidates.append((index, menu[index]))
            elif gpu_only and ("-opencl" in entry or "-cuda" in entry):
                candidates.append((index, menu[index]))

        if gpu_only:
            return candidates[0] if candidates else (None, "")
        return min(menu), menu[min(menu)]

    # -------------------------------------------------------------------- watcher

    def start_watcher(self):
        """poll the artifact files forever (also useful for externally run clients)"""
        if self._watcher and self._watcher.is_alive():
            return
        self._watch_stop.clear()
        self._watcher = threading.Thread(target=self._watch_loop, daemon=True)
        self._watcher.start()

    def stop_watcher(self):
        self._watch_stop.set()

    def _watch_loop(self):
        interval = float(self.settings.get("poll_interval") or 1.0)
        while not self._watch_stop.is_set():
            try:
                self.store.refresh(hkey=self.current_hkey)
            except Exception:  # pylint: disable=broad-except
                self._log("artifact refresh failed", "error")
            self._watch_stop.wait(max(0.2, interval))

    # ------------------------------------------------------------------- accessors

    def status(self):
        """everything the UI needs to render the process state"""
        with self._lock:
            proc = self._proc
            running = proc is not None and proc.poll() is None
            now = time.time()
            return {
                "running": running,
                "pid": proc.pid if running else None,
                "started_at": self._started_at,
                "uptime": round(now - self._started_at, 1) if running and self._started_at else None,
                "exit_code": self._exit_code,
                "phase": self._phase,
                "phase_age": round(now - self._phase_since, 1),
                "progress": dict(self._progress),
                "last_progress_at": self._last_progress_at,
                "command": " ".join(self._command) if self._command else "",
                "workdir": os.path.abspath(self.settings.get("workdir")),
                "stop_requested": self._stop_requested,
                "gpu_only": bool(self.settings.get("gpu_only")),
                "probe": self._probe,
                "tool_menu": dict(self._tool_menu),
            }

    def logs(self, since=0):
        """log lines newer than the given id"""
        with self._lock:
            lines = [entry for entry in self._logs if entry["id"] > since]
            if since <= 0 and len(lines) > 1000:
                lines = lines[-1000:]
            return {"lines": lines, "next": self._next_id - 1}


