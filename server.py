#!/usr/bin/env python3
"""
ZKA Voice Audio Server (HTTP version)
- POST /process-audio - send audio, get text response
- GET  /events/{sid}  - SSE stream for response + audio
No WebSocket needed — works with any tunnel.
"""
import asyncio
import json
import base64
import os
import tempfile
import uuid
from pathlib import Path
from collections import defaultdict

import aiohttp
from aiohttp import web
from dotenv import load_dotenv

load_dotenv(Path(__file__).parent / ".env")

GROQ_API_KEY = os.environ.get("GROQ_API_KEY", "")
GROQ_API_URL = "https://api.groq.com/openai/v1"
PORT = 8082

# Session -> conversation history
conversations = defaultdict(list)
# Session -> pending response (text + audio)
responses = {}
# SSE queues per session
queues = defaultdict(list)


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
            "ffmpeg", "-y", "-i", tts_path, "-ar", "24000", "-ac", "1", "-f", "wav", wav_path,
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


async def sse_handler(request):
    session_id = request.match_info.get("sid", "")
    resp = web.StreamResponse(
        status=200,
        headers={
            "Content-Type": "text/event-stream",
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
            "Access-Control-Allow-Origin": "*",
        },
    )
    await resp.prepare(request)

    q = asyncio.Queue()
    queues[session_id].append(q)

    try:
        while True:
            data = await q.get()
            if data is None:
                break
            out = json.dumps(data)
            await resp.write(f"data: {out}\n\n".encode())
    except Exception:
        pass
    finally:
        queues[session_id].remove(q)
        if not queues[session_id]:
            del queues[session_id]

    return resp


async def process_handler(request):
    try:
        reader = await request.multipart()
        part = await reader.next()
        if part is None:
            return web.json_response({"error": "No file"}, status=400)

        content_type = part.headers.get("Content-Type", "audio/webm")
        audio_bytes = await part.read()
        session_id = request.query.get("sid", str(uuid.uuid4()))

        # Kick off background processing
        asyncio.create_task(_process_and_emit(session_id, audio_bytes, content_type))

        return web.json_response({"ok": True, "sid": session_id})

    except Exception as e:
        return web.json_response({"error": str(e)}, status=500)


async def _process_and_emit(session_id: str, audio_bytes: bytes, content_type: str):
    # Emit progress
    _emit(session_id, {"type": "processing", "stage": "Mendengarkan..."})
    await asyncio.sleep(0.05)
    _emit(session_id, {"type": "processing", "stage": "Mikir..."})
    await asyncio.sleep(0.05)

    result = await process_audio(session_id, audio_bytes, content_type)

    if "error" in result:
        _emit(session_id, {"type": "error", "message": result["error"]})
    else:
        _emit(session_id, {
            "type": "response",
            "user_text": result.get("user_text", ""),
            "assistant_text": result.get("assistant_text", ""),
            "audio": result.get("audio", ""),
        })


def _emit(session_id: str, data: dict):
    for q in queues.get(session_id, []):
        asyncio.ensure_future(q.put(data))


async def index_handler(request):
    return web.FileResponse("./web-client/index.html")


async def health_handler(request):
    return web.Response(text="OK", content_type="text/plain")


app = web.Application()
app.router.add_get("/", index_handler)
app.router.add_get("/health", health_handler)
app.router.add_get("/events/{sid}", sse_handler)
app.router.add_post("/process-audio", process_handler)

if __name__ == "__main__":
    print(f"ZKA Voice HTTP Server on port {PORT}")
    web.run_app(app, host="0.0.0.0", port=PORT)
