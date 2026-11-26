> **!!! ATTENTION: THIS IS VIBECODED !!!**
> **I DONT KNOW ANYTHING ABOUT CODING BUT I THOUGHT A DASHBOARD WOULD BE COOL**

A small local web UI for [`help_crack.py`](https://wpa-sec.stanev.org), the
client of the dwpa distributed WPA cracker. It runs the client as a child
process, streams its output, and shows the current work package, hashcat's live
speed/ETA, the cracked keys with country flags, and which of those keys actually
map to your wpa-sec account.

* full documentation, options and caveats: **[dashboard/README.md](dashboard/README.md)**
* Windows: double-click `start-dashboard.bat` (installs Flask, opens the browser)
* anywhere: `pip install -r requirements.txt` then `python run_dashboard.py --open`

This repository ships **no** cracking binaries and **no** client — install
hashcat (or John the Ripper) and download `help_crack.py` from wpa-sec yourself,
as its documentation says. Runtime files such as `dashboard/keys.jsonl`
(cracked passwords of other people's networks) and `dashboard/settings.json`
(your site key) are git-ignored; keep them that way.

MIT licensed, see [LICENSE](LICENSE).

## Legal / attribution

* **Unofficial.** This is a personal UI around the *dwpa* client; it is not
  affiliated with, endorsed by, or supported by wpa-sec.stanev.org, the dwpa
  project, or Alex Stanev.
* **Third-party code.** `help_crack.py` (© Alex Stanev) is **not** included; it
  is GPL-3.0-or-later and you download it from the site yourself. The few code
  lines quoted in `dashboard/README.md` (the proof-of-work hash, the stats query,
  the pot-file parser) are his work as well and remain under that licence — the
  patch described there applies to GPL code and the patched file stays GPL.
  hashcat is MIT; John the Ripper is GPL-2.0.
* **What the network calls are.** The dashboard only reads public pages
  (`?search=`, `?stats`) and, if you configure a key, your own `?api&dl=1`
  founds. At most one request per BSSID, cached for 30 days, no client-protocol
  calls, nothing written back to the site.
* **Use of recovered material.** Cracked PSKs in `dashboard/keys.jsonl` belong to
  real third-party networks. This tool exists for the statistics and
  security-research purpose wpa-sec serves: **do not use a recovered password to
  access a network you are not authorised to test.** Distributing or running
  security tooling is restricted in some jurisdictions — check your local law.
* No warranty of any kind; see the MIT text in [LICENSE](LICENSE).

