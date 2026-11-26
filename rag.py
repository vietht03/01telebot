"""RAG: tài liệu (PDF/txt/md) + dữ liệu điểm danh -> trả lời bằng Claude."""
import re
import sqlite3
import datetime as dt
from pathlib import Path

import os
from zoneinfo import ZoneInfo
from pypdf import PdfReader

TZ = ZoneInfo(os.getenv("TIMEZONE", "Asia/Ho_Chi_Minh"))

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

db.executescript("""
CREATE TABLE IF NOT EXISTS seen(key TEXT PRIMARY KEY, val TEXT);
CREATE TABLE IF NOT EXISTS activity(id INTEGER PRIMARY KEY AUTOINCREMENT, at TEXT, kind TEXT, text TEXT);
""")

db.execute("CREATE TABLE IF NOT EXISTS mana_posts(src TEXT PRIMARY KEY, day TEXT, at TEXT, rows TEXT)")

CHUNK, OVERLAP = 900, 150


# ---------- nhật ký hoạt động: mọi tin bot đã gửi, để trả lời "hôm nay bot có gửi ... lúc mấy giờ" ----------
def log_event(kind: str, text: str):
    first = re.sub(r"\s+", " ", text).strip()[:220]
    db.execute("INSERT INTO activity(at, kind, text) VALUES(?,?,?)",
               (dt.datetime.now(TZ).isoformat(timespec="seconds"), kind, first))
    db.commit()


def paused() -> bool:
    row = db.execute("SELECT val FROM seen WHERE key='paused'").fetchone()
    return bool(row and row[0] == "on")


def set_schedule(key: str, text: str):
    db.execute("INSERT OR REPLACE INTO seen VALUES(?,?)", (key, text))
    db.commit()


def schedule_text() -> str:
    rows = db.execute("SELECT val FROM seen WHERE key IN ('schedule_bot','schedule_reader') ORDER BY key").fetchall()
    return "\n".join(r[0] for r in rows) or "(chưa có lịch)"


# ---------- lịch sử Mana theo thời điểm: mỗi bài trong nhóm là 1 mốc, trạng thái = gộp các bài đến mốc đó ----------
_RANK = {"missing": 0, "absent": 1, "ok": 2}


def record_mana_post(src: str, day: str, at: dt.datetime, rows):
    import json
    db.execute("INSERT OR REPLACE INTO mana_posts VALUES(?,?,?,?)",
               (src, day, at.astimezone(TZ).isoformat(timespec="seconds"), json.dumps(rows, ensure_ascii=False)))
    db.commit()


def mana_snapshots(day: str):
    """[(HH:MM, {mã: (trạng thái, ghi chú)})] sau từng bài trong ngày, theo thứ tự thời gian."""
    import json
    state, out = {}, []
    for at, rows in db.execute("SELECT at, rows FROM mana_posts WHERE day=? ORDER BY at", (day,)).fetchall():
        for code, st, note in json.loads(rows):
            old = state.get(code)
            if old is None or _RANK[st] > _RANK[old[0]] or (st == old[0] and note and note != old[1]):
                state[code] = (st, note)
        out.append((dt.datetime.fromisoformat(at).strftime("%H:%M"), dict(state)))
    return out


def _fmt_state(state) -> str:
    miss = [c for c, (st, _) in state.items() if st == "missing"]
    absent = [f"{c}" + (f" ({n})" if n else "") for c, (st, n) in state.items() if st == "absent"]
    ok = sum(1 for st, _ in state.values() if st == "ok")
    return (f"đã có icon {ok}/{len(state)}; CHƯA điểm danh: {', '.join(miss) or 'không ai'}"
            + (f"; báo vắng: {', '.join(absent)}" if absent else ""))


def _question_days(q: str):
    today = dt.datetime.now(TZ).date()
    days = []
    for d, m in re.findall(r"(\d{1,2})\s*[/.\-]\s*(\d{1,2})(?!\d)", q):
        try:
            days.append(dt.date(today.year, int(m), int(d)))
        except ValueError:
            pass
    low = q.lower()
    if "hôm qua" in low or "hom qua" in low:
        days.append(today - dt.timedelta(days=1))
    if "hôm kia" in low or "hom kia" in low:
        days.append(today - dt.timedelta(days=2))
    if "hôm nay" in low or "hom nay" in low:
        days.append(today)
    return list(dict.fromkeys(days)) or [today - dt.timedelta(days=i) for i in range(3)]


