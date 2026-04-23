"""Tests for JSON-RPC request/response schemas."""
import json

import pytest
from pydantic import ValidationError

from sagewai_tool_runner.rpc import (
    RpcError,
    RpcRequest,
    RpcResponse,
    parse_request,
)


def test_request_round_trip():
    raw = {
        "jsonrpc": "2.0",
        "method": "exec",
        "params": {"tool": "bash", "args": {"command": "ls"}, "call_id": "c1"},
        "id": 1,
    }
    req = RpcRequest.model_validate(raw)
    assert req.method == "exec"
    assert req.params["tool"] == "bash"


def test_request_missing_jsonrpc_rejected():
    with pytest.raises(ValidationError):
        RpcRequest.model_validate({"method": "exec", "id": 1})


def test_response_ok_shape():
    resp = RpcResponse(id=1, result={"ok": True})
    data = json.loads(resp.model_dump_json())
    assert data["jsonrpc"] == "2.0"
    assert data["result"] == {"ok": True}
    assert "error" not in data or data["error"] is None


def test_response_error_shape():
    resp = RpcResponse(
        id=1, error=RpcError(code=-32601, message="method not found")
    )
    data = json.loads(resp.model_dump_json())
    assert data["error"]["code"] == -32601


def test_parse_request_bad_json():
    with pytest.raises(ValueError):
        parse_request("{ not json")
