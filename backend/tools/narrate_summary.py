from __future__ import annotations

import struct
import time
import urllib.error
import urllib.request
import wave
from pathlib import Path
from xml.sax.saxutils import escape


ROOT = Path(__file__).resolve().parents[2]
OUT_DIR = ROOT / "generated-outputs"


def narrate_summary(text: str, voice: str = "zh-CN-XiaoxiaoNeural") -> dict[str, str | int]:
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    path = OUT_DIR / f"summary-{int(time.time())}.wav"
    mode = "local-fallback"
    speech_error = ""
    try:
        import os

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
        with urllib.request.urlopen(req, timeout=60) as resp:
            audio = resp.read()
        if audio.startswith(b"RIFF") and len(audio) > 1000:
            path.write_bytes(audio)
            mode = "azure-speech"
            return {"audio_blob_url": path.as_uri(), "local_path": str(path), "bytes": path.stat().st_size, "voice": voice, "mode": mode, "speech_error": speech_error}
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
    return {"audio_blob_url": path.as_uri(), "local_path": str(path), "bytes": path.stat().st_size, "voice": voice, "mode": mode, "speech_error": speech_error}
