#!/usr/bin/env python3
"""
ZKA Voice Audio Server (HTTP version)
- POST /process-audio - send audio, get text response
- POST /auth - validate password
- GET  /events/{sid}  - SSE stream for response + audio
Features: password protection + Telegram Voice topic integration
"""
import asyncio
import json
import base64
import os
import re
import sys
import tempfile
import uuid
import hashlib
import time
from pathlib import Path
from collections import defaultdict

import aiohttp
from aiohttp import web
from dotenv import load_dotenv
from hermes_context import build_system_prompt, handle_voice_command, execute_tool, parse_tool_call

load_dotenv(Path(__file__).parent / ".env")

GROQ_API_KEY=os.environ.get("GROQ_API_KEY", "")
GROQ_API_URL = "https://api.groq.com/openai/v1"
GC_API_KEY=os.environ.get("GC_API_KEY", "")
GC_API_URL = "https://api.generalcompute.com/v1"
GC_MODEL = "minimax-m2.7"
MIMO_API_KEY=os.environ.get("MIMO_API_KEY", "")
MIMO_API_URL = "https://api.xiaomimimo.com/v1"
MIMO_LLM_MODEL = "mimo-v2-flash"  # Fast & cheap for voice
MIMO_ASR_MODEL = "mimo-v2.5-asr"
MIMO_TTS_MODEL = "mimo-v2.5-tts"
PORT = 8082

# Password (set via VOICE_PASSWORD env or default)
VOICE_PASSWORD = os.environ.get("VOICE_PASSWORD", "zka2026")
# Telegram Voice topic
TELEGRAM_TARGET = "telegram:-1003744814454:3828"

# Saved fMP4 init segment (ftyp + moov) from first valid iOS recording
_saved_init_segment: bytes = b""

# Auth tokens (token -> expiry timestamp)
_auth_tokens: dict[str, float] = {}
TOKEN_TTL = 86400  # 24 hours

# Session -> conversation history
conversations = defaultdict(list)
# Session -> pending response (text + audio)
responses = {}
# SSE queues per session
queues = defaultdict(list)


def make_token(password: str) -> str | None:
    """Validate password and return auth token."""
    if password != VOICE_PASSWORD:
        return None
    token = hashlib.sha256(f"{uuid.uuid4()}{time.time()}".encode()).hexdigest()[:32]
    _auth_tokens[token] = time.time() + TOKEN_TTL
    return token


def check_token(token: str) -> bool:
    """Validate auth token."""
    if not token:
        return False
    exp = _auth_tokens.get(token, 0)
    if time.time() > exp:
        _auth_tokens.pop(token, None)
        return False
    return True


async def mimo_tts(text: str) -> str | None:
    """Generate TTS audio using MiMo TTS API. Returns WAV bytes as base64 or None."""
    try:
        async with aiohttp.ClientSession() as session:
            async with session.post(
                f"{MIMO_API_URL}/chat/completions",
                headers={
                    "Authorization": f"Bearer {MIMO_API_KEY}",
                    "Content-Type": "application/json",
                },
                json={
                    "model": MIMO_TTS_MODEL,
                    "messages": [
                        {"role": "user", "content": "Berbicara dengan nada santai dan friendly, kecepatan sedang."},
                        {"role": "assistant", "content": text}
                    ],
                    "audio": {
                        "format": "wav",
                        "voice": "Chloe"
                    }
                },
            ) as resp:
                if resp.status != 200:
                    error = await resp.text()
                    print(f"[MIMO TTS] Failed: {error[:200]}")
                    return None
                result = await resp.json()
                # Extract audio from response
                choices = result.get("choices", [])
                if not choices:
                    print("[MIMO TTS] No choices in response")
                    return None
                message = choices[0].get("message", {})
                audio_data = message.get("audio", {})
                if isinstance(audio_data, dict):
                    audio_b64 = audio_data.get("data", "")
                elif isinstance(audio_data, str):
                    audio_b64 = audio_data
                else:
                    print(f"[MIMO TTS] Unexpected audio format: {type(audio_data)}")
                    return None
                if not audio_b64:
                    print("[MIMO TTS] No audio data in response")
                    return None
                print(f"[MIMO TTS] Generated audio ({len(audio_b64)} chars base64)")
                return audio_b64
    except Exception as e:
        print(f"[MIMO TTS] Error: {e}")
        return None


