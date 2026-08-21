#!/usr/bin/env python3
"""
Specter-DIY Testmenü
=====================

Interaktives Auswahlmenü zum Testen des Geräts über USB, insbesondere
der neuen "xpubauth"-Sammelfreigabe für mehrere Xpubs auf einmal.

Voraussetzungen:  pip install hwi pyserial
Start:            python3 specter_menu.py
"""
import sys
from hwidevice import enumerate as find_devices, SpecterClient


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
        print("Freigegeben!" if ok else "Nicht bestätigt.")
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