def mana_history_text(q: str) -> str:
    """Lịch sử Mana của các ngày được hỏi, kèm trạng thái chính xác tại giờ được hỏi (vd 17h30)."""
    tm = re.search(r"(\d{1,2})\s*(?:h|g|:)\s*(\d{2})?", q.lower())
    ask_at = f"{int(tm.group(1)):02d}:{int(tm.group(2) or 0):02d}" if tm and int(tm.group(1)) < 24 else None
    out = []
    for day in _question_days(q):
        d = day.isoformat()
        snaps = mana_snapshots(d)
        if not snaps:
            out.append(f"Mana {day:%d/%m/%Y}: không có bài nào được ghi nhận.")
            continue
        out.append(f"Mana {day:%d/%m/%Y} — các mốc theo bài đăng trong nhóm:")
        out += [f"  {t}: {_fmt_state(st)}" for t, st in snaps]
        if ask_at:
            before = [x for x in snaps if x[0] <= ask_at]
            out.append(f"  => Tại {ask_at}: " + (f"{_fmt_state(before[-1][1])} (theo bài mới nhất lúc {before[-1][0]})"
                       if before else f"chưa có bài nào trong nhóm (bài đầu tiên lúc {snaps[0][0]})"))
    return "\n".join(out)


_WD = ["Thứ 2", "Thứ 3", "Thứ 4", "Thứ 5", "Thứ 6", "Thứ 7", "Chủ nhật"]


def activity_summary(days: int = 14, limit: int = 250) -> str:
    since = (dt.datetime.now(TZ) - dt.timedelta(days=days)).isoformat()
    rows = db.execute("SELECT at, kind, text FROM activity WHERE at>=? ORDER BY id DESC LIMIT ?",
                      (since, limit)).fetchall()[::-1]
    if not rows:
        return "(nhật ký trống)"
    out = []
    for at, kind, text in rows:
        t = dt.datetime.fromisoformat(at)
        out.append(f"{t:%Y-%m-%d} ({_WD[t.weekday()]}) {t:%H:%M} [{kind}] {text}")
    return "\n".join(out)


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
    "hãy nói rõ là không có, đừng bịa. Khi trích nội quy, nêu tên file nguồn.\n"
    "NHẬT KÝ GỬI TIN liệt kê mọi tin bot đã gửi (kind: 'gửi bạn' = bot nhắn cho admin, 'chuyển cho chị' = tin "
    "đã chuyển cho chị, 'hệ thống' = khởi động / tạm dừng / bật lại). Khi được hỏi hôm nay/hôm qua bot có gửi "
    "điểm danh hay báo cáo không và lúc mấy giờ, hãy đối chiếu BÂY GIỜ, LỊCH TỰ ĐỘNG và NHẬT KÝ: nêu giờ đã gửi; "
    "nếu giờ hẹn đã qua mà không có trong nhật ký thì nói rõ là KHÔNG thấy bot gửi và gợi ý nguyên nhân từ nhật ký "
    "(bot khởi động lại, tạm dừng bằng /tat...); nếu chưa tới giờ hẹn thì nói là chưa tới giờ.\n"
    "LỊCH SỬ MANA cho biết trạng thái từng ngày theo từng bài đăng trong nhóm. Khi hỏi ai chưa điểm danh/báo cáo Mana "
    "vào một ngày hoặc giờ cụ thể, dùng dòng '=> Tại HH:MM' (nếu có), liệt kê tên các mã trong 'CHƯA điểm danh' và nêu giờ của bài làm căn cứ."
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
    now = dt.datetime.now(TZ)
    user = (f"<bay_gio>{now:%Y-%m-%d %H:%M} ({_WD[now.weekday()]}), múi giờ Việt Nam. "
            f"Chế độ tạm dừng: {'ĐANG TẮT (/tat)' if paused() else 'bình thường'}</bay_gio>\n\n"
            f"<lich_tu_dong>\n{schedule_text()}\n</lich_tu_dong>\n\n"
            f"<nhat_ky_gui_tin>\n{activity_summary()}\n</nhat_ky_gui_tin>\n\n"
            f"<lich_su_mana>\n{mana_history_text(question)}\n</lich_su_mana>\n\n"
            f"<du_lieu_diem_danh>\n{attendance_summary()}\n</du_lieu_diem_danh>\n\n"
            f"<tai_lieu>\n{docs}\n</tai_lieu>\n\nCâu hỏi: {question}")
    gkey = os.getenv("GEMINI_API_KEY", "")
    if gkey and not gkey.startswith("dán"):
        return await _ask_gemini(user)
    return await _ask_claude(user)