async def fallback_tts(text: str) -> str | None:
    """Fallback TTS using edge-tts. Returns WAV bytes as base64 or None."""
    try:
        with tempfile.NamedTemporaryFile(suffix=".mp3", delete=False) as f:
            tts_path = f.name
        proc = await asyncio.create_subprocess_exec(
            "edge-tts", "--voice", "id-ID-ArdiNeural", "--rate", "+30%",
            "--text", text, "--write-media", tts_path,
            stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE)
        await proc.wait()
        if not os.path.exists(tts_path):
            return None
        wav_path = tts_path.replace(".mp3", ".wav")
        proc2 = await asyncio.create_subprocess_exec(
            "ffmpeg", "-y", "-i", tts_path, "-ar", "24000", "-ac", "1", "-f", "wav", wav_path,
            stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE)
        await proc2.wait()
        if os.path.exists(wav_path):
            with open(wav_path, "rb") as f:
                audio_b64 = base64.b64encode(f.read()).decode()
            os.unlink(tts_path)
            os.unlink(wav_path)
            return audio_b64
        os.unlink(tts_path)
        return None
    except Exception as e:
        print(f"[Fallback TTS] Error: {e}")
        return None


async def send_to_telegram(user_text: str, assistant_text: str):
    """Send conversation to Telegram Voice topic via hermes send."""
    msg = f"🎙️ *Voice Chat*\n\n👤 {user_text}\n\n🤖 {assistant_text}"
    try:
        proc = await asyncio.create_subprocess_exec(
            "hermes", "send", "-t", TELEGRAM_TARGET, "-q", msg,
            stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE)
        await proc.wait()
        if proc.returncode == 0:
            print(f"[TG] Sent to Voice topic")
        else:
            stderr = (await proc.stderr.read()).decode()
            print(f"[TG] Failed: {stderr}")
    except Exception as e:
        print(f"[TG] Error: {e}")


