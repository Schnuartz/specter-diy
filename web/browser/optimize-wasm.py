#!/usr/bin/env python3
"""Reduce native VM stack pressure after Emscripten's Asyncify transformation.

This reuses non-overlapping WASM local slots; it does not change firmware code,
imports, exports, the linear-memory stack, or the Asyncify stack allocation.
Run with the wasm-opt shipped by the build's pinned Emscripten SDK.
"""
from pathlib import Path
import os
import subprocess
import sys
import tempfile


def local_counts(data):
    pos = 8
    if data[:8] != b"\0asm\x01\0\0\0":
        raise ValueError("Not a WebAssembly 1 module")

    def uint():
        nonlocal pos
        value = shift = 0
        while True:
            byte = data[pos]
            pos += 1
            value |= (byte & 127) << shift
            if byte < 128:
                return value
            shift += 7

    while pos < len(data):
        section = data[pos]
        pos += 1
        length = uint()
        end = pos + length
        if section == 10:
            counts = []
            for _ in range(uint()):
                size = uint()
                function_end = pos + size
                count = 0
                for _ in range(uint()):
                    count += uint()
                    pos += 1  # Numeric local type in this wasm32 build.
                counts.append(count)
                pos = function_end
            return counts
        pos = end
    raise ValueError("Missing WASM code section")


def main():
    target = Path(sys.argv[1]).resolve()
    optimizer = sys.argv[2]
    before = local_counts(target.read_bytes())
    descriptor, temporary = tempfile.mkstemp(suffix=".wasm", dir=target.parent)
    os.close(descriptor)
    try:
        subprocess.run([optimizer, str(target), "--coalesce-locals", "--vacuum", "-o", temporary], check=True)
        after = local_counts(Path(temporary).read_bytes())
        if len(before) != len(after) or max(after) > 1024:
            raise RuntimeError(f"Unexpected native stack pressure after optimization: {max(after)} locals")
        os.replace(temporary, target)
        print(f"WASM locals: maximum {max(before)} -> {max(after)}; total {sum(before)} -> {sum(after)}")
    finally:
        Path(temporary).unlink(missing_ok=True)


if __name__ == "__main__":
    main()
