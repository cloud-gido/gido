# Copyright 2026 玑渡 GIDO Contributors
# SPDX-License-Identifier: Apache-2.0
"""Execute a frozen Python source with the published runtime macro semantics."""
from __future__ import annotations

import sys
from pathlib import Path
from typing import Any, Dict

from gido_job.context import load_job_context
from gido_job.macros import substitute_sql_macros


def run_main(path: str) -> None:
    source_path = Path(path)
    context = load_job_context() or {}
    macro_variables = context.get("macro_variables")
    source = substitute_sql_macros(
        source_path.read_text(encoding="utf-8"),
        bizdate=context.get("bizdate"),
        tz_name=str(context.get("timezone") or "Asia/Shanghai"),
        variables=macro_variables if isinstance(macro_variables, dict) else None,
    )
    namespace: Dict[str, Any] = {
        "__name__": "__main__",
        "__file__": str(source_path),
        "__package__": None,
        "__cached__": None,
    }
    exec(compile(source, str(source_path), "exec"), namespace, namespace)


def main() -> int:
    if len(sys.argv) != 2:
        raise SystemExit("usage: python -m gido_job.runner MAIN_PY")
    run_main(sys.argv[1])
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
