"""Check the exact MicroPython freeze inputs before the expensive WASM build."""

import importlib.util
import os
from pathlib import Path
import sys


source = Path(sys.argv[1]).resolve()
manifest = source / "browser.manifest.py"
tools = source / "f469-disco/micropython/tools/makemanifest.py"
spec = importlib.util.spec_from_file_location("makemanifest", tools)
module = importlib.util.module_from_spec(spec)
spec.loader.exec_module(module)
previous = Path.cwd()
try:
    os.chdir(source)
    module.include(str(manifest))
finally:
    os.chdir(previous)

files = [Path(base) / filename for _, base, filename, _ in module.manifest_list]
if not files or any(not path.is_file() for path in files):
    raise SystemExit("Browser freeze manifest has missing Python modules")
expected = [source / "src/main.py"]
embit = source / "f469-disco/libs/common/embit"
if (embit / "src/embit").is_dir():
    expected.append(embit / "src/embit/bip39.py")
    forbidden = ("/examples/", "/tests/", "/secp256k1/")
    if any(any(part in path.as_posix() for part in forbidden) for path in files):
        raise SystemExit("Browser freeze includes CPython-only embit files")
else:
    expected.append(embit / "bip39.py")
if any(path not in files for path in expected):
    raise SystemExit("Browser freeze omitted Specter or embit modules")
print(f"Browser freeze manifest: {len(files)} Python modules, embit import path valid")
