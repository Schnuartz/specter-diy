#!/usr/bin/env python3
"""Write source and artifact provenance for one addressable browser build."""
from datetime import datetime, timezone
from hashlib import sha256
from pathlib import Path
import json
import re
import subprocess
import sys

source, output = (Path(p).resolve() for p in sys.argv[1:3])
repository = sys.argv[3]


def git(*args):
    return subprocess.check_output(["git", "-C", str(source), *args], text=True).strip()


def specter_firmware_version():
    """Decode the firmware version using the layout from src/platform.py."""
    boot = source / "boot" / "main" / "boot.py"
    match = re.search(r"<version:tag10>(\d{10})</version:tag10>", boot.read_text())
    if not match:
        raise RuntimeError(f"Missing Specter firmware version tag in {boot}")
    encoded = match.group(1)
    version = f"{int(encoded[:2])}.{int(encoded[2:5])}.{int(encoded[5:8])}"
    release_candidate = int(encoded[8:])
    if release_candidate != 99:
        version += f"-rc{release_candidate}"
    return version


artifacts = {}
for name in ("micropython.js", "micropython.wasm", "micropython.data"):
    path = output / name
    if not path.is_file() or path.stat().st_size == 0:
        raise RuntimeError(f"Missing browser artifact: {path}")
    artifacts[name] = {"bytes": path.stat().st_size, "sha256": sha256(path.read_bytes()).hexdigest()}

artifact_set = sha256(''.join(artifacts[name]['sha256'] for name in sorted(artifacts)).encode()).hexdigest()

manifest = {
    "repository": repository,
    "source_url": "https://github.com/" + repository,
    "commit": git("rev-parse", "HEAD"),
    "branch": git("branch", "--show-current") or None,
    "build_type": "Browser / WebAssembly",
    "capabilities": {"smartcard": True, "smartcard_type": "MemoryCard"},
    "experimental": True,
    "toolchain": "Emscripten 3.1.74",
    "artifact_set_sha256": artifact_set,
    "built_at": datetime.now(timezone.utc).isoformat(),
    "artifacts": artifacts,
}
if repository.lower() in ("cryptoadvance/specter-diy", "schnuartz/specter-diy", "schnuartz-ai/specter-diy"):
    manifest["firmware_version"] = specter_firmware_version()
if len(sys.argv) > 4 and sys.argv[4] == "mockui":
    manifest["application"] = "MockUI"
    manifest["entrypoint"] = "mockui"
    manifest["capabilities"]["smartcard_type"] = "MockUI virtual card"
elif len(sys.argv) > 4:
    manifest["platform_repository"] = repository
    manifest["platform_commit"] = sys.argv[4]
(output / "build-info.json").write_text(json.dumps(manifest, indent=2) + "\n")
pointer = {
    "build": str(output.relative_to(Path(__file__).resolve().parent.parent)).replace('\\', '/') + "/",
    "version": artifact_set[:16],
}
pointer_name = "current.json"
pointer_path = Path(__file__).resolve().parent / pointer_name
pointer_path.parent.mkdir(parents=True, exist_ok=True)
pointer_path.write_text(json.dumps(pointer, indent=2) + "\n")
