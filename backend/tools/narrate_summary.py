from __future__ import annotations

import struct
import time
import wave
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
OUT_DIR = ROOT / "generated-outputs"


def narrate_summary(text: str, voice: str = "zh-CN-XiaoxiaoNeural") -> dict[str, str | int]:
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    path = OUT_DIR / f"summary-{int(time.time())}.wav"
    sample_rate = 16000
    duration_s = max(1, min(6, len(text) // 80 + 1))
    frames = []
    for i in range(sample_rate * duration_s):
        # Quiet deterministic tone; cloud Speech replacement plugs into the same contract.
        value = int(1200 * ((i // 40) % 2 - 0.5))
        frames.append(struct.pack("<h", value))
    with wave.open(str(path), "wb") as wf:
        wf.setnchannels(1)
        wf.setsampwidth(2)
        wf.setframerate(sample_rate)
        wf.writeframes(b"".join(frames))
    return {"audio_blob_url": path.as_uri(), "local_path": str(path), "bytes": path.stat().st_size, "voice": voice}

