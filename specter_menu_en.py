#!/usr/bin/env python3
"""
Specter-DIY Test Menu
=====================

Interactive menu for testing the device over USB, in particular the
new "xpubauth" bulk authorization for multiple xpubs at once.

Requirements:  pip install hwi pyserial
Start:         python3 specter_menu_en.py
"""
import re
import sys
from hwidevice import enumerate as find_devices, SpecterClient

# remembers the scope string from the last successful "xpubauth begin",
# so "query all" can default to exactly what was just approved
_last_scope = {"value": None}


def _expand_scope(scope_str):
    """
    Client-side expansion of the same scope grammar the device uses
    (see docs/communication.md / src/apps/xpubs/scope.py): entries
    separated by ';', each an exact path or a path with one bounded
    {lo-hi} range component. Returns the concrete list of derivation
    paths so they can be queried one after another.
    """
    paths = []
    for raw in scope_str.split(";"):
        entry = raw.strip()
        if not entry:
            continue
        m = re.search(r"\{(\d+)-(\d+)\}", entry)
        if not m:
            paths.append(entry)
            continue
        lo, hi = int(m.group(1)), int(m.group(2))
        if lo > hi:
            raise ValueError("Invalid range (start > end) in %r" % entry)
        prefix, suffix = entry[:m.start()], entry[m.end():]
        for i in range(lo, hi + 1):
            paths.append("%s%d%s" % (prefix, i, suffix))
    return paths


def pick_device():
    print("Looking for device ...")
    devices = find_devices()
    if len(devices) == 0:
        print("No device found. Is it connected via USB and unlocked?")
        sys.exit(1)
    if len(devices) == 1:
        d = devices[0]
        print("Found: %s (fingerprint %s)" % (d["path"], d["fingerprint"]))
        return SpecterClient(d["path"])
    print("Multiple devices found:")
    for i, d in enumerate(devices):
        print("  [%d] %s (fingerprint %s)" % (i, d["path"], d["fingerprint"]))
    while True:
        choice = input("Which device? Enter number: ").strip()
        if choice.isdigit() and int(choice) < len(devices):
            return SpecterClient(devices[int(choice)]["path"])
        print("Invalid input.")


def ask(prompt, default=None):
    suffix = " [%s]" % default if default is not None else ""
    val = input("%s%s: " % (prompt, suffix)).strip()
    return val if val else default


def show_fingerprint(client):
    try:
        fp = client.get_master_fingerprint().hex()
        print("Fingerprint: %s" % fp)
    except Exception as e:
        print("Error: %s" % e)


def get_single_xpub(client):
    path = ask("Derivation path", "m/84h/0h/0h")
    print("-> Please confirm on the device ...")
    try:
        xpub = client.get_pubkey_at_path(path).to_string()
        print("Xpub: %s" % xpub)
    except Exception as e:
        print("Error/cancelled: %s" % e)


def begin_authorization(client):
    print("Scope examples (separate multiple entries with ';'):")
    print("  m/84h/0h/0h                      (exactly one path)")
    print("  m/84h/0h/{0-9}h                   (10 accounts at once)")
    print("  m/84h/0h/{0-9}h;m/86h/0h/{0-9}h    (multiple ranges)")
    scope = ask("Scope to authorize", "m/84h/0h/{0-9}h")
    print("-> Please confirm ONCE on the device ...")
    try:
        ok = client.authorize_xpubs(scope)
        if ok:
            print("Authorized!")
            _last_scope["value"] = scope
        else:
            print("Not confirmed.")
    except Exception as e:
        print("Error/cancelled: %s" % e)


def get_xpub_in_scope(client):
    path = ask("Derivation path (inside the authorized scope)", "m/84h/0h/0h")
    print("-> Should come back WITHOUT another confirmation on the device, if in scope ...")
    try:
        xpub = client.get_pubkey_at_path(path).to_string()
        print("Xpub: %s" % xpub)
    except Exception as e:
        print("Error/cancelled (outside the scope? then confirm on the device): %s" % e)


def query_all_in_scope(client):
    default = _last_scope["value"] or "m/84h/0h/{0-9}h"
    scope = ask("Scope to fully query (as used for the authorization)", default)
    try:
        paths = _expand_scope(scope)
    except ValueError as e:
        print("Error parsing the scope: %s" % e)
        return
    print("Querying %d xpub(s) one after another ..." % len(paths))
    ok_count = 0
    for path in paths:
        try:
            xpub = client.get_pubkey_at_path(path).to_string()
            print("  %-24s -> %s" % (path, xpub))
            ok_count += 1
        except Exception as e:
            print("  %-24s -> Error/cancelled: %s" % (path, e))
    print("Done: %d of %d succeeded." % (ok_count, len(paths)))


def end_authorization(client):
    try:
        ok = client.end_xpub_authorization()
        print("Authorization ended." if ok else "Error ending authorization.")
    except Exception as e:
        print("Error: %s" % e)


def raw_command(client):
    cmd = ask("Raw command (e.g. 'xpub m/84h/0h/0h')")
    if not cmd:
        return
    try:
        print(client.query(cmd))
    except Exception as e:
        print("Error/cancelled: %s" % e)


MENU = [
    ("Show fingerprint (no confirmation needed)", show_fingerprint),
    ("Query a single xpub (asks on the device)", get_single_xpub),
    ("Authorize multiple xpubs at once (xpubauth begin)", begin_authorization),
    ("Query an xpub inside the authorized scope (no extra confirmation)", get_xpub_in_scope),
    ("Query every xpub in the authorized scope, in order", query_all_in_scope),
    ("End authorization (xpubauth end)", end_authorization),
    ("Send a raw command (for anything else)", raw_command),
]


def main():
    client = pick_device()
    print()
    while True:
        print("=" * 50)
        for i, (label, _) in enumerate(MENU, start=1):
            print("  %d) %s" % (i, label))
        print("  0) Quit")
        choice = input("Choice: ").strip()
        print()
        if choice == "0":
            break
        if choice.isdigit() and 1 <= int(choice) <= len(MENU):
            MENU[int(choice) - 1][1](client)
        else:
            print("Invalid choice.")
        print()


if __name__ == "__main__":
    main()
