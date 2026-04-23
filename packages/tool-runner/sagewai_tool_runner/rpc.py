"""JSON-RPC 2.0 schemas and parse helpers."""
from __future__ import annotations

import json
from typing import Any, Literal

from pydantic import BaseModel, Field


class RpcError(BaseModel):
    code: int
    message: str
    data: Any | None = None


class RpcRequest(BaseModel):
    jsonrpc: Literal["2.0"] = Field(..., description="Protocol tag")
    method: str
    params: dict[str, Any] = Field(default_factory=dict)
    id: int | str


class RpcResponse(BaseModel):
    jsonrpc: Literal["2.0"] = "2.0"
    id: int | str
    result: Any | None = None
    error: RpcError | None = None


def parse_request(line: str) -> RpcRequest:
    """Parse a JSON-RPC request line. Raises ValueError on malformed input."""
    try:
        data = json.loads(line)
    except json.JSONDecodeError as exc:
        raise ValueError(f"malformed JSON: {exc}") from exc
    return RpcRequest.model_validate(data)
