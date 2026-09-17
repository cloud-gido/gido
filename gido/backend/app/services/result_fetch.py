# Copyright 2026 玑渡 GIDO Contributors
# SPDX-License-Identifier: Apache-2.0
"""Progressive result fetching: a fast first page, then efficient larger batches."""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any, List


@dataclass(frozen=True)
class ResultFetchBatch:
    rows: List[Any]
    overflow: bool
    exhausted: bool


def fetch_result_batch(
    cursor: Any,
    *,
    fetched_rows: int,
    max_rows: int,
    first_chunk_rows: int,
    chunk_rows: int,
) -> ResultFetchBatch:
    remaining = max(0, int(max_rows) - int(fetched_rows))
    if remaining == 0:
        return ResultFetchBatch([], False, True)
    preferred = first_chunk_rows if fetched_rows == 0 else chunk_rows
    target = min(max(1, int(preferred)), remaining)
    final_batch = target == remaining
    requested = target + (1 if final_batch else 0)
    fetched = list(cursor.fetchmany(requested) or [])
    overflow = final_batch and len(fetched) > target
    return ResultFetchBatch(
        rows=fetched[:target],
        overflow=overflow,
        exhausted=len(fetched) < requested,
    )
