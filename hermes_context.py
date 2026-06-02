#!/usr/bin/env python3
"""
ZKA Voice — Full Hermes Control Center Context Builder
Provides system prompt + tool execution for voice agent.
Can: read, edit, create, delete cronjobs, manage memory, skills, 
send messages, run terminal commands, check server status, etc.
"""
import json
import os
import re
import subprocess
import time
from datetime import datetime, timezone, timedelta
from pathlib import Path

WIB = timezone(timedelta(hours=7))
CRON_PATH = Path.home() / ".hermes" / "cron" / "jobs.json"
MEMORY_PATH = Path.home() / ".hermes" / "memory" / "MEMORY.md"
USER_PATH = Path.home() / ".hermes" / "memory" / "USER.md"
SKILLS_DIR = Path.home() / ".hermes" / "skills"
SERVER_DIR = Path.home() / "livekit-voice-agent"


def get_wib_now():
    return datetime.now(WIB).strftime("%A, %d %B %Y, %H:%M WIB")


def load_cronjobs():
    """Load all cronjobs from jobs.json."""
    try:
        if CRON_PATH.exists():
            data = json.loads(CRON_PATH.read_text())
            if isinstance(data, list):
                return data
            return data.get("jobs", [])
    except Exception:
        pass
    return []


def format_cronjob_detail(job):
    """Format a single cronjob with ALL details."""
    name = job.get("name", "Unknown")
    job_id = job.get("id", "?")
    enabled = job.get("enabled", True)
    sched_raw = job.get("schedule", "?")
    schedule = sched_raw.get("display", str(sched_raw)) if isinstance(sched_raw, dict) else str(sched_raw)
    last_run = job.get("last_run_at", "Never")
    provider = job.get("provider", "default")
    model = job.get("model", "default")
    prompt = job.get("prompt", "")[:200]
    deliver = job.get("deliver", "origin")
    skills = job.get("skills", [])
    no_agent = job.get("no_agent", False)
    script = job.get("script", "")

    status = "✅ Aktif" if enabled else "⏸️ Paused"
    mode = "Script" if no_agent else "LLM"

    parts = [
        f"📋 {name} ({status})",
        f"  ID: {job_id}",
        f"  Schedule: {schedule}",
        f"  Mode: {mode}",
        f"  Provider: {provider}",
        f"  Model: {model}",
        f"  Deliver: {deliver}",
        f"  Last run: {last_run}",
    ]
    if skills:
        parts.append(f"  Skills: {', '.join(skills)}")
    if script:
        parts.append(f"  Script: {script}")
    if prompt:
        parts.append(f"  Prompt: {prompt}...")
    return "\n".join(parts)


def list_all_cronjobs_summary():
    """Summary of all cronjobs for system prompt."""
    jobs = load_cronjobs()
    if not jobs:
        return "Tidak ada cronjob."
    
    lines = []
    for j in jobs:
        status = "✅" if j.get("enabled", True) else "⏸️"
        name = j.get("name", "?")
        jid = j.get("id", "?")[:12]
        sched_raw = j.get("schedule", "?")
        schedule = sched_raw.get("display", str(sched_raw)) if isinstance(sched_raw, dict) else str(sched_raw)
        provider = j.get("provider", "default")
        model = j.get("model", "default")
        mode = "Script" if j.get("no_agent") else "LLM"
        skills = ", ".join(j.get("skills", []))
        deliver = j.get("deliver", "origin")
        last_run = j.get("last_run_at", "Never")
        prompt_snip = (j.get("prompt", "") or "")[:100]
        lines.append(
            f"{status} [{jid}] {name} | {schedule} | {mode} | "
            f"prov:{provider} | model:{model} | deliver:{deliver} | "
            f"skills:[{skills}] | last:{last_run} | prompt:{prompt_snip}"
        )
    return "\n".join(lines)


def load_memory():
    """Load MEMORY.md content."""
    try:
        if MEMORY_PATH.exists():
            content = MEMORY_PATH.read_text()
            # Truncate to fit context
            if len(content) > 4000:
                content = content[:4000] + "\n...(truncated)"
            return content
    except Exception:
        pass
    return "(no memory)"


