"""Atomic file writes: a reader sees the old file or the whole new one, never half of it, and a failed write leaves
the old file in place with no temp file beside it."""
from __future__ import annotations

import json
import os
import tempfile
from pathlib import Path


def write_text_atomic(path, text: str) -> None:
    """Write text to a temp file in the same folder, flush it to disk, then replace path with it."""
    path = Path(path)
    fd, tmp = tempfile.mkstemp(prefix=f".{path.name}.", suffix=".tmp", dir=str(path.parent))
    try:
        with os.fdopen(fd, "w") as f:
            f.write(text)
            f.flush()
            os.fsync(f.fileno())
        os.replace(tmp, path)
    except BaseException:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise


def write_json_atomic(path, data, *, indent=None, newline: bool = False) -> None:
    """json.dumps(data) written with write_text_atomic; newline=True ends the file with a line break."""
    write_text_atomic(path, json.dumps(data, indent=indent) + ("\n" if newline else ""))
