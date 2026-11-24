"""RAG: tài liệu (PDF/txt/md) + dữ liệu điểm danh -> trả lời bằng Claude."""
import re
import sqlite3
import datetime as dt
from pathlib import Path

import os
import anthropic
from pypdf import PdfReader

MODEL = "claude-opus-5-5"
GEMINI_MODEL = os.getenv("GEMINI_MODEL", "gemini-3.5-flash-lite")

db = sqlite3.connect("attendance.db", check_same_thread=False)
db.executescript("""
CREATE TABLE IF NOT EXISTS members(user_id INTEGER PRIMARY KEY, name TEXT);
CREATE TABLE IF NOT EXISTS sessions(id INTEGER PRIMARY KEY AUTOINCREMENT, day TEXT, slot TEXT, opened_at TEXT);
CREATE TABLE IF NOT EXISTS checkins(session_id INTEGER, user_id INTEGER, at TEXT, PRIMARY KEY(session_id, user_id));
CREATE VIRTUAL TABLE IF NOT EXISTS chunks USING fts5(
    source, text, tokenize='unicode61 remove_diacritics 2');
""")

CHUNK, OVERLAP = 900, 150


def _split(text: str):
    text = re.sub(r"\s+", " ", text).strip()
    i = 0
    while i < len(text):
        yield text[i:i + CHUNK]
        i += CHUNK - OVERLAP


def ingest(path: str) -> int:
    """Nạp 1 file vào kho tri thức, trả về số đoạn. Nạp lại cùng tên sẽ ghi đè."""
    p = Path(path)
    if p.suffix.lower() == ".pdf":
        text = "\n".join((pg.extract_text() or "") for pg in PdfReader(str(p)).pages)
    else:
        text = p.read_text(encoding="utf-8", errors="ignore")
    db.execute("DELETE FROM chunks WHERE source=?", (p.name,))
    n = 0
    for c in _split(text):
        db.execute("INSERT INTO chunks(source,text) VALUES(?,?)", (p.name, c))
        n += 1
    db.commit()
    return n


def search(query: str, k: int = 6):
    words = re.findall(r"\w+", query)
    if not words:
        return []
    q = " OR ".join(f'"{w}"' for w in words)
    return db.execute(
        "SELECT source, text FROM chunks WHERE chunks MATCH ? ORDER BY bm25(chunks) LIMIT ?",
        (q, k)).fetchall()


def attendance_summary(days: int = 30) -> str:
    since = (dt.date.today() - dt.timedelta(days=days)).isoformat()
    sessions = db.execute(
        "SELECT id, day, slot, opened_at FROM sessions WHERE day>=? ORDER BY id", (since,)).fetchall()
    if not sessions:
        return "(chưa có dữ liệu điểm danh)"
    members = db.execute("SELECT user_id, name FROM members").fetchall()
    lines = [f"Thành viên: {', '.join(n for _, n in members)}"]
    for sid, day, slot, opened in sessions:
        got = {u: at for u, at in db.execute(
            "SELECT user_id, at FROM checkins WHERE session_id=?", (sid,))}
        opened_dt = dt.datetime.fromisoformat(opened)
        ok = []
        for uid, name in members:
            if uid in got:
                late = (dt.datetime.fromisoformat(got[uid]) - opened_dt).total_seconds() / 60
                ok.append(f"{name} ({late:.0f}p sau khi mở)")
        miss = [n for u, n in members if u not in got]
        lines.append(f"- {day} {slot}: ĐÃ: {'; '.join(ok) or 'không ai'} | CHƯA: {', '.join(miss) or 'không ai'}")
    return "\n".join(lines)


SYSTEM = (
    "Bạn là trợ lý điểm danh của một nhóm Telegram. Trả lời bằng tiếng Việt, ngắn gọn, "
    "chỉ dựa trên DỮ LIỆU ĐIỂM DANH và TÀI LIỆU được cung cấp. Nếu thông tin không có, "
    "hãy nói rõ là không có, đừng bịa. Khi trích nội quy, nêu tên file nguồn."
)


GEMINI_FALLBACKS = ["gemini-3.5-flash", "gemini-3.1-flash-lite", "gemini-3.7-flash", "gemini-3.8-flash"]


async def _ask_gemini(user: str) -> str:
    from google import genai
    from google.genai import types, errors
    gc = genai.Client(api_key=os.environ["GEMINI_API_KEY"], http_options=types.HttpOptions(
        timeout=20000, retry_options=types.HttpRetryOptions(attempts=1)))
    cfg = types.GenerateContentConfig(system_instruction=SYSTEM, max_output_tokens=2000)
    last = None
    for m in list(dict.fromkeys([GEMINI_MODEL] + GEMINI_FALLBACKS)) * 2:  # 2 vòng
        try:
            r = await gc.aio.models.generate_content(model=m, contents=user, config=cfg)
            if r.text:
                return r.text
        except errors.ServerError as e:  # 5xx: quá tải -> thử model khác
            last = e
        except errors.ClientError as e:
            if e.code in (404, 429):  # model không còn / hết hạn mức -> thử model khác
                last = e
            else:
                raise
        except Exception as e:  # hết thời gian chờ, lỗi mạng
            last = e
    raise RuntimeError("Gemini đang quá tải, bạn thử lại sau ít phút.") from last


async def _ask_claude(user: str) -> str:
    client = anthropic.AsyncAnthropic()  # đọc ANTHROPIC_API_KEY
    resp = await client.beta.messages.create(
        model=MODEL, max_tokens=2000, system=SYSTEM,
        betas=["server-side-fallback-2026-07-01"], fallbacks="default",
        output_config={"effort": "low"},
        messages=[{"role": "user", "content": user}],
    )
    if resp.stop_reason == "refusal":
        return "Xin lỗi, tôi không thể trả lời câu hỏi này."
    return "".join(b.text for b in resp.content if b.type == "text") or "(không có câu trả lời)"


async def answer(question: str) -> str:
    hits = search(question)
    docs = "\n\n".join(f"[{s}] {t}" for s, t in hits) or "(không tìm thấy đoạn tài liệu liên quan)"
    user = (f"<du_lieu_diem_danh>\n{attendance_summary()}\n</du_lieu_diem_danh>\n\n"
            f"<tai_lieu>\n{docs}\n</tai_lieu>\n\nCâu hỏi: {question}")
    gkey = os.getenv("GEMINI_API_KEY", "")
    if gkey and not gkey.startswith("dán"):
        return await _ask_gemini(user)
    return await _ask_claude(user)
