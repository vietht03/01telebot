"""RAG: tài liệu (PDF/txt/md) + dữ liệu điểm danh -> trả lời bằng Claude."""
import re
import sqlite3
import datetime as dt
from pathlib import Path

import os
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
    """Gọi Gemini bằng HTTP thuần (httpx) để không cần cài SDK nặng, chạy được cả trên điện thoại."""
    import httpx
    key = os.environ["GEMINI_API_KEY"]
    body = {"systemInstruction": {"parts": [{"text": SYSTEM}]},
            "contents": [{"role": "user", "parts": [{"text": user}]}],
            "generationConfig": {"maxOutputTokens": 4096}}
    last = None
    async with httpx.AsyncClient(timeout=25) as h:
        for m in list(dict.fromkeys([GEMINI_MODEL] + GEMINI_FALLBACKS)) * 2:  # 2 vòng qua danh sách model
            try:
                r = await h.post(f"https://generativelanguage.googleapis.com/v1beta/models/{m}:generateContent",
                                 headers={"x-goog-api-key": key}, json=body)
            except httpx.HTTPError as e:  # hết thời gian chờ, lỗi mạng
                last = e
                continue
            if r.status_code == 200:
                cands = r.json().get("candidates") or []
                parts = (cands[0].get("content") or {}).get("parts", []) if cands else []
                text = "".join(p.get("text", "") for p in parts)
                if text:
                    return text
                last = RuntimeError(f"{m}: không có nội dung trả về")
            elif r.status_code in (404, 429) or r.status_code >= 500:  # model gỡ / hết hạn mức / quá tải -> thử model khác
                last = RuntimeError(f"{m}: HTTP {r.status_code}")
            else:
                raise RuntimeError(f"Gemini lỗi {r.status_code}: {r.text[:200]}")
    raise RuntimeError("Gemini đang quá tải, bạn thử lại sau ít phút.") from last


async def _ask_claude(user: str) -> str:
    """Gọi Claude bằng HTTP thuần (chưa kiểm thử vì chưa có ANTHROPIC_API_KEY)."""
    import httpx
    async with httpx.AsyncClient(timeout=90) as h:
        r = await h.post("https://api.anthropic.com/v1/messages",
                         headers={"x-api-key": os.environ["ANTHROPIC_API_KEY"],
                                  "anthropic-version": "2023-06-01", "content-type": "application/json"},
                         json={"model": MODEL, "max_tokens": 2000, "system": SYSTEM,
                               "messages": [{"role": "user", "content": user}]})
    if r.status_code != 200:
        raise RuntimeError(f"Claude lỗi {r.status_code}: {r.text[:200]}")
    d = r.json()
    if d.get("stop_reason") == "refusal":
        return "Xin lỗi, tôi không thể trả lời câu hỏi này."
    return "".join(b.get("text", "") for b in d.get("content", []) if b.get("type") == "text") or "(không có câu trả lời)"


async def answer(question: str) -> str:
    hits = search(question)
    docs = "\n\n".join(f"[{s}] {t}" for s, t in hits) or "(không tìm thấy đoạn tài liệu liên quan)"
    user = (f"<du_lieu_diem_danh>\n{attendance_summary()}\n</du_lieu_diem_danh>\n\n"
            f"<tai_lieu>\n{docs}\n</tai_lieu>\n\nCâu hỏi: {question}")
    gkey = os.getenv("GEMINI_API_KEY", "")
    if gkey and not gkey.startswith("dán"):
        return await _ask_gemini(user)
    return await _ask_claude(user)
