> **!!! ATTENTION: THIS IS VIBECODED !!!**
> **I DONT KNOW ANYTHING ABOUT CODING BUT I THOUGHT A DASHBOARD WOULD BE COOL**

Review the code before you run it on anything you care about, and never commit
`dashboard/keys.jsonl` or `dashboard/settings.json` (they hold cracked passwords
and your site key — both are already in `.gitignore`).

# help_crack web dashboard

A local web UI for [`help_crack.py`](help_crack.py), the client side of the dwpa
distributed WPA cracker (wpa-sec.stanev.org).

`help_crack.py` stays exactly as it is — the dashboard runs it as a child process,
streams its console output, and reads the artifacts it writes
(`help_crack.res`, `help_crack.hash`, `help_crack.key`).

```
┌───────────────────────────── your machine ─────────────────────────────┐
│  browser ── HTTP ──► dashboard/app.py (Flask)                          │
│                            │ spawn / signal                              │
│                            ▼                                            │
│                     python help_crack.py  ──► hashcat / john            │
│                            │ writes                                     │
│                            ▼                                            │
│              help_crack.res / .hash / .key  (polled every second)       │
└────────────────────────────────────────────────────────────────────────┘
```

## What you get

* **GPU only by default** — the client is refused unless a GPU backend is
  visible (`hashcat -I` device types, or john's `wpapsk-opencl`/`wpapsk-cuda`);
  hashcat is then pinned with `-D 2` so it cannot fall back to a CPU device.
  See [GPU only mode](#gpu-only-mode).
* **Run control** — Start / Stop the client from the browser. Stop sends an
  interrupt to the whole process group, so hashcat or john exit too, and the
  resume file keeps the work package recoverable.
* **Cracker panel** — live hashcat/john status block: speed, progress bar,
  recovered hashes, ETA, GPU utilisation / temperature.
* **Live log** — ANSI-stripped console output with follow, level filter and
  colouring; a copy is appended to `dashboard/session.log`.
* **Work package** — hkey, hash count, dictionary list, whether a rule set or
  the dynamic PR dictionary comes with it.
* **Hashes in the current job** — PMKID / EAPOL type, SSID decoded from hex,
  BSSID and station address.
* **Cracked keys** — SSID, BSSID, password (click to copy), persisted in
  `dashboard/keys.jsonl`, searchable, exportable as CSV or JSON. Every row was
  cracked here, on your GPU, so it carries a **`your work`** badge. The site's
  per-package proof-of-work decoy (a fake PMKID, not a real network) is **not
  listed at all** — each work package contributes exactly one, so after a few
  hundred packages the table would be nothing but decoys; the panel footer just
  counts how many were solved. Once you use the **site** button, two more chips
  can appear: **`uploaded`** (the site credits this exact net and password to your
  key) and **`site knew`** (the site already had that net cracked with a different
  password — someone else got there first).

  **Country flags are best-effort, and usually missing.** A network's country
  lives only in the site's record, and it comes from whoever captured the
  handshake — `help_crack` hands out *other people's* captures, so the location
  was never yours to know. In practice most rows show `—`: either the site has no
  record of that BSSID any more (`?search=` only answers for nets still inside
  its active window) or it has the record with its `xx` "unknown" flag. Whatever
  the site does know is cached in `dashboard/geo.json`, and the panel footer
  tallies how many countries *your* keys are spread over.
* **Session stats** — keys cracked, cracking runs, last run duration,
  dictionary count, plus a 12-hour crack chart.
* **TV mode** — `/tv`: full-screen speed/ETA/GPU board, a spinning globe with real
  coastlines, the site's recent uploads *and* your own cracks, the wpa-sec
  statistics wall and the latest keys.

## Quick start

**Windows:** double-click `start-dashboard.bat`. It finds Python, installs Flask
on first run, starts the dashboard and opens the browser. Arguments are
forwarded to the dashboard:

```bat
start-dashboard.bat --port 9000
start-dashboard.bat --workdir D:\crack
```

Set `HC_DASHBOARD_NO_BROWSER=1` beforehand to keep the browser closed.

Any platform:

```bash
python -m pip install -r requirements.txt
python run_dashboard.py --open          # http://127.0.0.1:8666/
```

`python -m dashboard` and `python dashboard/app.py` work as well.

Options (all persisted in `dashboard/settings.json`, also editable in the UI):

| flag | meaning |
| --- | --- |
| `--host` | listen address, default `127.0.0.1` |
| `--port` | listen port, default `8666` |
| `--workdir` | directory the client should run in (default: repo root) |
| `--settings` | alternative settings file |
| `--open` | open the dashboard in the default browser |

The **Options** dialog in the UI maps to the `help_crack.py` command line:
working directory, interpreter, script path, `-co/--coptions`, `-pot/--potfile`,
`-ad/--additional`, plus the artifact poll interval, the GPU-only switch and the
"allow client self-update" switch.

A working directory that does not exist is **created** when you press Start (the
log says so), because Windows wipes `%TEMP%` between runs and a deleted folder
should not stop you. Keep it off `%TEMP%` anyway: the client's `.res`, `.hash` and
`.key` files live there, and the OS can delete them *while a run is in progress* —
the log warns you when the configured folder is inside the temp directory. Your
cracked keys are not affected either way, they are kept in `dashboard/keys.jsonl`.

## Requirements

* Python 3.8+ (tested on 3.11)
* Flask 3.x — the only extra dependency
* hashcat 6.2.6+ or John the Ripper, installed and on `PATH`, exactly as
  `help_crack.py` requires (validated here against hashcat 8.0.0 on an
  RTX 3060 through the OpenCL backend)

## Layout

| file | purpose |
| --- | --- |
| `run_dashboard.py` | entry point |
| `start-dashboard.bat` | Windows launcher (find Python, install Flask, start, open browser) |
| `dashboard/app.py` | Flask app, JSON API, argument parsing |
| `dashboard/runner.py` | subprocess supervisor, log ring buffer, status parsing, prompt handling |
| `dashboard/gpu.py` | tool detection and GPU-only enforcement (`hashcat -I` parsing) |
| `dashboard/site.py` | read-only client for the public site pages (country, stats, founds) |
| `dashboard/landdata.py` | Natural Earth land polygons → coastlines and the grid fallback |
| `dashboard/store.py` | parsers for the artifact files and key history |
| `dashboard/config.py` | persisted settings |
| `dashboard/templates/index.html` | single page UI |
| `dashboard/static/` | stylesheet and vanilla JS (no build step) |
| `dashboard/tests/mock_help_crack.py` | fake client for testing |
| `dashboard/tests/selftest.py` | end-to-end API test |
| `dashboard/tests/globe_drag.js` | head-less test of the globe drag / spin timeout |
| `dashboard/tests/key_badges.js` | head-less test of the cracked-key badges |
| `dashboard/tests/tv_gpu.js` | head-less test of the TV gpu panel |

## HTTP API

Every endpoint returns JSON. `POST` bodies are JSON objects.

| method | path | purpose |
| --- | --- | --- |
| `GET` | `/api/state?since=<log id>` | runner state, artifacts, keys, stats, new log lines |
| `POST` | `/api/start` | spawn the client (`{"extra": ["--coptions", "..."]}` optional) |
| `POST` | `/api/stop` | stop it (`{"force": true}` kills the process tree) |
| `POST` | `/api/settings` | update persisted settings |
| `GET` | `/api/keys/export?fmt=csv\|json` | download the key history |
| `POST` | `/api/keys/clear` | forget the dashboard's key history |
| `POST` | `/api/probe` | re-run tool / GPU detection |
| `POST` | `/api/input` | write a line to the running client's stdin |
| `POST` | `/api/site/geo` | country + known/cracked flag per BSSID (public search, cached) |
| `POST` | `/api/site/account` | your founds + a verdict per locally cracked key |
| `GET` | `/api/site/world` | saved country tally for the globe (instant; `?refresh=1` samples now) |
| `GET` | `/api/map/coast` | coastline polygons for the globe (smooth; built once from Natural Earth) |
| `GET` | `/api/map/land` | 1-degree land grid, the globe's fallback (built once from Natural Earth) |
| `GET` | `/api/site/stats` | the public statistics page as label/value pairs |
| `GET` | `/api/health` | liveness probe |

The UI polls `/api/state` once a second, passing the last log id it saw so only
new output is transferred.

## Tests

```bash
python dashboard/tests/selftest.py
```

The test boots the real Flask app against `dashboard/tests/mock_help_crack.py`
in a temporary directory — no hashcat and no network — then checks the live
status, package/hash/key parsing, exports, settings, log pagination, prompt
handling and the stop path, plus the GPU gate: device-type filtering in the
spawned command, refusal of a CPU-only host, and `hashcat -I` parsing. It also
restarts the site client and store over the same files to prove the globe's tally,
the country cache and your keys come back from disk, and hammers the threaded
sampler from several threads so a lock mistake cannot slip in.

The globe's interaction is tested too: `dashboard/tests/globe_drag.js` loads
`static/tv.js` against a stub canvas in node, drags it, and asserts the view
moves, the spin parks, the countdown runs down and the turn resumes after 30 s.
`dashboard/tests/key_badges.js` renders the real `static/app.js` against a stub
DOM — two real keys plus one PoW decoy — and asserts that `your work` lands on the
real rows (the `POW` badge survives as a safety net if a decoy ever reached the
table), then clicks **site** to check the `uploaded` / `site knew` chips.
`dashboard/tests/tv_gpu.js` feeds the real `static/tv.js` hashcat's single
long hardware line and checks it becomes six separate cells, and that two cards get
two captioned blocks. The self test runs all three when `node` is on PATH.

## Proof-of-work hashes (why the site makes you crack fake networks)

`get_work.php` appends one synthetic hash to every work package:

```php
$pow_hash = sprintf('WPA*01*%s*%s*%s*%s***01', substr($pow_pmkid,0,32),
                    bin2hex($pow_mac_ap), bin2hex($pow_mac_sta), $pow_arr[5]);
```

Random AP/station MACs, the real SSID, and a `01` marker in the pmkid-info
field. The client must crack it, otherwise `put_work` drops the `hkey` and the
results are not accepted. So **one fake "network" is cracked per package, by
design** — an anti-abuse check, not a capture, and its "password" is just a
preimage of a fake PMKID.

The dashboard recognises the marker and keeps those rows out of the key list, the
exports and the chart, while the footer still reports how many were solved:

```
17 keys · 150 site challenges (POW) solved
```

That happened here for real: with one decoy per package, 150 finished packages
had produced 150 fake rows against 17 genuine networks, so the table had become
90 % noise. They stay in `keys.jsonl` (a few kB) and `counts.pow` still counts
them; they are simply never presented as keys.

## Publishing this repository

The directory the dashboard runs in also collects artefacts that must **never**
be published: `help_crack.*` (other people's handshakes and results),
`cracked.txt.gz` / `wpskey*.txt.gz` (downloaded dictionaries), and the
dashboard's own `keys.jsonl` / `session.log` / `settings.json` (cracked
passwords, client output, optional site key). The root `.gitignore` excludes all
of them, plus the client and the bundled cracker, so a fresh
`git init && git add -A` only tracks the dashboard itself:

* `help_crack.py` is GPL-3.0-or-later (Alex Stanev) — have users download it
  from wpa-sec instead of vendoring it; the 13-line parser fix documented above
  can be re-applied locally.
* hashcat is MIT (John the Ripper GPL-2.0) but tens of megabytes of binaries —
  let users install it. The dashboard finds it on `PATH`, next to
  `help_crack.py`, or in a `hashcat-*/` folder.
* everything in this repository is MIT, see `LICENSE`.

Attribution of the quoted client code, the "unofficial" note and the rules on
using recovered credentials are in the [root README](../README.md#legal--attribution).

## Who provided the password? (`site` button)

The site does not record which volunteer cracked a net — `nets` has `pass` and
`n_state`, `n2d.hkey` is the work token (nulled on return), and there is no
cracker column. What it does record is **who submitted the capture** (`n2u`), and
`search.php` hides the password behind the literal word `Found` for everyone
else.

So attribution can only be reconstructed by cross-referencing:

```
setx WPASEC_KEY <your 32-hex key>      :: then restart the dashboard
```

Pressing **site** in the Cracked keys card does exactly that:

| badge | meaning |
| --- | --- |
| `your work` | cracked here, on your GPU — on every real row |
| `uploaded` | the BSSID is in your `?api&dl=1` founds **and** the password matches what you cracked locally |
| `site knew` | the site has that net cracked, but with a different password — someone else got there first |
| *(nothing extra)* | the site does not know this BSSID |

The site's proof-of-work decoys never appear here at all (see above), so this
table only ever talks about real networks.

The footer then reads `site: N founds, M yours`. Everything is read-only: one
`?api&dl=1` download plus the cached `?search=` lookups, no client protocol calls.

The key is read from `WPASEC_KEY` (preferred) or the `site_key` setting; it is
never written to the log, and `dashboard/settings.json` is git-ignored.

## TV mode & the globe

`/tv` (the **TV** button, or `http://127.0.0.1:8666/tv`) is a full-screen board for
a second monitor: giant speed/progress/ETA/keys/PoW/GPU numbers, a spinning globe,
the site's statistics wall, the latest keys with flags, and a one-line log ticker.
No controls — leave it on a screen.

**On a narrow screen** — a phone, or a small browser window — the three-column wall
collapses into one scrollable column and the globe keeps a real height instead of
being squeezed to nothing; a phone held sideways drops the statistics wall and
keeps the numbers plus the globe.

**GPU numbers get a cell each** — temp, fan, util, core, mem, bus, power — because
hashcat prints them as one long line (`Temp: 70c Fan: 54% Util: 96% Core:1920MHz…`)
that used to be cut off with an ellipsis in a narrow tile. With several cards in the
machine you get one captioned block per card (`gpu 1`, `gpu 2`).

**Drag the globe** (mouse, trackpad or finger) to look anywhere: horizontal drag
turns it, vertical drag tilts the pole toward you (clamped to ±80°). Let go and it
holds that view, showing `spins again in 29s` in the corner; after **30 seconds**
of no dragging it eases the tilt back to level and resumes its turn from wherever
you parked it. That timeout means a passer-by can read the map without fighting
the animation, and the wall heals itself afterwards.

The globe is **country-level** because that is all the site exposes: there is no
endpoint with coordinates. `?nets` ("Last 20 submitted networks", hardcoded
`LIMIT 20`, no pagination) does carry each net's country flag, so the dashboard
*samples that firehose once a minute* and accumulates the tally in
`dashboard/world.json` — after a few hours the globe shows a real distribution of
recent uploads. That tally is **everyone's** uploads, not yours.

That page also carries the **BSSID next to each flag**, so every network you own
(any key in `keys.jsonl`, any hash in the current package) is *watched*: when it
appears there, the flag is cached in `geo.json` and the key gets its country. This
matters because the site's `?search=` lookup forgets a network once it is pruned —
for older cracks there is simply no country left to find, which is why a key can
keep a `—` forever while a brand-new one lights up within a minute.

**Expect most of your own keys to have no country**, and the rings to be sparse
because of it. Measured on this installation: 0 of 17 cracked keys had one — 13
were not in the site's search index at all and the other 4 were there with the
site's own `xx` "unknown" flag, while the same `?nets` page was 15/20 geolocated
for other people's uploads. A location only exists if the person who *captured*
the handshake had it, and you are cracking someone else's capture.

**Your own cracks are the green rings**: each cracked key's BSSID is looked up on
the public search page (the same `dashboard/geo.json` cache the key table uses),
so the rings, the green chips at the bottom and the caption's `yours: N in M`
(nets / countries) cover your keys only — and stay empty while the site has no
country for them, which is the normal case explained just above. Blue markers and
grey chips are the site-wide sample. The TV page warms the cache itself, so it
works without opening the main page first.

**The coastlines are real vector polygons**, not a hand-drawn mask: on first use
the dashboard fetches Natural Earth's `ne_110m_land` polygons (public domain — no
attribution required, but thanks anyway), converts them to 128 rings / ~5 k points
(hundredths of a degree, ≈ 54 kB of JSON) in about a tenth of a second and caches
them in `dashboard/coast.json`. The page fills and outlines those rings, cutting
each one at the horizon so the edge follows the limb. If that file is missing and
downloads are off it falls back to a 1-degree raster (`dashboard/land.json`, built
the same way) and, failing that, to a built-in 5-degree mask — the globe always
shows *something*. Delete `coast.json` to rebuild it.

What `?stats` gives, and the TV wall shows:

| last 24 h | all time |
| --- | --- |
| processed nets, performance, submissions, founds, contributors, keyspace progress | total nets, cracked nets, success rate, PMKID success rate, geolocated share, round ETA |

## What survives a restart

Close the dashboard and everything the globe shows is still there: the tally, your
keys, their countries. Nothing worth keeping lives only in memory, and all of it
sits next to the code in `dashboard/`, written by your own runs:

| file | holds | why it is git-ignored |
| --- | --- | --- |
| `keys.jsonl` | every key you cracked: ssid, bssid, password, timestamp | it is other people's Wi-Fi and passwords |
| `geo.json` | bssid → country (where the site knows one), plus known/cracked flags | whatever the site revealed about your networks |
| `world.json` | the saved site tally: counts, samples, first/last sample time | your sampling history |
| `land.json` | the 1-degree coastline grid (globe fallback) | runtime cache of public-domain data |
| `coast.json` | the coastline polygons the globe actually draws | runtime cache of public-domain data |
| `settings.json` | your options, possibly a site key | may hold a key |

Every writer is atomic (`write .tmp`, then rename), so a hard kill or a power cut
cannot leave half a file behind. A file that is corrupt, truncated or from some
older layout is read as empty instead of breaking the dashboard, and the next
sample rewrites it.

The tally is answered **from disk** on every request: a dashboard that sat closed
for a week paints last week's map instantly and refreshes it in the background
(once a minute; a failed sample is retried after two minutes and reported in the
API's `error` field). Once the saved sample is older than 15 minutes the TV
caption admits it — `· saved 4h ago` — rather than pretending it is live.
`GET /api/site/world?refresh=1` forces a sample and waits for it.

## Security

* Binds to `127.0.0.1` by default and has **no authentication**: anyone who can
  reach the port can start and stop the client. Only change `--host` on a
  trusted network and prefer an SSH tunnel or a reverse proxy with auth.
* The dashboard never asks for and never stores your **wpa-sec upload key** —
  the client does not use one (work and results are addressed by `hkey`). That
  key only belongs in the website's upload form; keep it out of screenshots,
  chat and logs, and regenerate it if it leaks.
* The dashboard never edits `help_crack.py` and never uses the client protocol.
  Two **read-only** lookups on the public site are enabled by default and can be
  switched off with *Site lookups* in Options: the country of a BSSID
  (`?search=`, cached) and the statistics page (`?stats`). Neither sends your
  key, your work token or any cracked password. The cracking path itself stays
  entirely inside `help_crack.py`.
* Cracked passwords live unencrypted in `dashboard/keys.jsonl` — same exposure
  as any pot file.

## How state is collected

`help_crack.py` deletes `help_crack.key` right after uploading the results, so
the watcher polls the file every second (configurable) and tails new lines while
it exists. Keys are de-duplicated by `bssid:password` and appended to
`dashboard/keys.jsonl`, which is what the table and the exports show.

The known challenge handshake (`1c7ee5e2f2d0` / `aaaa1234`) is filtered out, just
like the client does when writing a user pot file.

## GPU only mode

Enabled by default (`gpu_only` in the Options dialog). On every Start the
dashboard probes the machine and refuses to run the client when no GPU backend
is available:

* `hashcat -I` is parsed — devices reporting `Type: GPU` count as usable, a
  CPU-only device list blocks the start and the message names the CPU device.
* If hashcat is missing, john's `wpapsk-opencl` / `wpapsk-cuda` formats are
  self-tested; without them the start is blocked as well.

With a GPU present, `-D 2` (GPU device types only) is appended to the cracker
options handed to `help_crack.py` through `-co`, unless you already set `-D` or
`--backend-devices-types` yourself. `help_crack.py` uses those options for both
the challenge run and the real cracking runs, so the CPU is never selected.

Independently of GPU only mode, the dashboard also adds `--status
--status-timer=1` for hashcat. hashcat only prints its periodic status block
when asked, and without it the Cracker panel stays empty for the whole run
(verified: 0 blocks without the flag, a parseable block per timer tick with it).
Both of these are local cracker flags and never reach the server.

The probe also looks inside versioned tool folders (`hashcat-*/`, `john-*/`)
next to the dashboard or in the working directory, and the directories of the
tools it finds are prepended to the client's `PATH`. A hashcat unpacked next to
`help_crack.py` therefore works even though the client itself only searches
`PATH`.

`help_crack.py` asks which tool to use when it finds several
(`0: hashcat`, `1: john --format=wpapsk`, …). The dashboard answers that prompt
itself: in GPU only mode it picks hashcat, or an `-opencl`/`-cuda` john format,
and stops the client instead of accepting a CPU format. The self-update prompt
is declined with a log note (tick *allow client self-update* in Options to let
the client replace itself). Any other prompt can be answered from the
`stdin ›` box next to the log.

Turn the mode off in Options if you really want CPU cracking — the Devices card
still shows what was detected, and `Detect GPU` re-runs the probe without
restarting the dashboard.

### Troubleshooting: `hashcat or john not found`

`help_crack.py` builds its cracker command as a string and runs
`shlex.split()` on it; `shlex` treats `\` as an escape character, so a plain
Windows path such as `C:\tools\hashcat-8.0.0\hashcat.exe` is mangled into
`C:toolshashcat-8.0.0hashcat.exe` and the client reports *"hashcat or john not
found"* even though the binary is there. The dashboard therefore puts the tool
directories on the client's `PATH` in a form that survives the round trip
(`C:/tools/hashcat-8.0.0/`), which is what makes a hashcat dropped next to
`help_crack.py` work. If the line still shows up, use **Options → Detect GPU**
to see which binary was found, and check that it runs (`hashcat.exe -V`).

## Does it still work with wpa-sec.stanev.org?

Yes. `help_crack.py` is shipped **unmodified** (32 707 bytes, MD5
`DAD2E3AC5610236D0F5D64339E4BE31A`, v2.2.0) and still speaks to the server itself:
the same `?get_work`, `?put_work` and `?prdict` requests, the same challenge
handshake, the same file formats. The dashboard adds no protocol code — it has
no HTTP client at all — and only:

* spawns the unmodified script with its documented options (`-co`, `-pot`, `-ad`),
* runs it in a working directory it never writes to,
* reads the artifact files the client writes and the log lines it prints.

Nothing in `dashboard/` opens, patches or rewrites `help_crack.py`; its runtime
files (`settings.json`, `keys.jsonl`, `session.log`) live in `dashboard/`.

Two behavioural notes, both caused by running the client non-interactively:

1. When the server announces a newer client version, `help_crack.py` asks
   whether to update. The dashboard answers "no" and logs a warning — run
   `python help_crack.py` once from a terminal to update it.
2. GPU only mode adds `-D 2` to the *cracker* options (`-co`). That only affects
   the local hashcat invocation, never anything sent to the server.

### About the cracked-key BSSID

`help_crack.py` 2.2.0 parses hashcat `-m 22000` result lines with the layout of
the old hccap format, so for hashcat it uploads the hash-mode field as the
network id:

```
{"hkey": "...", "type": "bssid", "cand": [{"k": "01", "v": "6161616131323334"}]}
```

Both the endpoint and the payload shape are correct — only `k` is wrong, which
means the site cannot file the key under a network (the john path, which sends a
real 12-hex BSSID, is unaffected). Two things follow:

* The dashboard parses the pot file itself, so the keys table and the CSV/JSON
  exports always show the real BSSID and password, and it logs a warning when
  the client reports `Key for bssid 01 …`.
* Uploads stay misattributed until the client is fixed. **That guard is applied
  in this folder** — backup at `help_crack.py.orig`, revert with
  `copy help_crack.py.orig help_crack.py`. It is 13 added lines at the top of
  `parse_hashcat_output()`:

```python
            try:
                # hashcat -m 22000 outfile: "WPA*02*mic*bssid*sta*ssid*...:<password>"
                if pot.startswith("WPA*"):
                    head, sep, plain = pot.partition(":")
                    if sep and len(head.split("*")) > 3:
                        return {"k": head.split("*")[3].lower(),
                                "v": bytes(plain.rstrip("\r\n"), encoding="utf-8",
                                           errors="ignore").hex()}
                arr = pot.split(":", 4)
                # legacy hccap outfile: "essid:ap:sta:mic:<password>"
                if (len(arr) == 5
                        and re.fullmatch(r"[0-9a-fA-F]{12}", arr[1] or "")
                        and re.fullmatch(r"[0-9a-fA-F]{12}", arr[2] or "")):
                    return {"k": arr[1][:12].lower(), …}
```

  The second guard matters because a john-wpapsk line also contains colons: it
  used to be swallowed by the legacy-hccap branch, so jtr results were mangled
  too. Now they fall through to the client's own `parse_jtr()`.

  Verified with a real hashcat 8.0.0 outfile (from cracking the client's own
  challenge hash): hashcat → correct BSSID, legacy hccap → unchanged, john → no
  longer misparsed (john is not installed here, so its potfile layout is
  unverified).

  The client's self-update replaces `help_crack.py` wholesale: if you enable
  *allow client self-update* and the site ships a new version, re-apply the
  guard (diff above) or keep updates manual.

## Caveat found while building this

`help_crack.py`'s `get_key()` → `parse_hashcat_output()` splits a potfile line
into five colon-separated fields, which is the *legacy hccap* layout
(`essid:ap:sta:mic:password`). With `-m 22000` hashcat writes `hash:password`,
so the hashcat branch fails and `parse_jtr()` picks it up instead; that branch
returns `WPA*..` field 1 (`01`/`02`) as the BSSID. If your uploads look wrong in
a real run, that is the place to look. The dashboard parses both layouts
correctly on its own (`store.parse_potline`) and does not share that code path.

