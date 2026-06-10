#!/usr/bin/env python3
# Copyright 2026 玑渡 GIDO Contributors
# SPDX-License-Identifier: Apache-2.0

"""为 gido 源码批量添加 SPDX 文件头（幂等，可重复执行）。"""

from __future__ import annotations

import sys
from pathlib import Path

PY_HEADER = "# Copyright 2026 玑渡 GIDO Contributors\n# SPDX-License-Identifier: Apache-2.0\n"
TS_HEADER = "/**\n * Copyright 2026 玑渡 GIDO Contributors\n * SPDX-License-Identifier: Apache-2.0\n */\n"
MARKER = "SPDX-License-Identifier: Apache-2.0"

SKIP_DIRS = {
    "node_modules",
    "dist",
    ".venv",
    "venv",
    "__pycache__",
    ".pytest_cache",
    ".git",
    "target",
}

def should_skip(path: Path) -> bool:
    return any(part in SKIP_DIRS for part in path.parts)

def add_py_header(text: str) -> str:
    if MARKER in text:
        return text
    return PY_HEADER + text

def add_ts_header(text: str) -> str:
    if MARKER in text:
        return text
    return TS_HEADER + text

def iter_files(root: Path) -> list[Path]:
    files: list[Path] = []
    for path in root.rglob("*"):
        if not path.is_file() or should_skip(path):
            continue
        if path.suffix in {".py", ".ts", ".tsx"}:
            files.append(path)
    return sorted(files)

def main() -> int:
    root = Path(__file__).resolve().parents[1]
    changed = 0
    for path in iter_files(root):
        text = path.read_text(encoding="utf-8")
        if path.suffix == ".py":
            updated = add_py_header(text)
        else:
            updated = add_ts_header(text)
        if updated != text:
            path.write_text(updated, encoding="utf-8")
            changed += 1
            print(f"updated: {path.relative_to(root.parent)}")
    print(f"done: {changed} file(s) updated")
    return 0

if __name__ == "__main__":
    sys.exit(main())
