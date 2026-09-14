#!/usr/bin/env python3
"""Remove Emscripten's random temporary pre-JS filenames from generated glue."""
from pathlib import Path
import re
import sys

path = Path(sys.argv[1])
source = path.read_text()
normalized, count = re.subn(r'(?m)^\s*// (?:end )?include: /tmp/tmp[a-z0-9_]+\.js\s*$',
                            '', source)
if count < 2:
    raise RuntimeError('Emscripten pre-JS markers changed; inspect generated glue')
path.write_text(normalized)
