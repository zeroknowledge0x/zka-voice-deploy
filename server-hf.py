#!/usr/bin/env python3
"""
ZKA Voice Audio Server (HF Spaces / FastAPI version)
- POST /process-audio - send audio base64, get response
- GET  /health - health check
"""
import asyncio
import json
import base64
import os
import tempfile
import uuid
from collections import defaultdict
from pathlib import Path
from fastapi import FastAPI
from fastapi.responses import JSONResponse
from fastapi.middleware.cors import CORSMiddleware
from dotenv import load_dotenv
import aiohttp

load_dotenv(Path(__file__).parent / ".env")

GROQ_API_KEY = os.environ.get("GROQ_API_KEY", "")
GROQ_API_URL = "https://api.groq.com/openai/v1"

conversations = defaultdict(list)
queues = defaultdict(list)

app = FastAPI(title="ZKA Voice")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)


async def process_audio(session_id: str, audio_bytes: bytes, content_type: str):
    conversation = conversations[session_id]
    try:
        # 1. STT
        audio_ext = "webm"
        if "mp4" in content_type or "aac" in content_type:
            audio_ext = "m4a"
        elif "wav" in content_type:
            audio_ext = "wav"
        elif "ogg" in content_type:
            audio_ext = "ogg"

        form = aiohttp.FormData()
        form.add_field("file", audio_bytes, filename=f"audio.{audio_ext}", content_type=content_type)
        form.add_field("model", "whisper-large-v3")
        form.add_field("model", "whisper-large-v3-turbo")
        form.add_field("language", "id")

        async with aiohttp.ClientSession() as session:
            async with session.post(
                f"{GROQ_API_URL}/audio/transcriptions",
                headers={"Authorization": f"Bearer {GROQ_API_KEY}"},
                data=form,
            ) as resp:
                if resp.status != 200:
                    error = await resp.text()
                    return {"error": f"STT failed: {error}"}
                result = await resp.json()
                user_text = result.get("text", "").strip()

        if not user_text:
            return {"error": "No speech detected"}

        # 2. LLM
        conversation.append({"role": "user", "content": user_text})
        if len(conversation) > 20:
            conversation[:] = conversation[-20:]

        async with aiohttp.ClientSession() as session:
            async with session.post(
                f"{GROQ_API_URL}/chat/completions",
                headers={"Authorization": f"Bearer {GROQ_API_KEY}", "Content-Type": "application/json"},
                json={
                    "model": "llama-3.3-70b-versatile",
                    "messages": [
                        {"role": "system", "content": "Loe adalah ZKA (Zero Knowledge Agent), AI assistant yang ngobrol pake bahasa Indonesia gaul/santai. Jawab singkat dan natural, kayak ngobrol sama temen. Maksimal 2-3 kalimat. Jangan terlalu formal."}
                    ] + conversation,
                    "max_tokens": 200,
                    "temperature": 0.7,
                },
            ) as resp:
                if resp.status != 200:
                    error = await resp.text()
                    return {"error": f"LLM failed: {error}"}
                result = await resp.json()
                assistant_text = result["choices"][0]["message"]["content"]

        conversation.append({"role": "assistant", "content": assistant_text})

        # 3. TTS
        with tempfile.NamedTemporaryFile(suffix=".mp3", delete=False) as f:
            tts_path = f.name

        proc = await asyncio.create_subprocess_exec(
            "edge-tts",
            "--voice", "id-ID-ArdiNeural",
            "--rate", "+30%",
            "--text", assistant_text,
            "--write-media", tts_path,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        await proc.wait()

        if not os.path.exists(tts_path):
            return {"error": "TTS failed"}

        wav_path = tts_path.replace(".mp3", ".wav")
        proc2 = await asyncio.create_subprocess_exec(
            "ffmpeg", "-y", "-i", tts_path,
            "-ar", "24000", "-ac", "1", "-f", "wav", wav_path,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        await proc2.wait()

        with open(wav_path, "rb") as f:
            audio_b64 = base64.b64encode(f.read()).decode()

        os.unlink(tts_path)
        os.unlink(wav_path)

        return {"user_text": user_text, "assistant_text": assistant_text, "audio": audio_b64}
    except Exception as e:
        return {"error": str(e)}


@app.post("/process-audio")
async def process_handler(request: dict):
    try:
        audio_b64 = request.get("audio", "")
        content_type = request.get("content_type", "audio/webm")
        session_id = request.get("sid") or str(uuid.uuid4())

        try:
            audio_bytes = base64.b64decode(audio_b64)
        except Exception:
            return JSONResponse({"error": "Invalid base64 audio"}, status_code=400)

        asyncio.create_task(_process_and_emit(session_id, audio_bytes, content_type))
        return JSONResponse({"ok": True, "sid": session_id})
    except Exception as e:
        return JSONResponse({"error": str(e)}, status_code=500)


async def _process_and_emit(session_id: str, audio_bytes: bytes, content_type: str):
    # This is simplified for HF Spaces (no SSE, just process)
    result = await process_audio(session_id, audio_bytes, content_type)
    # Store in memory (HF Spaces: will be lost on restart)
    queues[session_id].append(result)


# No SSE in HF Spaces - client polls /poll endpoint
@app.get("/poll/{sid}")
async def poll_handler(sid: str):
    if queues.get(sid):
        result = queues[sid].pop()
        if not queues[sid]:
            del queues[sid]
        return JSONResponse(result)
    return JSONResponse({"status": "processing"})


@app.get("/health")
def health():
    return {"ok": True}


# HF Spaces: index.html web client
@app.get("/")
async def index():
    from fastapi.responses import FileResponse
    return FileResponse("./web-client/index.html")


if __name__ == "__main__":
    import uvicorn
    port = int(os.environ.get("PORT", 8080))
    uvicorn.run(app, host="0.0.0.0", port=port)