def load_user_profile():
    """Load USER.md content."""
    try:
        if USER_PATH.exists():
            content = USER_PATH.read_text()
            if len(content) > 2000:
                content = content[:2000]
            return content
    except Exception:
        pass
    return "(no user profile)"


def list_skills():
    """List all available skills."""
    try:
        if SKILLS_DIR.exists():
            skills = []
            for item in sorted(SKILLS_DIR.iterdir()):
                if item.is_dir():
                    skill_md = item / "SKILL.md"
                    if skill_md.exists():
                        # Read first few lines for description
                        lines = skill_md.read_text()[:300].split("\n")
                        desc = ""
                        for line in lines:
                            if line.strip() and not line.startswith("#") and not line.startswith("---"):
                                desc = line.strip()[:100]
                                break
                        skills.append(f"  - {item.name}: {desc}")
            return "\n".join(skills[:50]) if skills else "(no skills)"
    except Exception:
        pass
    return "(error loading skills)"


def get_server_status():
    """Get voice server status."""
    try:
        import psutil
        cpu = psutil.cpu_percent(interval=0.1)
        mem = psutil.virtual_memory()
        disk = psutil.disk_usage("/")
        return (
            f"CPU: {cpu}%, RAM: {mem.percent}% ({mem.available//1024//1024}MB free), "
            f"Disk: {disk.percent}% ({disk.free//1024//1024//1024}GB free)"
        )
    except ImportError:
        # Fallback without psutil
        try:
            result = subprocess.run(
                ["free", "-m"], capture_output=True, text=True, timeout=5
            )
            lines = result.stdout.strip().split("\n")
            if len(lines) >= 2:
                parts = lines[1].split()
                total = int(parts[1])
                available = int(parts[6]) if len(parts) > 6 else int(parts[3])
                return f"RAM: {total}MB total, {available}MB available"
        except Exception:
            pass
        return "(status unavailable)"


