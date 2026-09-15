#!/usr/bin/env python3
"""Check that browser provenance exposes firmware and PR identity."""
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile


ROOT = Path(__file__).resolve().parents[2]
SCRIPT = ROOT / "web/browser/write-manifest.py"
pointer_path = ROOT / "web/browser/current.json"
original_pointer = pointer_path.read_bytes() if pointer_path.exists() else None
build_root = ROOT / "web/builds"
build_root.mkdir(parents=True, exist_ok=True)
try:
    with tempfile.TemporaryDirectory(dir=build_root) as tmp:
        output = Path(tmp)
        for name in ("micropython.js", "micropython.wasm", "micropython.data"):
            (output / name).write_bytes(name.encode("ascii"))
        env = os.environ.copy()
        env["BROWSER_PR_NUMBER"] = "6"
        subprocess.run(
            [sys.executable, str(SCRIPT), str(ROOT), str(output), "Schnuartz/specter-diy"],
            check=True,
            env=env,
        )
        manifest = json.loads((output / "build-info.json").read_text())
        assert manifest["firmware_version"] == "v1.10.5", manifest
        assert manifest["pr_number"] == 6, manifest
finally:
    if original_pointer is None:
        pointer_path.unlink(missing_ok=True)
    else:
        pointer_path.write_bytes(original_pointer)

print("Browser manifest includes firmware version and PR number")
