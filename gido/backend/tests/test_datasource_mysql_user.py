# Copyright 2026 玑渡 GIDO Contributors
# SPDX-License-Identifier: Apache-2.0
# @author felixzhu
# @date 2026-06-05
from types import SimpleNamespace

from app.services.datasource_mysql_user import mysql_protocol_connect_user


def test_doris_empty_user_becomes_root():
    ds = SimpleNamespace(ds_type="doris", username="")
    assert mysql_protocol_connect_user(ds) == "root"
    ds2 = SimpleNamespace(ds_type="doris", username=None)
    assert mysql_protocol_connect_user(ds2) == "root"
    ds3 = SimpleNamespace(ds_type="doris", username="  u1  ")
    assert mysql_protocol_connect_user(ds3) == "u1"


def test_mysql_empty_user_stays_empty():
    ds = SimpleNamespace(ds_type="mysql", username="")
    assert mysql_protocol_connect_user(ds) == ""
