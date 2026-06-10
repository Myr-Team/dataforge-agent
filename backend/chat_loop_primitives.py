from __future__ import annotations

import asyncio
import json
import time
from typing import Any


HEARTBEAT_INTERVAL_S = 5


def sse(event: str, data: Any) -> str:
    payload = json.dumps(data, ensure_ascii=False) if isinstance(data, (dict, list)) else str(data)
    return f"event: {event}\ndata: {payload}\n\n"


def heartbeat_frame() -> str:
    return f": ping {int(time.time())}\n\n"


async def heartbeat_pumper(queue: asyncio.Queue[str]) -> None:
    try:
        while True:
            await asyncio.sleep(HEARTBEAT_INTERVAL_S)
            try:
                queue.put_nowait(heartbeat_frame())
            except asyncio.QueueFull:
                pass
    except asyncio.CancelledError:
        return


async def drain_heartbeat(queue: asyncio.Queue[str], max_wait: float = HEARTBEAT_INTERVAL_S) -> list[str]:
    frames: list[str] = []
    while True:
        try:
            frames.append(queue.get_nowait())
        except asyncio.QueueEmpty:
            break
    if frames:
        return frames
    try:
        frames.append(await asyncio.wait_for(queue.get(), timeout=max_wait))
    except asyncio.TimeoutError:
        frames.append(heartbeat_frame())
    return frames


def function_call_summary(name: str, call_id: str, arguments: Any) -> dict[str, Any]:
    if isinstance(arguments, str):
        try:
            args = json.loads(arguments)
        except json.JSONDecodeError:
            args = {"_raw": arguments[:500], "_unparseable": True}
    else:
        args = arguments
    return {"name": name, "call_id": call_id, "args": args, "args_keys": list(args) if isinstance(args, dict) else None}

