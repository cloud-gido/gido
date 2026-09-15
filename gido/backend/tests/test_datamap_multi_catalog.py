# Copyright 2026 玑渡 GIDO Contributors
# SPDX-License-Identifier: Apache-2.0
from types import SimpleNamespace

from app.api.datamap import _explicit_datamap_catalogs, _mysql_catalogs_to_scan


def test_explicit_datamap_catalogs_from_list_and_csv():
    ds = SimpleNamespace(extra_config={"catalogs": ["bigdata_ads", "bigdata_dw"]})
    assert _explicit_datamap_catalogs(ds) == ["bigdata_ads", "bigdata_dw"]
    ds2 = SimpleNamespace(extra_config={"datamap_catalogs": "a, b;c"})
    assert _explicit_datamap_catalogs(ds2) == ["a", "b", "c"]
    ds3 = SimpleNamespace(extra_config=None)
    assert _explicit_datamap_catalogs(ds3) == []


def test_mysql_catalogs_prefer_explicit_and_put_default_first():
    ds = SimpleNamespace(database="bigdata_ads", extra_config={"catalogs": ["bigdata_dw", "bigdata_ads"]})

    class Cur:
        def execute(self, *_a, **_k):
            return None

        def fetchall(self):
            return [("zzz",), ("bigdata_ads",), ("bigdata_dw",)]

    assert _mysql_catalogs_to_scan(ds, Cur()) == ["bigdata_dw", "bigdata_ads"]

    ds_all = SimpleNamespace(database="bigdata_ads", extra_config={})
    names = _mysql_catalogs_to_scan(ds_all, Cur())
    assert names[0] == "bigdata_ads"
    assert "bigdata_dw" in names
    assert "zzz" in names
