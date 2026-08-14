"""SSE 帧编码——实时与回放共用（协议见架构文档 §4）。"""

from __future__ import annotations

import json
from typing import Any


def sse_frame(event_type: str, payload: dict[str, Any]) -> str:
    return f"event: {event_type}\ndata: {json.dumps(payload, ensure_ascii=False)}\n\n"


__all__ = ["sse_frame"]
