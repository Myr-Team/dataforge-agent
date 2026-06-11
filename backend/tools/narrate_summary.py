from __future__ import annotations

import struct
import time
import http.client
import os
import urllib.error
import urllib.request
import wave
from pathlib import Path
from xml.sax.saxutils import escape

try:
    from ..blob_store import upload_artifact
except ImportError:
    from blob_store import upload_artifact


ROOT = Path(__file__).resolve().parents[2]
OUT_DIR = ROOT / "generated-outputs"


def narrate_summary(text: str, voice: str = "zh-CN-XiaoxiaoNeural") -> dict[str, str | int]:
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    path = OUT_DIR / f"summary-{int(time.time())}.wav"
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
            path.write_bytes(audio)
            mode = "azure-speech"
            result: dict[str, str | int] = {
                "audio_blob_url": path.as_uri(),
                "artifact_name": path.name,
                "artifact_url": f"/api/artifacts/{path.name}",
                "local_path": str(path),
                "bytes": path.stat().st_size,
                "voice": voice,
                "mode": mode,
                "speech_error": speech_error,
                "speech_warning": speech_warning,
            }
            _attach_blob(result, path, "audio/wav")
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
    with wave.open(str(path), "wb") as wf:
        wf.setnchannels(1)
        wf.setsampwidth(2)
        wf.setframerate(sample_rate)
        wf.writeframes(b"".join(frames))
    result = {
        "audio_blob_url": path.as_uri(),
        "artifact_name": path.name,
        "artifact_url": f"/api/artifacts/{path.name}",
        "local_path": str(path),
        "bytes": path.stat().st_size,
        "voice": voice,
        "mode": mode,
        "speech_error": speech_error,
        "speech_warning": speech_warning,
    }
    _attach_blob(result, path, "audio/wav")
    return result


def _attach_blob(result: dict[str, str | int], path: Path, content_type: str) -> None:
    try:
        blob = upload_artifact(path.name, path.read_bytes(), content_type)
        result["audio_blob_url"] = str(blob.get("blob_url") or result["audio_blob_url"])
        result["blob_name"] = str(blob.get("blob_name") or "")
    except Exception as exc:
        result["blob_error"] = f"{type(exc).__name__}: {exc}"[:500]