def build_system_prompt():
    """Build comprehensive system prompt with ALL capabilities."""
    now = get_wib_now()
    cron_summary = list_all_cronjobs_summary()
    memory = load_memory()
    user_profile = load_user_profile()
    skills = list_skills()
    server_status = get_server_status()

    return f"""Kamu adalah ZKA (Zero Knowledge Agent) — AI assistant suara yang bisa mengelola SEMUA sistem Hermes via suara.

WAKTU SEKARANG: {now}

=== PROFIL USER ===
{user_profile}

=== STATUS SERVER ===
{server_status}

=== SEMUA CRONJOB ===
{cron_summary}

=== MEMORY (pengetahuan sistem) ===
{memory}

=== SKILLS TERSEDIA ===
{skills}

=== KEMAMPUAN KAMU ===

Kamu BISA melakukan SEMUA hal ini. Jika user minta sesuatu, LANGSUNG eksekusi dengan mengembalikan JSON tool call.

TOOL CALL FORMAT — kembalikan JSON di dalam ```tool blok:
```tool
{{"tool": "tool_name", "args": {{...}}}}
```

TOOLS YANG TERSEDIA:

1. **cronjob_list** — List semua cronjob
   {{"tool": "cronjob_list", "args": {{}}}}

2. **cronjob_detail** — Detail lengkap 1 cronjob
   {{"tool": "cronjob_detail", "args": {{"name": "nama atau id job"}}}}

3. **cronjob_create** — Buat cronjob baru
   {{"tool": "cronjob_create", "args": {{"name": "nama", "schedule": "30m", "prompt": "isi prompt", "deliver": "telegram:-1003744814454:36"}}}}
   - schedule: "30m", "every 2h", "0 9 * * *", ISO timestamp
   - deliver: "origin", "local", "telegram:-1003744814454:39", dll

4. **cronjob_edit** — Edit cronjob yang sudah ada
   {{"tool": "cronjob_edit", "args": {{"name": "nama atau id", "field": "schedule|prompt|deliver|skills|enabled", "value": "nilai baru"}}}}

5. **cronjob_pause** — Pause cronjob
   {{"tool": "cronjob_pause", "args": {{"name": "nama atau id"}}}}

6. **cronjob_resume** — Resume cronjob
   {{"tool": "cronjob_resume", "args": {{"name": "nama atau id"}}}}

7. **cronjob_run** — Jalankan cronjob sekarang
   {{"tool": "cronjob_run", "args": {{"name": "nama atau id"}}}}

8. **cronjob_delete** — Hapus cronjob (HANYA kalau user minta!)
   {{"tool": "cronjob_delete", "args": {{"name": "nama atau id"}}}}

9. **memory_read** — Baca memory
   {{"tool": "memory_read", "args": {{}}}}

10. **memory_write** — Tulis/add memory baru
    {{"tool": "memory_write", "args": {{"content": "isi memory baru"}}}}

11. **send_message** — Kirim pesan ke topic Telegram
    {{"tool": "send_message", "args": {{"target": "telegram:-1003744814454:39", "message": "isi pesan"}}}}
    Topics: 1=Main, 36=Laporan, 37=Research, 38=Background, 39=Bounty, 40=News, 41=Reminder, 42=Poker, 43=Scraping, 813=Kuliah, 1116=Github, 3554=Twitter, 3828=Voice

12. **terminal** — Jalankan perintah terminal
    {{"tool": "terminal", "args": {{"command": "ls -la"}}}}

13. **file_read** — Baca file
    {{"tool": "file_read", "args": {{"path": "/root/.hermes/cron/jobs.json"}}}}

14. **skill_list** — List semua skills
    {{"tool": "skill_list", "args": {{}}}}

15. **skill_view** — Lihat isi skill
    {{"tool": "skill_view", "args": {{"name": "nama-skill"}}}}

16. **server_status** — Status server (CPU, RAM, disk)
    {{"tool": "server_status", "args": {{}}}}

17. **github_repos** — List repos GitHub
    {{"tool": "github_repos", "args": {{}}}}

18. **hermes_status** — Status gateway Hermes
    {{"tool": "hermes_status", "args": {{}}}}

19. **session_search** — Cari di session history
    {{"tool": "session_search", "args": {{"query": "kata kunci"}}}}

=== ATURAN ===

1. BAHASA: Selalu bahasa Indonesia, informal/slang (loe, gue, dong, sih)
2. JAWABAN: Singkat dan padat untuk voice (maks 3-4 kalimat)
3. TOOL CALL: Kalau user minta aksi (edit, create, jalankan, cek, dll), LANGSUNG kembalikan tool call
4. KONFIRMASI: Untuk DELETE, WAJIB konfirmasi dulu sebelum execute
5. DETAIL: Kalau user tanya detail, berikan info LENGKAP (model, provider, schedule, skills, dll)
6. NATURAL: Untuk pertanyaan biasa (bukan command), jawab natural tanpa tool call
7. ERROR: Kalau tool gagal, jelaskan kenapa dan sarankan alternatif

=== CONTOH ===

User: "cronjob apa aja"
→ Tool: cronjob_list
→ Response: "Ada 19 cronjob, 16 aktif 3 paused. Yang aktif: Mega Money Printer, Auto News, Bounty Hunter, dll..."

User: "model apa auto news"
→ Tool: cronjob_detail (name="Auto News")
→ Response: "Auto News pake model minimax-m2.7 via GC1, schedule tiap 6 jam, deliver ke NEWS topic thread 40"

User: "pause bounty hunter"
→ Tool: cronjob_pause (name="bounty")
→ Response: "Oke, bounty hunter gue pause ya"

User: "gimana kabarnya"
→ (no tool call, natural response)
→ Response: "Gue baik-baik aja dong! Server jalan normal, semua cronjob aktif..."

User: "update schedule money printer jadi 2 jam"
→ Tool: cronjob_edit (name="Money Printer", field="schedule", value="every 2h")
→ Response: "Done! Money Printer sekarang jalan tiap 2 jam"
"""


def find_job_by_name_or_id(query):
    """Find a cronjob by partial name match or ID."""
    jobs = load_cronjobs()
    query_lower = query.lower().strip()
    
    # Exact ID match
    for j in jobs:
        if j.get("id", "").lower() == query_lower:
            return j
    
    # Partial ID match
    for j in jobs:
        if j.get("id", "").lower().startswith(query_lower):
            return j
    
    # Exact name match
    for j in jobs:
        if j.get("name", "").lower() == query_lower:
            return j
    
    # Partial name match
    for j in jobs:
        if query_lower in j.get("name", "").lower():
            return j
    
    return None


