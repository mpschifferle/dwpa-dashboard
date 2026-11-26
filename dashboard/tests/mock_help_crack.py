#!/usr/bin/env python3
"""
Stand-in for help_crack.py, used by dashboard/tests/selftest.py.

It writes the same artifact files (help_crack.res / hash / key) and prints the
same kind of console output, including hashcat style "Key..... : value" status
blocks, so the dashboard can be validated without hashcat and without network.
"""

import json
import os
import sys
import time

BSSID_A = "1c7ee5e2f2d0"
STA_A = "0026c72e4900"
BSSID_B = "f81a67aa33ee"
STA_B = "9c5c8e112233"

HKEY = "42f3a9c1d2e3f4a5b6c7d8e9f0a1b2c3"

HASHES = [
    f"WPA*01*8ac36b891edca8eef49094b1afe061ac*{BSSID_A}*{STA_A}*646c696e6b***",
    f"WPA*02*269a61ef25e135a4b423832ec4ecc7f4*{BSSID_B}*{STA_B}*686f6d652d77696669*"
    "dbd249a3e9cec6ced3360fba3fae9ba4aa6ec6c76105796ff6b5a209d18782ca*"
    "0103007702010a00000000000000000000645b1f684a2566e21266f123abc386cc576f593e6dc5e3823a32fbd4af929f"
    "51000000000000000000000000000000000000000000000000000000000000*00",
]

KEYS = [
    f"WPA*01*8ac36b891edca8eef49094b1afe061ac*{BSSID_A}*{STA_A}*646c696e6b***:sunshine123",
    f"WPA*02*269a61ef25e135a4b423832ec4ecc7f4*{BSSID_B}*{STA_B}*686f6d652d77696669*"
    "dbd249a3e9cec6ced3360fba3fae9ba4aa6ec6c76105796ff6b5a209d18782ca*"
    "0103007702010a00000000000000000000645b1f684a2566e21266f123abc386cc576f593e6dc5e3823a32fbd4af929f"
    "51000000000000000000000000000000000000000000000000000000000000*00:00000000",
]


def emit(text):
    """print and flush, like the real client does"""
    sys.stdout.write(text + "\n")
    sys.stdout.flush()


def status(status="Running", progress=0, speed="0 H/s"):
    """mimic one hashcat status block (rewritten in place)"""
    lines = [
        f"Status...........: {status}",
        "Hash.Mode........: 22000 (WPA-PBKDF2-PMKID+EAPOL)",
        "Hash.Target......: help_crack.hash",
        f"Time.Estimated...: {time.strftime('%H:%M:%S')} (12 secs)",
        "Guess.Base.......: File (10-million-top10000.txt)",
        f"Speed.#1.........: {speed}",
        f"Recovered........: {1 if progress > 60 else 0}/2 (0.00%) Digests",
        f"Progress.........: {progress * 20}/2000 ({progress:.2f}%)",
        "Rejected.........: 0/0 (0.00%)",
        "Restore.Point....: 0/0 (0.00%)",
        "Candidates.#1....: 1234 -> sunshine1",
        "HWMon.GPU.#1....: 91% Util, 57c Temp, 61% Fan",
    ]
    for line in lines:
        sys.stdout.write(line + "\n")
    sys.stdout.flush()


def write(name, lines):
    with open(name, "w", encoding="utf-8") as fd:
        fd.write("\n".join(lines) + "\n")


def main():
    cmdline = " ".join(sys.argv[1:])
    emit("hashcat found" + (f" (args: {cmdline})" if cmdline else ""))

    # 0. tool selection, like help_crack.py does when several tools are present.
    #    The prompt has no trailing newline, exactly like input() would print it.
    emit("Choose the tool for cracking:")
    emit("0: hashcat")
    emit("1: john --format=wpapsk")
    emit("2: john --format=wpapsk-opencl")
    sys.stdout.write("Index:")
    sys.stdout.flush()
    answer = sys.stdin.readline().strip()
    choice = answer if answer.isdigit() else "0"
    emit(f"selected tool {choice}")

    # 1. the cracker challenge help_crack.py runs before doing real work
    emit("Challenge cracker for correct results")
    write("help_crack.hash", [HASHES[0]])
    write("help_crack.key", [f"{HASHES[0]}:aaaa1234"])
    time.sleep(0.3)
    os.unlink("help_crack.key")

    # 2. a work package arrives
    payload = {
        "hkey": HKEY,
        "hashes": HASHES,
        "dicts": [
            {"dpath": "/dicts/10-million-top10000.txt.gz", "dhash": "0" * 32},
            {"dpath": "/dicts/rockyou.txt.gz", "dhash": "1" * 32},
        ],
        "prdict": True,
    }
    with open("help_crack.res", "w", encoding="utf-8") as fd:
        json.dump(payload, fd)
    write("help_crack.hash", HASHES)

    emit("Downloading 10-million-top10000.txt.gz")
    time.sleep(0.2)
    emit("Extracting 10-million-top10000.txt.gz")
    time.sleep(0.2)
    emit("Downloading PR dynamic dictionary")

    # 3. cracking, with a live status block
    for step in range(4):
        status(progress=(step + 1) * 20, speed=f"{123 + step * 7}.4 kH/s")
        time.sleep(0.35)

    # 4. results come back through the pot file
    write("help_crack.key", KEYS)
    emit(f"Key for bssid {BSSID_A} is: sunshine123")
    emit(f"Key for bssid {BSSID_B} is: 00000000")
    time.sleep(0.4)

    status("Exhausted", progress=100, speed="150.2 kH/s")
    emit("Incrementing dictionary count to 2, last duration 24s")

    # 5. package finished, client goes to sleep
    for name in ("help_crack.res", "help_crack.hash", "help_crack.key"):
        if os.path.exists(name):
            os.unlink(name)
    emit("Sleeping...")
    time.sleep(0.5)
    emit("User exit requested")
    return 0


if __name__ == "__main__":
    sys.exit(main())
