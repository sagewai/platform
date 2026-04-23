"""stdio JSON-RPC loop. Reads one request per line, writes one response per line."""
from __future__ import annotations

import asyncio
import sys

from sagewai_tool_runner.rpc import RpcError, RpcRequest, RpcResponse, parse_request
from sagewai_tool_runner.tools.bash import run_bash

_TOOLS = {
    "bash": run_bash,
}


async def _handle(req: RpcRequest) -> RpcResponse:
    if req.method != "exec":
        return RpcResponse(
            id=req.id, error=RpcError(code=-32601, message=f"method not found: {req.method}")
        )
    tool = req.params.get("tool")
    args = req.params.get("args", {})
    call_id = req.params.get("call_id", "")
    timeout_s = float(req.params.get("timeout_s", 60.0))

    handler = _TOOLS.get(tool)
    if handler is None:
        return RpcResponse(
            id=req.id, error=RpcError(code=-32602, message=f"unknown tool: {tool}")
        )
    raw = await handler(args, timeout_s=timeout_s)
    return RpcResponse(id=req.id, result={"call_id": call_id, **raw})


async def _loop() -> None:
    loop = asyncio.get_event_loop()
    reader = asyncio.StreamReader()
    proto = asyncio.StreamReaderProtocol(reader)
    await loop.connect_read_pipe(lambda: proto, sys.stdin)
    writer = sys.stdout

    while True:
        line = await reader.readline()
        if not line:
            return
        try:
            req = parse_request(line.decode("utf-8"))
        except ValueError as exc:
            err = RpcResponse(
                id=0,
                error=RpcError(code=-32700, message=f"parse error: {exc}"),
            )
            writer.write(err.model_dump_json() + "\n")
            writer.flush()
            continue

        resp = await _handle(req)
        writer.write(resp.model_dump_json() + "\n")
        writer.flush()


def main() -> None:
    try:
        asyncio.run(_loop())
    except KeyboardInterrupt:
        pass


if __name__ == "__main__":
    main()