def execute_tool(tool_name, args):
    """Execute a tool and return the result as text."""
    # Ensure args is a dict (LLM might return string)
    if isinstance(args, str):
        try:
            args = json.loads(args)
        except (json.JSONDecodeError, TypeError):
            args = {}
    if not isinstance(args, dict):
        args = {}
    
    if tool_name == "cronjob_list":
        jobs = load_cronjobs()
        active = [j for j in jobs if j.get("enabled", True)]
        paused = [j for j in jobs if not j.get("enabled", True)]
        lines = [f"Total: {len(jobs)} cronjob ({len(active)} aktif, {len(paused)} paused)\n"]
        for j in jobs:
            status = "✅" if j.get("enabled", True) else "⏸️"
            name = j.get("name", "?")
            sched_raw = j.get("schedule", "?")
            schedule = sched_raw.get("display", str(sched_raw)) if isinstance(sched_raw, dict) else str(sched_raw)
            provider = j.get("provider", "default")
            model = j.get("model", "default")
            mode = "Script" if j.get("no_agent") else "LLM"
            lines.append(f"{status} {name} | {schedule} | {mode} | {provider}/{model}")
        return "\n".join(lines)
    
    elif tool_name == "cronjob_detail":
        job = find_job_by_name_or_id(args.get("name", ""))
        if not job:
            return f"Cronjob '{args.get('name')}' tidak ditemukan"
        return format_cronjob_detail(job)
    
    elif tool_name == "cronjob_create":
        name = args.get("name", "")
        schedule = args.get("schedule", "30m")
        prompt = args.get("prompt", "")
        deliver = args.get("deliver", "origin")
        if not name or not prompt:
            return "Error: name dan prompt wajib diisi"
        # Use hermes cron create
        cmd = [
            "hermes", "cron", "create",
            "--name", name,
            "--schedule", schedule,
            "--prompt", prompt,
            "--deliver", deliver,
        ]
        try:
            result = subprocess.run(cmd, capture_output=True, text=True, timeout=30)
            if result.returncode == 0:
                return f"✅ Cronjob '{name}' berhasil dibuat!\n{result.stdout.strip()}"
            return f"❌ Gagal: {result.stderr.strip()}"
        except Exception as e:
            return f"❌ Error: {e}"
    
    elif tool_name == "cronjob_edit":
        job_name = args.get("name", "")
        field = args.get("field", "")
        value = args.get("value", "")
        job = find_job_by_name_or_id(job_name)
        if not job:
            return f"Cronjob '{job_name}' tidak ditemukan"
        job_id = job.get("id", "")
        
        # Edit via hermes CLI or direct JSON edit
        try:
            jobs = load_cronjobs()
            for j in jobs:
                if j.get("id") == job_id:
                    if field == "schedule":
                        j["schedule"] = value
                    elif field == "prompt":
                        j["prompt"] = value
                    elif field == "deliver":
                        j["deliver"] = value
                    elif field == "skills":
                        j["skills"] = [s.strip() for s in value.split(",")]
                    elif field == "enabled":
                        j["enabled"] = value.lower() in ("true", "1", "yes")
                    elif field == "provider":
                        if "model" not in j:
                            j["model"] = {}
                        j["model"]["provider"] = value
                    elif field == "model":
                        if "model" not in j:
                            j["model"] = {}
                        j["model"]["model"] = value
                    else:
                        return f"Field '{field}' tidak bisa diedit"
                    break
            
            # Write back
            CRON_PATH.write_text(json.dumps({"jobs": jobs}, indent=2))
            
            # Restart scheduler to pick up changes
            subprocess.run(["hermes", "cron", "tick"], capture_output=True, timeout=10)
            
            return f"✅ {job.get('name')}: {field} diubah ke '{value}'"
        except Exception as e:
            return f"❌ Error edit: {e}"
    
    elif tool_name == "cronjob_pause":
        job = find_job_by_name_or_id(args.get("name", ""))
        if not job:
            return f"Cronjob '{args.get('name')}' tidak ditemukan"
        job_id = job.get("id", "")
        try:
            jobs = load_cronjobs()
            for j in jobs:
                if j.get("id") == job_id:
                    j["enabled"] = False
                    break
            CRON_PATH.write_text(json.dumps({"jobs": jobs}, indent=2))
            subprocess.run(["hermes", "cron", "tick"], capture_output=True, timeout=10)
            return f"⏸️ {job.get('name')} berhasil dipause"
        except Exception as e:
            return f"❌ Error: {e}"
    
    elif tool_name == "cronjob_resume":
        job = find_job_by_name_or_id(args.get("name", ""))
        if not job:
            return f"Cronjob '{args.get('name')}' tidak ditemukan"
        job_id = job.get("id", "")
        try:
            jobs = load_cronjobs()
            for j in jobs:
                if j.get("id") == job_id:
                    j["enabled"] = True
                    break
            CRON_PATH.write_text(json.dumps({"jobs": jobs}, indent=2))
            subprocess.run(["hermes", "cron", "tick"], capture_output=True, timeout=10)
            return f"✅ {job.get('name')} berhasil diresume"
        except Exception as e:
            return f"❌ Error: {e}"
    
    elif tool_name == "cronjob_run":
        job = find_job_by_name_or_id(args.get("name", ""))
        if not job:
            return f"Cronjob '{args.get('name')}' tidak ditemukan"
        job_id = job.get("id", "")
        try:
            result = subprocess.run(
                ["hermes", "cron", "run", job_id],
                capture_output=True, text=True, timeout=30
            )
            if result.returncode == 0:
                return f"▶️ {job.get('name')} berhasil dijalankan!\n{result.stdout.strip()}"
            return f"❌ Gagal: {result.stderr.strip()}"
        except Exception as e:
            return f"❌ Error: {e}"
    
    elif tool_name == "cronjob_delete":
        job = find_job_by_name_or_id(args.get("name", ""))
        if not job:
            return f"Cronjob '{args.get('name')}' tidak ditemukan"
        job_id = job.get("id", "")
        try:
            jobs = load_cronjobs()
            jobs = [j for j in jobs if j.get("id") != job_id]
            CRON_PATH.write_text(json.dumps({"jobs": jobs}, indent=2))
            subprocess.run(["hermes", "cron", "tick"], capture_output=True, timeout=10)
            return f"🗑️ {job.get('name')} berhasil dihapus"
        except Exception as e:
            return f"❌ Error: {e}"
    
    elif tool_name == "memory_read":
        return load_memory()
    
    elif tool_name == "memory_write":
        content = args.get("content", "")
        if not content:
            return "Error: content kosong"
        try:
            existing = ""
            if MEMORY_PATH.exists():
                existing = MEMORY_PATH.read_text()
            existing += f"\n§\n{content}\n"
            MEMORY_PATH.write_text(existing)
            return f"✅ Memory ditambahkan: {content[:100]}..."
        except Exception as e:
            return f"❌ Error: {e}"
    
    elif tool_name == "send_message":
        target = args.get("target", "telegram:-1003744814454:3828")
        message = args.get("message", "")
        if not message:
            return "Error: message kosong"
        try:
            result = subprocess.run(
                ["hermes", "send", "-t", target, "-q", message],
                capture_output=True, text=True, timeout=30
            )
            if result.returncode == 0:
                return f"✅ Pesan terkirim ke {target}"
            return f"❌ Gagal: {result.stderr.strip()}"
        except Exception as e:
            return f"❌ Error: {e}"
    
    elif tool_name == "terminal":
        command = args.get("command", "")
        if not command:
            return "Error: command kosong"
        try:
            result = subprocess.run(
                command, shell=True, capture_output=True, text=True, timeout=30
            )
            output = result.stdout.strip()
            if result.stderr.strip():
                output += f"\nstderr: {result.stderr.strip()}"
            if not output:
                output = "(no output)"
            return output[:2000]
        except Exception as e:
            return f"❌ Error: {e}"
    
    elif tool_name == "file_read":
        path = args.get("path", "")
        if not path:
            return "Error: path kosong"
        try:
            content = Path(path).read_text()
            if len(content) > 3000:
                content = content[:3000] + "\n...(truncated)"
            return content
        except Exception as e:
            return f"❌ Error: {e}"
    
    elif tool_name == "skill_list":
        return list_skills()
    
    elif tool_name == "skill_view":
        name = args.get("name", "")
        skill_path = SKILLS_DIR / name / "SKILL.md"
        if not skill_path.exists():
            # Search in subdirectories
            for subdir in SKILLS_DIR.rglob("SKILL.md"):
                if name in str(subdir):
                    skill_path = subdir
                    break
        try:
            content = skill_path.read_text()
            if len(content) > 3000:
                content = content[:3000] + "\n...(truncated)"
            return content
        except Exception:
            return f"Skill '{name}' tidak ditemukan"
    
    elif tool_name == "server_status":
        return get_server_status()
    
    elif tool_name == "github_repos":
        try:
            result = subprocess.run(
                ["gh", "repo", "list", "--limit", "20", "--json", "name,visibility,updatedAt"],
                capture_output=True, text=True, timeout=15
            )
            if result.returncode == 0:
                repos = json.loads(result.stdout)
                lines = []
                for r in repos:
                    vis = "🔒" if r.get("visibility") == "PRIVATE" else "🌐"
                    lines.append(f"{vis} {r.get('name')} (updated: {r.get('updatedAt', '?')[:10]})")
                return "\n".join(lines) if lines else "(no repos)"
            return f"❌ gh error: {result.stderr.strip()}"
        except Exception as e:
            return f"❌ Error: {e}"
    
    elif tool_name == "hermes_status":
        try:
            result = subprocess.run(
                ["systemctl", "status", "hermes-gateway.service", "--no-pager"],
                capture_output=True, text=True, timeout=10
            )
            status = "Running" if result.returncode == 0 else "Stopped"
            lines = result.stdout.strip().split("\n")[:10]
            return f"Gateway: {status}\n" + "\n".join(lines)
        except Exception as e:
            return f"❌ Error: {e}"
    
    elif tool_name == "session_search":
        query = args.get("query", "")
        if not query:
            return "Error: query kosong"
        try:
            result = subprocess.run(
                ["hermes", "search", query, "--limit", "5"],
                capture_output=True, text=True, timeout=15
            )
            if result.returncode == 0:
                return result.stdout.strip()[:2000]
            return f"(no results for '{query}')"
        except Exception as e:
            return f"❌ Error: {e}"
    
    return f"Tool '{tool_name}' tidak dikenali"


