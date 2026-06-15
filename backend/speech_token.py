from __future__ import annotations

import os
import urllib.request
from typing import Any


def issue_speech_token() -> dict[str, Any]:
    region = os.environ.get("SPEECH_REGION") or os.environ.get("DF_REGION")
    key = os.environ.get("SPEECH_KEY")
    if not region:
        raise RuntimeError("SPEECH_REGION or DF_REGION is not configured")
    if not key:
        raise RuntimeError("SPEECH_KEY is not configured")
    url = f"https://{region}.api.cognitive.microsoft.com/sts/v1.0/issueToken"
    req = urllib.request.Request(url, data=b"", method="POST")
    req.add_header("Ocp-Apim-Subscription-Key", key)
    req.add_header("Content-Length", "0")
    with urllib.request.urlopen(req, timeout=float(os.environ.get("DF_SPEECH_TOKEN_TIMEOUT_SECONDS", "8"))) as resp:
        token = resp.read().decode("utf-8")
        status = int(getattr(resp, "status", 200))
    if status < 200 or status >= 300 or not token:
        raise RuntimeError(f"Speech token endpoint returned HTTP {status}")
    return {"token": token, "region": region, "expires_in": 600}
