#!/usr/bin/env python3
"""
Specter-DIY Testmenü
=====================

Interaktives Auswahlmenü zum Testen des Geräts über USB, insbesondere
der neuen "xpubauth"-Sammelfreigabe für mehrere Xpubs auf einmal.

Voraussetzungen:  pip install hwi pyserial
Start:            python3 specter_menu.py
"""
import re
import sys
from hwidevice import enumerate as find_devices, SpecterClient

# remembers the scope string from the last successful "xpubauth begin",
# so "alle abfragen" can default to exactly what was just approved
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
            raise ValueError("Ungültiger Bereich (Start > Ende) in %r" % entry)
        prefix, suffix = entry[:m.start()], entry[m.end():]
        for i in range(lo, hi + 1):
            paths.append("%s%d%s" % (prefix, i, suffix))
    return paths


def pick_device():
    print("Suche Gerät ...")
    devices = find_devices()
    if len(devices) == 0:
        print("Kein Gerät gefunden. Ist es per USB verbunden und entsperrt?")
        sys.exit(1)
    if len(devices) == 1:
        d = devices[0]
        print("Gefunden: %s (Fingerprint %s)" % (d["path"], d["fingerprint"]))
        return SpecterClient(d["path"])
    print("Mehrere Geräte gefunden:")
    for i, d in enumerate(devices):
        print("  [%d] %s (Fingerprint %s)" % (i, d["path"], d["fingerprint"]))
    while True:
        choice = input("Welches Gerät? Nummer eingeben: ").strip()
        if choice.isdigit() and int(choice) < len(devices):
            return SpecterClient(devices[int(choice)]["path"])
        print("Ungültige Eingabe.")


def ask(prompt, default=None):
    suffix = " [%s]" % default if default is not None else ""
    val = input("%s%s: " % (prompt, suffix)).strip()
    return val if val else default


def show_fingerprint(client):
    try:
        fp = client.get_master_fingerprint().hex()
        print("Fingerprint: %s" % fp)
    except Exception as e:
        print("Fehler: %s" % e)


def get_single_xpub(client):
    path = ask("Derivationspfad", "m/84h/0h/0h")
    print("-> Bitte am Gerät bestätigen ...")
    try:
        xpub = client.get_pubkey_at_path(path).to_string()
        print("Xpub: %s" % xpub)
    except Exception as e:
        print("Fehler/Abgebrochen: %s" % e)


def begin_authorization(client):
    print("Beispiele für den Bereich (mehrere Einträge mit ';' trennen):")
    print("  m/84h/0h/0h                      (genau ein Pfad)")
    print("  m/84h/0h/{0-9}h                   (10 Konten auf einmal)")
    print("  m/84h/0h/{0-9}h;m/86h/0h/{0-9}h    (mehrere Bereiche)")
    scope = ask("Freizugebender Bereich", "m/84h/0h/{0-9}h")
    print("-> Bitte am Gerät EINMAL bestätigen ...")
    try:
        ok = client.authorize_xpubs(scope)
        if ok:
            print("Freigegeben!")
            _last_scope["value"] = scope
        else:
            print("Nicht bestätigt.")
    except Exception as e:
        print("Fehler/Abgebrochen: %s" % e)


def get_xpub_in_scope(client):
    path = ask("Derivationspfad (innerhalb des freigegebenen Bereichs)", "m/84h/0h/0h")
    print("-> Sollte OHNE erneute Bestätigung am Gerät zurückkommen, wenn im Bereich ...")
    try:
        xpub = client.get_pubkey_at_path(path).to_string()
        print("Xpub: %s" % xpub)
    except Exception as e:
        print("Fehler/Abgebrochen (außerhalb des Bereichs? dann am Gerät bestätigen): %s" % e)


def query_all_in_scope(client):
    default = _last_scope["value"] or "m/84h/0h/{0-9}h"
    scope = ask("Bereich, der komplett abgefragt werden soll (wie bei der Freigabe)", default)
    try:
        paths = _expand_scope(scope)
    except ValueError as e:
        print("Fehler beim Auswerten des Bereichs: %s" % e)
        return
    print("Frage %d Xpub(s) nacheinander ab ..." % len(paths))
    ok_count = 0
    for path in paths:
        try:
            xpub = client.get_pubkey_at_path(path).to_string()
            print("  %-24s -> %s" % (path, xpub))
            ok_count += 1
        except Exception as e:
            print("  %-24s -> Fehler/Abgebrochen: %s" % (path, e))
    print("Fertig: %d von %d erfolgreich." % (ok_count, len(paths)))


def end_authorization(client):
    try:
        ok = client.end_xpub_authorization()
        print("Freigabe beendet." if ok else "Fehler beim Beenden.")
    except Exception as e:
        print("Fehler: %s" % e)


def raw_command(client):
    cmd = ask("Rohbefehl (z.B. 'xpub m/84h/0h/0h')")
    if not cmd:
        return
    try:
        print(client.query(cmd))
    except Exception as e:
        print("Fehler/Abgebrochen: %s" % e)


MENU = [
    ("Fingerprint anzeigen (keine Bestätigung nötig)", show_fingerprint),
    ("Einzelne Xpub abfragen (fragt am Gerät nach)", get_single_xpub),
    ("Mehrere Xpubs auf einmal freigeben (xpubauth begin)", begin_authorization),
    ("Xpub im freigegebenen Bereich abfragen (ohne erneute Bestätigung)", get_xpub_in_scope),
    ("Alle freigegebenen Xpubs nacheinander abfragen", query_all_in_scope),
    ("Freigabe beenden (xpubauth end)", end_authorization),
    ("Rohbefehl senden (für alles andere)", raw_command),
]


def main():
    client = pick_device()
    print()
    while True:
        print("=" * 50)
        for i, (label, _) in enumerate(MENU, start=1):
            print("  %d) %s" % (i, label))
        print("  0) Beenden")
        choice = input("Auswahl: ").strip()
        print()
        if choice == "0":
            break
        if choice.isdigit() and 1 <= int(choice) <= len(MENU):
            MENU[int(choice) - 1][1](client)
        else:
            print("Ungültige Auswahl.")
        print()


if __name__ == "__main__":
    main()
