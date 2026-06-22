# Copyright 2026 玑渡 GIDO Contributors
# SPDX-License-Identifier: Apache-2.0
from unittest.mock import MagicMock, patch

import pytest

from app.services.nacos_config import (
    build_nacos_preview_payload,
    fetch_nacos_config_content,
    merge_nacos_params,
    parse_nacos_params_from_program_args,
    validate_nacos_server_addr,
)


def test_parse_nacos_params_space_separated():
    args = (
        "--flink.nacos.dataId my-app.yaml "
        "--flink.nacos.group DEFAULT "
        "--flink.nacos.namespaceId dev "
        "--flink.nacos.serverAddr http://10.0.0.1:8848 "
        "--flink.nacos.username nacos "
        "--flink.nacos.password secret"
    )
    params = parse_nacos_params_from_program_args(args)
    assert params["flink.nacos.dataId"] == "my-app.yaml"
    assert params["flink.nacos.group"] == "DEFAULT"
    assert params["flink.nacos.namespaceId"] == "dev"
    assert params["flink.nacos.serverAddr"] == "http://10.0.0.1:8848"
    assert params["flink.nacos.username"] == "nacos"
    assert params["flink.nacos.password"] == "secret"


def test_parse_nacos_params_equals_form():
    args = "--flink.nacos.dataId=app.properties --flink.nacos.group=MY_GROUP --flink.nacos.serverAddr=http://nacos:8848"
    params = parse_nacos_params_from_program_args(args)
    assert params["flink.nacos.dataId"] == "app.properties"
    assert params["flink.nacos.group"] == "MY_GROUP"
    assert params["flink.nacos.serverAddr"] == "http://nacos:8848"


def test_merge_nacos_params_program_args_override_props():
    props = {
        "flink.nacos.dataId": "from-props",
        "flink.nacos.group": "G1",
        "flinkConfiguration": {"flink.nacos.serverAddr": "http://old:8848"},
    }
    args = "--flink.nacos.dataId from-args --flink.nacos.serverAddr http://new:8848"
    merged = merge_nacos_params(args, props)
    assert merged["flink.nacos.dataId"] == "from-args"
    assert merged["flink.nacos.group"] == "G1"
    assert merged["flink.nacos.serverAddr"] == "http://new:8848"


def test_build_nacos_preview_no_params():
    payload = build_nacos_preview_payload("--other flag", None)
    assert payload["content"] is None
    assert payload["error"]
    assert "flink.nacos" in payload["error"]


def test_build_nacos_preview_strips_password():
    args = (
        "--flink.nacos.dataId x --flink.nacos.group g "
        "--flink.nacos.serverAddr http://10.0.0.1:8848 "
        "--flink.nacos.password p"
    )
    with patch("app.services.nacos_config.fetch_nacos_config_content", return_value="key: value"):
        payload = build_nacos_preview_payload(args, None)
    assert payload["content"] == "key: value"
    assert "flink.nacos.password" not in payload["params"]


@patch("app.services.nacos_config.httpx.Client")
def test_fetch_nacos_config_content(mock_client_cls):
    mock_login_resp = MagicMock()
    mock_login_resp.status_code = 200
    mock_login_resp.json.return_value = {"accessToken": "tok-abc", "tokenTtl": 18000}

    mock_config_resp = MagicMock()
    mock_config_resp.status_code = 200
    mock_config_resp.text = "foo: bar"

    mock_client = MagicMock()
    mock_client.__enter__.return_value = mock_client
    mock_client.__exit__.return_value = False
    mock_client.post.return_value = mock_login_resp
    mock_client.get.return_value = mock_config_resp
    mock_client_cls.return_value = mock_client

    content = fetch_nacos_config_content({
        "flink.nacos.dataId": "d",
        "flink.nacos.group": "g",
        "flink.nacos.serverAddr": "http://10.0.0.1:8848",
        "flink.nacos.namespaceId": "ns1",
        "flink.nacos.username": "u",
        "flink.nacos.password": "p",
    })
    assert content == "foo: bar"
    mock_client.post.assert_called_once_with(
        "http://10.0.0.1:8848/nacos/v1/auth/login",
        data={"username": "u", "password": "p"},
    )
    mock_client.get.assert_called_once()
    call_kwargs = mock_client.get.call_args
    assert call_kwargs[0][0] == "http://10.0.0.1:8848/nacos/v1/cs/configs"
    assert call_kwargs[1]["params"] == {
        "dataId": "d",
        "group": "g",
        "tenant": "ns1",
        "accessToken": "tok-abc",
    }


@patch("app.services.nacos_config.httpx.Client")
def test_fetch_nacos_config_content_no_auth(mock_client_cls):
    mock_config_resp = MagicMock()
    mock_config_resp.status_code = 200
    mock_config_resp.text = "plain"
    mock_client = MagicMock()
    mock_client.__enter__.return_value = mock_client
    mock_client.__exit__.return_value = False
    mock_client.get.return_value = mock_config_resp
    mock_client_cls.return_value = mock_client

    content = fetch_nacos_config_content({
        "flink.nacos.dataId": "d",
        "flink.nacos.group": "g",
        "flink.nacos.serverAddr": "http://10.0.0.1:8848",
    })
    assert content == "plain"
    mock_client.post.assert_not_called()
    assert "accessToken" not in mock_client.get.call_args[1]["params"]


def test_validate_nacos_server_addr_rejects_bad_scheme(monkeypatch):
    monkeypatch.setattr("app.services.nacos_config.settings.GIDO_NACOS_ALLOW_INSECURE", False)
    with pytest.raises(ValueError, match="HTTP"):
        validate_nacos_server_addr("http://10.0.0.1:8848")