def parse_tool_call(text):
    """Parse tool call from LLM response. Returns (tool_name, args) or None."""
    if not text:
        return None
    
    # Look for ```tool ... ``` block
    match = re.search(r'```tool\s*\n(.*?)\n```', text, re.DOTALL)
    if match:
        try:
            data = json.loads(match.group(1).strip())
            tool = data.get("tool")
            args = data.get("args", {})
            if isinstance(args, str):
                try:
                    args = json.loads(args)
                except (json.JSONDecodeError, TypeError):
                    args = {}
            return tool, args if isinstance(args, dict) else {}
        except json.JSONDecodeError:
            pass
    
    # Also try raw JSON tool call
    match = re.search(r'\{"tool"\s*:\s*"(\w+)"\s*,\s*"args"\s*:\s*(\{.*?\})\s*\}', text, re.DOTALL)
    if match:
        try:
            return match.group(1), json.loads(match.group(2))
        except json.JSONDecodeError:
            pass
    
    # Try more flexible pattern for args that might be empty object
    match = re.search(r'\{"tool"\s*:\s*"(\w+)"\s*,\s*"args"\s*:\s*(\{\s*\})\s*\}', text)
    if match:
        return match.group(1), {}
    
    return None


def handle_voice_command(text):
    """Handle quick voice commands (no LLM needed). Returns response or None."""
    t = text.lower().strip()
    
    # Quick status
    if t in ("status", "server status", "status server"):
        jobs = load_cronjobs()
        active = len([j for j in jobs if j.get("enabled", True)])
        paused = len([j for j in jobs if not j.get("enabled", True)])
        srv = get_server_status()
        return f"Server OK. {len(jobs)} cronjob ({active} aktif, {paused} paused). {srv}"
    
    # Quick help
    if t in ("help", "apa yang bisa", "bisa apa", "bisa ngapain"):
        return (
            "Gue bisa: cek status server, list cronjob, lihat detail, "
            "jalankan, pause, resume, edit, buat baru, hapus cronjob. "
            "Juga bisa baca memory, kirim pesan ke topic, cek GitHub, "
            "dan banyak lagi. Tanya aja!"
        )
    
    return None
