"""Browser entry point. Specter itself is frozen unchanged into the wasm VM."""
print("SPECTER_BROWSER_BOOT")
import sys
import os
sys.path.insert(0, "/browser")
sys.path.append("")  # MicroPython's frozen modules are resolved through this entry.
try:
    sys.setrecursionlimit(256)
except AttributeError:
    pass
for _path in ("/state", "/bridge"):
    try:
        os.mkdir(_path)
    except OSError:
        pass
# The current firmware has deep import chains. Its Unix simulator uses this
# same leaf-first import ordering to stay within MicroPython's import limit.
import hashlib
import microur.util.xoshiro256
import microur.util.random_sampler
import microur.util.fountain
import microur.util.ur
import microur.util.bytewords
import microur.encoder
import microur.decoder
print("SPECTER_IMPORTS_DONE")
import browser_sd
browser_sd.install()
import main
print("SPECTER_MAIN_IMPORTED")
main.main()