async def process_audio(session_id: str, audio_bytes: bytes, content_type: str, mode: str = "chat"):
    conversation = conversations[session_id]
    try:
        global _saved_init_segment
        # Build system prompt based on mode
        if mode == "command":
            system_prompt = build_system_prompt()  # Full control center with tools
        else:
            # Chat mode — simple conversational prompt
            from hermes_context import get_wib_now, load_user_profile
            now = get_wib_now()
            user_profile = load_user_profile()
            system_prompt = f"""Kamu adalah ZKA (Zero Knowledge Agent) — AI assistant suara yang santai dan friendly.

WAKTU: {now}

{user_profile}

Aturan:
- Bahasa Indonesia, informal/slang (loe, gue, dong, sih)
- Jawaban SINGKAT untuk voice (maks 2-3 kalimat)
- Natural, kayak ngobrol sama temen
- JANGAN gunakan tool call, cukup jawab natural
- Kalau user minta aksi teknis (edit cronjob, cek status, dll), sarankan switch ke mode Command
"""
        # Debug logging
        print(f"[DEBUG] Session: {session_id}, Content-Type: {content_type}, Size: {len(audio_bytes)} bytes")
        print(f"[DEBUG] First 20 bytes: {audio_bytes[:20]}")
        
        # Fix iOS fMP4: if data starts with 'moof' (fragment without init segment),
        # prepend saved init segment from first valid recording
        if audio_bytes[4:8] == b"moof" and _saved_init_segment:
            print(f"[DEBUG] Detected fMP4 fragment, prepending init segment ({len(_saved_init_segment)} bytes)")
            audio_bytes = _saved_init_segment + audio_bytes
        
        # 1. STT
        audio_ext = "webm"
        if "mp4" in content_type or "aac" in content_type:
            audio_ext = "m4a"
        elif "wav" in content_type:
            audio_ext = "wav"
        elif "ogg" in content_type:
            audio_ext = "ogg"

        # Save to temp file and validate
        with tempfile.NamedTemporaryFile(suffix=f".{audio_ext}", delete=False) as tmp:
            tmp.write(audio_bytes)
            tmp_path = tmp.name
        
        # Check if file is valid
        file_size = os.path.getsize(tmp_path)
        print(f"[DEBUG] Temp file: {tmp_path}, size: {file_size}")
        
        if file_size < 100:
            os.unlink(tmp_path)
            return {"error": f"Audio terlalu kecil: {file_size} bytes"}

        # Normalize with ffmpeg to WAV before sending to Groq
        normalized_path = tmp_path + ".wav"
        norm_proc = await asyncio.create_subprocess_exec(
            "ffmpeg", "-y", "-i", tmp_path, "-ar", "16000", "-ac", "1", "-f", "wav", normalized_path,
            stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE)
        await norm_proc.wait()
        
        if os.path.exists(normalized_path) and os.path.getsize(normalized_path) > 100:
            audio_bytes = open(normalized_path, "rb").read()
            audio_ext = "wav"
            print(f"[DEBUG] Normalized: {len(audio_bytes)} bytes")
        else:
            print(f"[DEBUG] ffmpeg normalize failed, trying raw audio")
            if content_type and "mp4" in content_type:
                raw_path = tmp_path + ".aac"
                raw_proc = await asyncio.create_subprocess_exec(
                    "ffmpeg", "-y", "-i", tmp_path, "-c:a", "copy", "-f", "adts", raw_path,
                    stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE)
                await raw_proc.wait()
                if os.path.exists(raw_path) and os.path.getsize(raw_path) > 100:
                    audio_bytes = open(raw_path, "rb").read()
                    audio_ext = "aac"
                    print(f"[DEBUG] Raw AAC extract: {len(audio_bytes)} bytes")
                    os.unlink(raw_path)
        
        # Save init segment from first valid MP4 recording
        if not _saved_init_segment and content_type and "mp4" in content_type:
            raw = open(tmp_path, "rb").read() if os.path.exists(tmp_path) else b""
            if not raw:
                raw = audio_bytes
            moof_pos = raw.find(b"moof")
            if moof_pos > 0:
                init_seg = raw[:moof_pos - 4]
                if len(init_seg) > 20:
                    _saved_init_segment = init_seg
                    print(f"[DEBUG] Saved fMP4 init segment: {len(init_seg)} bytes")
        
        os.unlink(tmp_path)
        if os.path.exists(normalized_path):
            os.unlink(normalized_path)

        # STT via MiMo ASR (chat/completions with input_audio)
        # MiMo requires data URL format: data:audio/wav;base64,...
        mime_type = "audio/wav" if audio_ext == "wav" else "audio/mpeg"
        audio_b64_str = f"data:{mime_type};base64,{base64.b64encode(audio_bytes).decode()}"

        async with aiohttp.ClientSession() as session:
            async with session.post(
                f"{MIMO_API_URL}/chat/completions",
                headers={
                    "Authorization": f"Bearer {MIMO_API_KEY}",
                    "Content-Type": "application/json",
                },
                json={
                    "model": MIMO_ASR_MODEL,
                    "messages": [
                        {
                            "role": "user",
                            "content": [
                                {
                                    "type": "input_audio",
                                    "input_audio": {
                                        "data": audio_b64_str,
                                        "format": "wav" if audio_ext == "wav" else "mp3"
                                    }
                                }
                            ]
                        }
                    ]
                },
            ) as resp:
                if resp.status != 200:
                    error = await resp.text()
                    return {"error": f"STT failed: {error}"}
                result = await resp.json()
                # MiMo ASR returns transcription in choices[0].message.content
                choices = result.get("choices", [])
                if choices:
                    user_text = choices[0].get("message", {}).get("content", "").strip()
                else:
                    user_text = ""

        if not user_text:
            return {"error": "No speech detected"}

        # 1.5 Check if it's a voice command (handle directly, skip LLM)
        cmd_result = handle_voice_command(user_text)
        if cmd_result:
            print(f"[CMD] Command detected: {user_text}")
            asyncio.create_task(send_to_telegram(user_text, cmd_result))
            # Generate TTS using MiMo (fallback to edge-tts)
            audio_b64 = await mimo_tts(cmd_result)
            if not audio_b64:
                audio_b64 = await fallback_tts(cmd_result)
            if audio_b64:
                return {"user_text": user_text, "assistant_text": cmd_result, "audio": audio_b64}
            return {"user_text": user_text, "assistant_text": cmd_result}

        # 2. LLM
        conversation.append({"role": "user", "content": user_text})
        if len(conversation) > 20:
            conversation[:] = conversation[-20:]

        max_tool_rounds = 3 if mode == "command" else 0  # Tool calls only in command mode
        assistant_text = ""
        
        for round_num in range(max_tool_rounds + 1):
            async with aiohttp.ClientSession() as session:
                async with session.post(
                    f"{MIMO_API_URL}/chat/completions",
                    headers={"Authorization": f"Bearer {MIMO_API_KEY}", "Content-Type": "application/json"},
                    json={
                        "model": MIMO_LLM_MODEL,
                        "messages": [
                            {"role": "system", "content": system_prompt}
                        ] + conversation,
                        "max_tokens": 400,
                        "temperature": 0.7,
                    },
                ) as resp:
                    if resp.status != 200:
                        error = await resp.text()
                        return {"error": f"LLM failed: {error}"}
                    result = await resp.json()
                    assistant_text = result["choices"][0]["message"]["content"]

            # Check for tool call
            tool_call = parse_tool_call(assistant_text)
            if not tool_call:
                break  # No tool call, use as final response
            
            tool_name, tool_args = tool_call
            print(f"[TOOL] Round {round_num+1}: {tool_name}({tool_args})")
            
            # Execute tool
            tool_result = execute_tool(tool_name, tool_args)
            print(f"[TOOL] Result: {tool_result[:200]}...")
            
            # Feed tool result back to LLM for natural language summary
            conversation.append({"role": "assistant", "content": assistant_text})
            conversation.append({"role": "user", "content": f"Tool result:\n{tool_result}\n\nJawab dalam bahasa Indonesia, singkat untuk voice (maks 3-4 kalimat). Jangan kembalikan tool call lagi."})
        
        # Remove tool call artifacts from final response
        assistant_text = re.sub(r'```tool\s*\n.*?\n```', '', assistant_text, flags=re.DOTALL).strip()
        assistant_text = re.sub(r'\{"tool"\s*:.*?\}', '', assistant_text).strip()
        
        if not assistant_text:
            assistant_text = "Oke, udah gue lakuin!"

        conversation.append({"role": "assistant", "content": assistant_text})

        # 3. TTS (MiMo with edge-tts fallback)
        audio_b64 = await mimo_tts(assistant_text)
        if not audio_b64:
            print("[TTS] MiMo TTS failed, falling back to edge-tts")
            audio_b64 = await fallback_tts(assistant_text)
        if not audio_b64:
            return {"error": "TTS failed (both MiMo and edge-tts)"}

        # 4. Send to Telegram Voice topic (async, don't block response)
        asyncio.create_task(send_to_telegram(user_text, assistant_text))

        return {"user_text": user_text, "assistant_text": assistant_text, "audio": audio_b64}

    except Exception as e:
        return {"error": str(e)}


