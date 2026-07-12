from __future__ import annotations

import asyncio
from contextvars import ContextVar

import pytest

from backend.app import _sse_keepalive


@pytest.mark.asyncio
async def test_sse_keepalive_consumes_generator_in_one_context() -> None:
    marker: ContextVar[str] = ContextVar("sse_keepalive_context", default="none")

    async def source():
        token = marker.set("active")
        try:
            yield "event: first\ndata: {}\n\n"
            await asyncio.sleep(0.03)
            yield "event: second\ndata: {}\n\n"
        finally:
            marker.reset(token)

    frames = [frame async for frame in _sse_keepalive(source(), interval=0.005)]

    assert frames[0].startswith("event: first")
    assert frames[-1].startswith("event: second")
    assert ": keepalive\n\n" in frames
    assert marker.get() == "none"
