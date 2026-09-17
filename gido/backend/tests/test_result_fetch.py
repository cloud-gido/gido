# Copyright 2026 玑渡 GIDO Contributors
# SPDX-License-Identifier: Apache-2.0
from app.services.result_fetch import fetch_result_batch
from app.services.adhoc_run_store import (
    _encode_result_rows,
    _fit_result_rows_to_bytes,
)


class Cursor:
    def __init__(self, rows):
        self.rows = list(rows)
        self.requests = []

    def fetchmany(self, size):
        self.requests.append(size)
        result = self.rows[:size]
        self.rows = self.rows[size:]
        return result


def test_progressive_fetch_returns_small_first_page_then_large_batch():
    cursor = Cursor(range(5000))

    first = fetch_result_batch(
        cursor,
        fetched_rows=0,
        max_rows=10_000,
        first_chunk_rows=200,
        chunk_rows=2000,
    )
    second = fetch_result_batch(
        cursor,
        fetched_rows=len(first.rows),
        max_rows=10_000,
        first_chunk_rows=200,
        chunk_rows=2000,
    )

    assert len(first.rows) == 200
    assert len(second.rows) == 2000
    assert cursor.requests == [200, 2000]


def test_final_fetch_detects_overflow_without_an_extra_round_trip():
    cursor = Cursor(range(201))

    batch = fetch_result_batch(
        cursor,
        fetched_rows=0,
        max_rows=200,
        first_chunk_rows=200,
        chunk_rows=2000,
    )

    assert len(batch.rows) == 200
    assert batch.overflow is True
    assert cursor.requests == [201]


def test_result_byte_limit_uses_largest_fitting_row_prefix():
    rows = [[index, "x" * 20] for index in range(100)]

    fitted, raw = _fit_result_rows_to_bytes(rows, 200)

    assert fitted
    assert len(raw) <= 200
    assert len(_encode_result_rows(rows[: len(fitted) + 1])) > 200