async def auth_handler(request):
    """POST /auth - validate password, return token."""
    try:
        data = await request.json()
        password = data.get("password", "")
        token = make_token(password)
        if token:
            return web.json_response({"ok": True, "token": token})
        return web.json_response({"ok": False, "error": "Password salah"}, status=401)
    except Exception as e:
        return web.json_response({"error": str(e)}, status=500)


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
        data = await request.json()
        audio_b64 = data.get("audio", "")
        content_type = data.get("content_type", "audio/webm")
        session_id = data.get("sid") or str(uuid.uuid4())
        token = data.get("token", "")
        mode = data.get("mode", "chat")  # 'chat' or 'command'

        # Check auth
        if not check_token(token):
            return web.json_response({"error": "Unauthorized"}, status=401)

        import base64 as _b64
        try:
            audio_bytes = _b64.b64decode(audio_b64)
        except Exception:
            return web.json_response({"error": "Invalid base64 audio"}, status=400)

        # Process synchronously
        result = await process_audio(session_id, audio_bytes, content_type, mode=mode)
        return web.json_response(result)
    except Exception as e:
        return web.json_response({"error": str(e)}, status=500)


async def index_handler(request):
    return web.FileResponse("./web-client/index.html")


async def health_handler(request):
    return web.Response(text="OK", content_type="text/plain")


app = web.Application()
app.router.add_get("/", index_handler)
app.router.add_get("/health", health_handler)
app.router.add_get("/events/{sid}", sse_handler)
app.router.add_post("/auth", auth_handler)
app.router.add_post("/process-audio", process_handler)

if __name__ == "__main__":
    print(f"ZKA Voice HTTP Server on port {PORT}")
    print(f"Password: {VOICE_PASSWORD}")
    print(f"Telegram target: {TELEGRAM_TARGET}")
    web.run_app(app, host="0.0.0.0", port=PORT)
