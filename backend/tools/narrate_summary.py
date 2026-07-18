from __future__ import annotations

import struct
import time
import http.client
import os
import urllib.error
import urllib.request
import wave
from io import BytesIO
from pathlib import Path
from typing import Any
from xml.sax.saxutils import escape

try:
    from ..artifact_registry import reserve_artifact, write_artifact
except ImportError:
    from artifact_registry import reserve_artifact, write_artifact


ROOT = Path(__file__).resolve().parents[2]
OUT_DIR = ROOT / "generated-outputs"


def narrate_summary(
    text: str,
    voice: str = "zh-CN-XiaoxiaoNeural",
    *,
    workspace_id: str,
) -> dict[str, str | int]:
    reservation = reserve_artifact(
        workspace_id=workspace_id,
        kind="audio_summary",
        content_type="audio/wav",
        suffix=".wav",
    )
    mode = "local-fallback"
    speech_error = ""
    speech_warning = ""
    try:
        region = os.environ.get("SPEECH_REGION", "eastus2")
        speech_key = os.environ.get("SPEECH_KEY")
        ssml = (
            "<speak version='1.0' xml:lang='zh-CN'>"
            f"<voice xml:lang='zh-CN' name='{escape(voice)}'>{escape(text[:3000])}</voice>"
            "</speak>"
        )
        req = urllib.request.Request(
            f"https://{region}.tts.speech.microsoft.com/cognitiveservices/v1",
            data=ssml.encode("utf-8"),
            method="POST",
        )
        if speech_key:
            req.add_header("Ocp-Apim-Subscription-Key", speech_key)
        else:
            from azure.identity import DefaultAzureCredential

            token = DefaultAzureCredential().get_token("https://cognitiveservices.azure.com/.default")
            req.add_header("Authorization", f"Bearer {token.token}")
        req.add_header("Content-Type", "application/ssml+xml")
        req.add_header("X-Microsoft-OutputFormat", "riff-16khz-16bit-mono-pcm")
        req.add_header("User-Agent", "dataforge-agent")
        with urllib.request.urlopen(req, timeout=float(os.environ.get("DF_SPEECH_TIMEOUT_SECONDS", "120"))) as resp:
            try:
                audio = resp.read()
            except http.client.IncompleteRead as exc:
                audio = exc.partial
                speech_warning = f"IncompleteRead: kept {len(audio)} partial bytes"
        if audio.startswith(b"RIFF") and len(audio) > 1000:
            record = write_artifact(reservation, audio, OUT_DIR)
            path = OUT_DIR / str(record["artifact_name"])
            mode = "azure-speech"
            result: dict[str, str | int] = {
                "audio_blob_url": path.as_uri(),
                "artifact_name": str(record["artifact_name"]),
                "artifact_url": f"/api/artifacts/{record['artifact_name']}",
                "local_path": str(path),
                "bytes": int(record["bytes"]),
                "voice": voice,
                "mode": mode,
                "speech_error": speech_error,
                "speech_warning": speech_warning,
            }
            _attach_blob(result, record)
            return result
    except Exception as exc:
        mode = "local-fallback"
        speech_error = f"{type(exc).__name__}: {exc}"

    sample_rate = 16000
    duration_s = max(1, min(6, len(text) // 80 + 1))
    frames = []
    for i in range(sample_rate * duration_s):
        # Quiet deterministic tone; cloud Speech replacement plugs into the same contract.
        value = int(1200 * ((i // 40) % 2 - 0.5))
        frames.append(struct.pack("<h", value))
    buffer = BytesIO()
    with wave.open(buffer, "wb") as wf:
        wf.setnchannels(1)
        wf.setsampwidth(2)
        wf.setframerate(sample_rate)
        wf.writeframes(b"".join(frames))
    record = write_artifact(reservation, buffer.getvalue(), OUT_DIR)
    path = OUT_DIR / str(record["artifact_name"])
    result = {
        "audio_blob_url": path.as_uri(),
        "artifact_name": str(record["artifact_name"]),
        "artifact_url": f"/api/artifacts/{record['artifact_name']}",
        "local_path": str(path),
        "bytes": int(record["bytes"]),
        "voice": voice,
        "mode": mode,
        "speech_error": speech_error,
        "speech_warning": speech_warning,
    }
    _attach_blob(result, record)
    return result


def _attach_blob(result: dict[str, str | int], record: dict[str, Any]) -> None:
    if record.get("blob_url"):
        result["audio_blob_url"] = str(record["blob_url"])
        result["blob_name"] = str(record.get("blob_name") or "")
