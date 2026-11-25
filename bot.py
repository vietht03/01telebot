"""Bot chỉ chat RIÊNG với admin: nhắc giờ điểm danh, đối chiếu danh sách, hỏi đáp RAG."""
import logging, os, re, sqlite3, unicodedata, datetime as dt
from zoneinfo import ZoneInfo
from dotenv import load_dotenv
from telegram import Update
from telegram.ext import (Application, ChatMemberHandler, CommandHandler, ContextTypes,
                          MessageHandler, filters)
import rag

load_dotenv()
logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
logging.getLogger("httpx").setLevel(logging.WARNING)
TOKEN = os.environ["BOT_TOKEN"]
ADMIN_ID = int(os.getenv("ADMIN_ID", 0))
TIMES = [t.strip() for t in os.getenv("CHECKIN_TIMES", "08:00").split(",") if t.strip()]
DEADLINE = int(os.getenv("DEADLINE_MINUTES", 15))
DAYS = tuple(int(d) for d in os.getenv("DAYS", "0,1,2,3,4").split(","))
TZ = ZoneInfo(os.getenv("TIMEZONE", "Asia/Ho_Chi_Minh"))
GROUP_ID = int(os.getenv("GROUP_ID", 0) or 0)
KT_TIME = os.getenv("KT_CHECK_TIME", "23:30")
KT_DAYS = tuple(int(d) for d in os.getenv("KT_DAYS", "0,1,2,3,4,5,6").split(","))
CN_GD_TIME = os.getenv("CN_GIAODUC_TIME", "09:30")
CN_DL_TIME = os.getenv("CN_DANGLE_TIME", "11:00")
THU5_TIME = os.getenv("THU5_TIME", "08:00")
THU4_TIME = os.getenv("THU4_TIME", "11:00")
THU7_TIME = os.getenv("THU7_TIME", "20:30")
THU3_TIME = os.getenv("THU3_TIME", "08:00")
MANA_TIME = os.getenv("MANA_CHECK_TIME", "17:30")
MANA_DAYS = tuple(int(d) for d in os.getenv("MANA_DAYS", "0,1,2,3,4,5,6").split(","))
TEMPLATE_FILE = "template.txt"

db = rag.db  # dùng chung 1 database (members / sessions / checkins / chunks)
db.execute("CREATE TABLE IF NOT EXISTS mana_marks(day TEXT, code TEXT, status TEXT, note TEXT, PRIMARY KEY(day, code))")
db.execute("CREATE TABLE IF NOT EXISTS kt_marks(week TEXT, code TEXT, slots TEXT, PRIMARY KEY(week, code))")
db.execute("CREATE TABLE IF NOT EXISTS outbox(id INTEGER PRIMARY KEY AUTOINCREMENT, text TEXT, created REAL, status TEXT DEFAULT 'new')")
db.execute("CREATE TABLE IF NOT EXISTS seen(key TEXT PRIMARY KEY, val TEXT)")
db.execute("CREATE TABLE IF NOT EXISTS kt(day TEXT PRIMARY KEY, text TEXT, saved_at TEXT)")
db.execute("CREATE TABLE IF NOT EXISTS mana(day TEXT PRIMARY KEY, text TEXT, saved_at TEXT)")


async def send_admin(bot, text: str):
    """Gửi tin cho admin, tự thử lại khi mạng chập chờn."""
    import asyncio
    from telegram.error import NetworkError, TimedOut
    for i in range(5):
        try:
            return await bot.send_message(ADMIN_ID, text)
        except (TimedOut, NetworkError):
            logging.warning("Gửi tin cho admin lỗi mạng, thử lại lần %d", i + 1)
            await asyncio.sleep(5 * (i + 1))
    logging.error("Không gửi được tin cho admin sau 5 lần thử")


def relay_on() -> bool:
    row = db.execute("SELECT val FROM seen WHERE key='relay'").fetchone()
    return not row or row[0] != "off"


def relay(text: str):
    """Xếp tin vào hàng đợi để tài khoản Telegram của bạn (chương trình reader) gửi tiếp cho người nhận."""
    if relay_on():
        import time
        db.execute("INSERT INTO outbox(text, created) VALUES(?,?)", (text, time.time()))
        db.commit()


def get_template() -> str:
    if os.path.exists(TEMPLATE_FILE):
        return open(TEMPLATE_FILE, encoding="utf-8").read().strip()
    return "Điểm danh ngày {date}"


def norm(s: str) -> str:
    s = unicodedata.normalize("NFD", s.replace("đ", "d").replace("Đ", "D"))
    s = "".join(c for c in s if unicodedata.category(c) != "Mn").lower()
    return " " + re.sub(r"[^a-z0-9]+", " ", s).strip() + " "


def admin_only(fn):
    async def w(update: Update, ctx):
        if update.effective_user.id != ADMIN_ID:
            return
        return await fn(update, ctx)
    return w


def body(update: Update) -> str:
    parts = (update.message.text or "").split(None, 1)
    return parts[1].strip() if len(parts) > 1 else ""


def roster():
    return db.execute("SELECT user_id, name FROM members ORDER BY user_id").fetchall()


def today_session(create=True):
    day = dt.datetime.now(TZ).date().isoformat()
    row = db.execute("SELECT id FROM sessions WHERE day=? ORDER BY id DESC LIMIT 1", (day,)).fetchone()
    if row or not create:
        return row[0] if row else None
    cur = db.execute("INSERT INTO sessions(day,slot,opened_at) VALUES(?,?,?)",
                     (day, "thủ công", dt.datetime.now(TZ).isoformat()))
    db.commit()
    return cur.lastrowid


# ---------- bật/tắt tự động trả lời "đang bận" ----------
@admin_only
async def tudong(update: Update, ctx):
    arg = ctx.args[0].lower() if ctx.args else ""
    if arg in ("on", "off"):
        db.execute("INSERT OR REPLACE INTO seen VALUES('autoreply',?)", (arg,))
        db.commit()
    row = db.execute("SELECT val FROM seen WHERE key='autoreply'").fetchone()
    state = "TẮT" if row and row[0] == "off" else "BẬT"
    await update.message.reply_text(f"Tự động trả lời \"đang bận\": {state}.\nDùng /tudong on hoặc /tudong off.")


@admin_only
async def chuyen(update: Update, ctx):
    arg = ctx.args[0].lower() if ctx.args else ""
    if arg in ("on", "off"):
        db.execute("INSERT OR REPLACE INTO seen VALUES('relay',?)", (arg,))
        db.commit()
    state = "BẬT" if relay_on() else "TẮT"
    await update.message.reply_text(f"Tự động gửi các báo cáo cho chị: {state}.\nDùng /chuyen on hoặc /chuyen off.")


# ---------- tiện ích ----------
async def myid(update: Update, ctx):
    await update.message.reply_text(f"User ID: {update.effective_user.id}")


@admin_only
async def start(update: Update, ctx):
    await update.message.reply_text(
        "Các lệnh:\n"
        "/lop <họ tên, mỗi người 1 dòng> - đặt danh sách lớp\n"
        "/danhsach - xem danh sách lớp\n"
        "/mau <nội dung> - đặt mẫu tin điểm danh ({date} = ngày)\n"
        "/dd - dán danh sách người đã điểm danh để đối chiếu\n"
        "/test - nhắc điểm danh thử ngay\n"
        "/hoi <câu hỏi> - hỏi RAG (hoặc nhắn thẳng)\n"
        "Gửi file PDF/txt/md để nạp nội quy.")


# ---------- danh sách lớp ----------
@admin_only
async def lop(update: Update, ctx):
    names = [n.strip(" \t-•*0123456789.)") .strip() for n in body(update).splitlines()]
    names = [n for n in names if n]
    if not names:
        return await update.message.reply_text("Cú pháp: /lop rồi xuống dòng, mỗi dòng 1 họ tên.")
    old = {n: u for u, n in roster()}
    nxt = max(old.values(), default=0) + 1
    db.execute("DELETE FROM members")
    for n in names:
        uid = old.get(n)
        if uid is None:
            uid, nxt = nxt, nxt + 1
        db.execute("INSERT INTO members VALUES(?,?)", (uid, n))
    db.commit()
    await update.message.reply_text(f"Đã lưu {len(names)} người.")


@admin_only
async def danhsach(update: Update, ctx):
    r = roster()
    await update.message.reply_text(
        "\n".join(f"{i}. {n}" for i, (_, n) in enumerate(r, 1)) if r else "Chưa có danh sách. Dùng /lop.")


@admin_only
async def mau(update: Update, ctx):
    t = body(update)
    if not t:
        return await update.message.reply_text(f"Mẫu hiện tại:\n{get_template()}\n\nĐặt mẫu mới: /mau <nội dung>")
    open(TEMPLATE_FILE, "w", encoding="utf-8").write(t)
    await update.message.reply_text("Đã lưu mẫu.")


# ---------- nhắc giờ ----------
async def send_reminder(ctx: ContextTypes.DEFAULT_TYPE, slot: str):
    now = dt.datetime.now(TZ)
    cur = db.execute("INSERT INTO sessions(day,slot,opened_at) VALUES(?,?,?)",
                     (now.date().isoformat(), slot, now.isoformat()))
    db.commit()
    text = get_template().replace("{date}", now.strftime("%d/%m/%Y"))
    await send_admin(ctx.bot, f"⏰ Đến giờ điểm danh ({slot}). Mẫu tin để bạn gửi vào nhóm:")
    await send_admin(ctx.bot, text)
    ctx.job_queue.run_once(nudge, DEADLINE * 60, data=cur.lastrowid)


async def reminder_job(ctx):
    if dt.datetime.now(TZ).weekday() in DAYS:
        await send_reminder(ctx, ctx.job.data)


async def nudge(ctx):
    sid = ctx.job.data
    if not db.execute("SELECT 1 FROM checkins WHERE session_id=?", (sid,)).fetchone():
        await send_admin(ctx.bot, "📝 Bạn chưa đối chiếu danh sách điểm danh. Gõ /dd rồi dán danh sách vào nhé.")


@admin_only
async def test(update: Update, ctx):
    await send_reminder(ctx, "thủ công")


# ---------- đối chiếu ----------
@admin_only
async def dd(update: Update, ctx):
    t = body(update)
    if not t:
        ctx.user_data["await_list"] = True
        return await update.message.reply_text("Dán danh sách người đã điểm danh vào tin nhắn kế tiếp.")
    await compare(update, t)


async def compare(update: Update, text: str):
    rep = process_paste(text)
    if rep:
        return await update.message.reply_text(rep)
    r = roster()
    if not r:
        return await update.message.reply_text("Chưa có danh sách lớp. Dùng /lop trước.")
    nt = norm(text)
    given = {}
    for _, n in r:
        g = norm(n).split()[-1]
        given[g] = given.get(g, 0) + 1
    present, guess = [], []
    for uid, n in r:
        full = norm(n)
        if full in nt:
            present.append((uid, n))
        elif given[full.split()[-1]] == 1 and f" {full.split()[-1]} " in nt:
            present.append((uid, n)); guess.append(n)  # khớp theo tên gọi (duy nhất trong lớp)
    sid = today_session()
    at = dt.datetime.now(TZ).isoformat()
    db.execute("DELETE FROM checkins WHERE session_id=?", (sid,))
    for uid, _ in present:
        db.execute("INSERT OR IGNORE INTO checkins VALUES(?,?,?)", (sid, uid, at))
    db.commit()
    ids = {u for u, _ in present}
    miss = [n for u, n in r if u not in ids]
    msg = f"✅ Đã điểm danh: {len(present)}/{len(r)}\n"
    msg += ("⚠️ CHƯA điểm danh:\n" + "\n".join(f"• {n}" for n in miss)) if miss else "Tất cả đã điểm danh 🎉"
    if guess:
        msg += "\n\nℹ️ Khớp theo tên gọi (kiểm tra lại): " + ", ".join(guess)
    await update.message.reply_text(msg)


# ---------- Mana mỗi ngày: mỗi người 1 icon sau dấu "/" ----------
RANK = {"missing": 0, "absent": 1, "ok": 2}


def parse_mana(text: str):
    """-> [(mã, trạng thái, ghi chú)], trạng thái: ok | absent (❌ báo vắng) | missing (không có icon)."""
    out = []
    for line in text.splitlines():
        m = ROW.match(line)
        if not m:
            continue
        rest = m.group(2).strip()
        if not rest or set(rest) <= EMPTY:
            out.append((m.group(1), "missing", ""))
        elif rest.startswith("❌"):
            out.append((m.group(1), "absent", rest[1:].strip(" -")))
        else:
            late = "tre" in norm(rest)
            out.append((m.group(1), "ok", rest if late else ""))
    return out


def is_mana(text: str) -> bool:
    return "mana" in norm(text) and len(parse_mana(text)) >= 3


def mana_day(text: str) -> dt.date:
    today = dt.datetime.now(TZ).date()
    m = re.search(r"\[(\d{1,2})\.(\d{1,2})\]", text)
    if m:
        try:
            return dt.date(today.year, int(m.group(2)), int(m.group(1)))
        except ValueError:
            pass
    return today


def mana_rows(day: dt.date):
    return db.execute("SELECT code, status, note FROM mana_marks WHERE day=? ORDER BY rowid",
                      (day.isoformat(),)).fetchall()


def mana_doc(day: dt.date) -> str:
    lines = [f"Báo cáo Mana ngày {day:%d/%m/%Y}"]
    for code, st, note in mana_rows(day):
        label = {"ok": "đã điểm danh", "absent": "báo vắng", "missing": "CHƯA điểm danh"}[st]
        lines.append(f"{code}: {label}" + (f" ({note})" if note else ""))
    return "\n".join(lines)


def ingest_mana(text: str, day: dt.date = None) -> dt.date:
    """Gộp bài Mana vào dữ liệu trong ngày: có icon ở BẤT KỲ bản nào là tính đã điểm danh.
    Ngày = ngày bài được ĐĂNG (không dùng tiêu đề [dd.mm] vì nhóm hay quên đổi tiêu đề)."""
    day = day or dt.datetime.now(TZ).date()
    d = day.isoformat()
    for code, st, note in parse_mana(text):
        old = db.execute("SELECT status, note FROM mana_marks WHERE day=? AND code=?", (d, code)).fetchone()
        if old is None or RANK[st] > RANK[old[0]] or (st == old[0] and note and note != old[1]):
            db.execute("INSERT OR REPLACE INTO mana_marks VALUES(?,?,?,?)", (d, code, st, note))
    db.execute("INSERT OR REPLACE INTO seen VALUES(?,?)", ("mana:" + d, dt.datetime.now(TZ).strftime("%H:%M")))
    db.commit()
    os.makedirs("docs", exist_ok=True)
    p = f"docs/mana_{d}.txt"
    open(p, "w", encoding="utf-8").write(mana_doc(day))
    rag.ingest(p)  # nạp vào RAG để hỏi sau này
    return day


def mana_summary(day: dt.date) -> str:
    rows = mana_rows(day)
    today = dt.datetime.now(TZ).date()
    seen = db.execute("SELECT val FROM seen WHERE key=?", ("mana:" + day.isoformat(),)).fetchone()
    head = f"🌿 Mana {day:%d/%m}" + (f" (cập nhật lúc {seen[0]})" if seen else "") + "\n"
    if day != today:
        head += f"⚠️ Đây là ngày {day:%d/%m}, không phải hôm nay.\n"
    miss = [c for c, st, _ in rows if st == "missing"]
    absent = [f"{c} ({n})" if n else c for c, st, n in rows if st == "absent"]
    late = [f"{c} ({n})" for c, st, n in rows if st == "ok" and n]
    done = sum(1 for _, st, _ in rows if st == "ok")
    msg = head + f"✅ Đã có icon: {done}/{len(rows)}"
    if miss:
        msg += f"\n⚠️ CHƯA điểm danh ({len(miss)}):\n" + "\n".join(f"• {c}" for c in miss)
    else:
        msg += "\n🎉 Tất cả đã điểm danh."
    if absent:
        msg += "\n\n❌ Báo vắng: " + "; ".join(absent)
    if late:
        msg += "\n\n⏱ Điểm danh trễ: " + "; ".join(late)
    return msg


def mana_line(day: dt.date) -> str:
    miss = [c for c, st, _ in mana_rows(day) if st == "missing"]
    return ("có " + ", ".join(miss) + " chưa điểm danh Mana") if miss else "Tất cả đã điểm danh Mana ✅"


def process_paste(text: str):
    """Nhận bài dán: Mana, Cảm nhận KT hoặc báo cáo tuần khác. Trả về câu trả lời hoặc None."""
    if is_mana(text):
        return mana_summary(ingest_mana(text))
    if is_kt(text):
        return kt_report(ingest_kt(text))
    rep = weekly_report(text)
    if rep:
        save_report(text)
    return rep


def mana_frame(day: dt.date) -> str:
    """Bảng kết quả Mana đã gộp: ⭕️ đã điểm danh, ❌ báo vắng (kèm lý do), ➖ chưa điểm danh."""
    lines = [f"🌈 KẾT QUẢ ĐIỂM DANH MANA [{day:%d.%m}]", ""]
    for i, (code, st, note) in enumerate(mana_rows(day), 1):
        note = note.replace("⭕️", "").replace("⭕", "").strip(" -/")
        mark = {"ok": "⭕️" + (f"-{note}" if note else ""),
                "absent": "❌" + (f"-{note}" if note else ""), "missing": "➖"}[st]
        lines.append(f"{i}. {code}/{mark}")
    lines += ["", "-Hết-"]
    return "\n".join(lines)


USERNAMES_FILE = "usernames.txt"  # mỗi dòng: 000269-TTXĐ = @abc  (file có mã thành viên nên không commit)


def load_usernames() -> dict:
    out = {}
    if os.path.exists(USERNAMES_FILE):
        for line in open(USERNAMES_FILE, encoding="utf-8"):
            if "=" in line:
                code, user = (x.strip() for x in line.split("=", 1))
                if code and user:
                    out[code.split("-")[0].strip()] = user if user.startswith("@") else "@" + user
    return out


def mention_msg(codes, phrase: str) -> str:
    """Tin để bạn gửi vào nhóm nhắc người còn thiếu: '@a @b anh chị {phrase} giúp em nha ạ'. Người chưa có @username bị bỏ qua."""
    names = load_usernames()
    users = [names[c.split("-")[0].strip()] for c in codes if c.split("-")[0].strip() in names]
    return " ".join(users) + f" anh chị {phrase} giúp em nha ạ" if users else ""


async def send_mention(bot_, codes, phrase: str):
    msg = mention_msg(codes, phrase)
    if msg:
        await send_admin(bot_, msg)
        relay(msg)  # chuyển cho chị từ tài khoản của bạn (tắt bằng /chuyen off)


async def mana_prenotice(ctx: ContextTypes.DEFAULT_TYPE):
    now = dt.datetime.now(TZ)
    if now.weekday() not in MANA_DAYS:
        return
    if not mana_rows(now.date()):  # chưa có bài trong nhóm: chỉ nhắc riêng bạn
        return await send_admin(ctx.bot, f"🔔 Sắp {MANA_TIME[:5]} mà tôi chưa thấy bài Mana hôm nay trong nhóm. "
                      "Nếu có, bạn copy bài dán vào đây giúp.")
    if any(st == "missing" for _, st, _ in mana_rows(now.date())):  # có người chưa điểm danh: báo bạn rồi chuyển chị
        line = mana_line(now.date())
        await send_admin(ctx.bot, line)
        relay(line)
        await send_mention(ctx.bot, [c for c, st, _ in mana_rows(now.date()) if st == "missing"], "cảm nhận Mana")


# ---------- báo cáo tuần (mỗi người 7 ô T2..CN) ----------
DAY_NAMES = ["Thứ 2", "Thứ 3", "Thứ 4", "Thứ 5", "Thứ 6", "Thứ 7", "CN"]
ROW = re.compile(r"^\s*\d+\s*[.)]\s*([^/\s]+)\s*/(.*)$")
EMPTY = set("—▬➖−─-–_ \u200b\ufe0f")  # các kiểu ký tự "ô trống" (nhóm dùng ➖)


def parse_report(text: str):
    rows = []
    for line in text.splitlines():
        m = ROW.match(line)
        if not m:
            continue
        slots = [x.strip() for x in m.group(2).split("/")]
        slots = (slots + [""] * 7)[:7]
        rows.append((m.group(1), [bool(x) and not set(x) <= EMPTY for x in slots]))
    return rows


def parse_report_raw(text: str):
    """-> [(mã, [7 ô])], ô trống là "" còn lại là icon NGUYÊN VĂN như trong nhóm."""
    out = []
    for line in text.splitlines():
        m = ROW.match(line)
        if m:
            slots = [x.strip() for x in m.group(2).split("/")]
            slots = (slots + [""] * 7)[:7]
            out.append((m.group(1), ["" if (not x or set(x) <= EMPTY) else x for x in slots]))
    return out


def header_start(text: str):
    """Ngày bắt đầu tuần trong tiêu đề, ví dụ S43.11.23 -> 23/11."""
    m = re.search(r"S\d+\.(\d{1,2})\.(\d{1,2})", text)
    if m:
        try:
            return dt.date(dt.datetime.now(TZ).year, int(m.group(1)), int(m.group(2)))
        except ValueError:
            pass
    return None


def week_monday() -> dt.date:
    t = dt.datetime.now(TZ).date()
    return t - dt.timedelta(days=t.weekday())


def weekly_core(rows, start):
    """Mỗi người phải có ít nhất (thứ trong tuần) icon: T2=1, T3=2, T4=3 ... CN=7."""
    today = dt.datetime.now(TZ).date()
    idx, warn = today.weekday(), ""
    if start:
        idx = (today - start).days
        if not 0 <= idx <= 6:
            warn = f"⚠️ Bài này là tuần bắt đầu {start:%d/%m}, không phải tuần hiện tại. Kết quả chỉ để tham khảo.\n\n"
            idx = min(max(idx, 0), 6)
    need = idx + 1
    bad = []
    for code, filled in rows:
        n = sum(filled)
        if n < need:
            empty = [DAY_NAMES[k] for k in range(need) if not filled[k]]
            bad.append((code, n, empty))
    head = f"{warn}📊 Tính đến {DAY_NAMES[idx]}: cần ít nhất {need} icon ({len(rows)} người)\n"
    if not bad:
        return head + "✅ Tất cả đã đủ."
    lines = [f"• {c} - có {n}/{need} icon (trống: {', '.join(e)})" for c, n, e in bad]
    return head + f"✅ Đủ: {len(rows) - len(bad)}   ⚠️ Chưa đủ: {len(bad)}\n" + "\n".join(lines)


def weekly_report(text: str):
    rows = parse_report(text)
    return weekly_core(rows, header_start(text)) if len(rows) >= 3 else None


def is_kt(text: str) -> bool:
    return "kinh thanh" in norm(text) and len(parse_report(text)) >= 3


def save_report(text: str):
    title = next((l for l in text.splitlines() if l.strip()), "baocao")
    slug = "_".join(norm(title).split())[:30] or "baocao"
    os.makedirs("docs", exist_ok=True)
    p = f"docs/{slug}_{dt.datetime.now(TZ):%Y-%m-%d}.txt"
    open(p, "w", encoding="utf-8").write("Báo cáo ngày " + dt.datetime.now(TZ).strftime("%d/%m/%Y") + "\n" + text)
    rag.ingest(p)


def kt_rows(start: dt.date):
    return [(c, [bool(x) for x in sl.split("/")]) for c, sl in db.execute(
        "SELECT code, slots FROM kt_marks WHERE week=? ORDER BY rowid", (start.isoformat(),))]


def kt_header(text: str):
    """Hai dòng đầu của khung (tiêu đề + 'Họ tên/ ...') lấy nguyên văn từ bài trong nhóm."""
    lines = [l.rstrip() for l in text.splitlines() if l.strip()]
    first = next((l for l in lines if "kinh thanh" in norm(l)), None)
    second = next((l for l in lines if norm(l).strip().startswith("ho ten")), None)
    return (first, second) if first and second else None


def ingest_kt(text: str) -> dt.date:
    """Gộp bài KT vào dữ liệu của tuần: mỗi ô giữ icon thật; ô trống ở bản mới không xóa icon đã có."""
    start = header_start(text) or week_monday()
    wk = start.isoformat()
    for code, raw in parse_report_raw(text):
        old = db.execute("SELECT slots FROM kt_marks WHERE week=? AND code=?", (wk, code)).fetchone()
        oldl = old[0].split("/") if old else [""] * 7
        merged = [n or o for n, o in zip(raw, oldl)]
        db.execute("INSERT OR REPLACE INTO kt_marks VALUES(?,?,?)", (wk, code, "/".join(merged)))
    head = kt_header(text)
    if head:
        db.execute("INSERT OR REPLACE INTO seen VALUES(?,?)", ("kt_head:" + wk, "\n".join(head)))
    db.execute("INSERT OR REPLACE INTO seen VALUES(?,?)", ("kt:" + wk, dt.datetime.now(TZ).strftime("%d/%m %H:%M")))
    db.commit()
    lines = [f"Cảm nhận Kinh Thánh tuần bắt đầu {start:%d/%m/%Y} (T2..CN, 1 = đã làm, 0 = chưa):"]
    lines += [f"{c}: " + " ".join(f"{DAY_NAMES[i]}={'1' if f else '0'}" for i, f in enumerate(fl)) for c, fl in kt_rows(start)]
    os.makedirs("docs", exist_ok=True)
    p = f"docs/kt_{wk}.txt"
    open(p, "w", encoding="utf-8").write("\n".join(lines))
    rag.ingest(p)
    return start


def kt_frame(start: dt.date) -> str:
    """Khung điểm danh KT đúng định dạng nhóm, đủ icon từng người từng ngày."""
    row = db.execute("SELECT val FROM seen WHERE key=?", ("kt_head:" + start.isoformat(),)).fetchone()
    end = start + dt.timedelta(days=6)
    head = row[0] if row else (f"🌈 CẢM NHẬN KINH THÁNH VÀ CẢM TẠ (S43.{start:%m.%d}- S43.{end:%m.%d})\n"
                              "Họ tên/ ⭕️❌ - Lý do")
    lines = [head, ""]
    for i, (code, sl) in enumerate(db.execute(
            "SELECT code, slots FROM kt_marks WHERE week=? ORDER BY rowid", (start.isoformat(),)), 1):
        lines.append(f"{i}. {code}/" + "/".join(x or "➖" for x in sl.split("/")))
    lines += ["", "-Hết-"]
    return "\n".join(lines)


def kt_missing(start: dt.date):
    today = dt.datetime.now(TZ).date()
    need = min(max((today - start).days, 0), 6) + 1
    return [c for c, f in kt_rows(start) if sum(f) < need]


def kt_summary(start: dt.date) -> str:
    miss = kt_missing(start)
    line = ("có " + ", ".join(miss) + " chưa cảm nhận KT") if miss else "Tất cả đã cảm nhận KT ✅"
    return line


def kt_report(start: dt.date) -> str:
    seen = db.execute("SELECT val FROM seen WHERE key=?", ("kt:" + start.isoformat(),)).fetchone()
    return (f"📖 Cảm nhận KT" + (f" (cập nhật lúc {seen[0]})" if seen else "") + "\n"
            + weekly_core(kt_rows(start), start))


async def kt_prenotice(ctx: ContextTypes.DEFAULT_TYPE):
    now = dt.datetime.now(TZ)
    if now.weekday() not in KT_DAYS:
        return
    start = week_monday()
    if not kt_rows(start):  # chưa có bài trong nhóm: chỉ nhắc riêng bạn
        return await send_admin(ctx.bot, f"🔔 Sắp {KT_TIME[:5]} mà tôi chưa thấy bài Cảm nhận KT tuần này trong nhóm. "
                      "Nếu có, bạn copy bài dán vào đây giúp.")
    if kt_missing(start):  # có người chưa cảm nhận: báo bạn, chuyển chị, rồi soạn sẵn tin nhắc cho bạn
        await send_admin(ctx.bot, kt_summary(start))
        relay(kt_summary(start))
        await send_mention(ctx.bot, kt_missing(start), "cảm nhận KT")


# ---------- tin nhắn cố định gửi cho bạn lúc 11:00 thứ 4 (nội dung nằm trong msg_thu4.txt, gửi y nguyên) ----------
def render_msg(path: str, day: dt.date = None) -> str:
    """Điền ngày gửi vào mẫu: {YYMMDD} -> 261125, {YY.MM.DD} -> 26.11.25. Phần còn lại giữ y nguyên."""
    day = day or dt.datetime.now(TZ).date()
    return (open(path, encoding="utf-8").read()
            .replace("{YYMMDD}", day.strftime("%y%m%d"))
            .replace("{YY.MM.DD}", day.strftime("%y.%m.%d")))


def render_thu4(day: dt.date = None) -> str:
    return render_msg("msg_thu4.txt", day)


def render_thu5(day: dt.date = None) -> str:
    return render_msg("msg_thu5.txt", day)


async def thu4_job(ctx: ContextTypes.DEFAULT_TYPE):
    if dt.datetime.now(TZ).weekday() == 2:  # thứ 4
        await send_admin(ctx.bot, render_thu4())
        relay(render_thu4())


def make_form_job(path: str, weekday: int):
    """Tạo job: đúng thứ `weekday` thì gửi form (đã điền ngày) cho admin và chuyển tiếp qua tài khoản của admin."""
    async def job(ctx: ContextTypes.DEFAULT_TYPE):
        if dt.datetime.now(TZ).weekday() == weekday:
            text = render_msg(path)
            await send_admin(ctx.bot, text)
            relay(text)
    return job


def make_form_cmd(path: str):
    @admin_only
    async def cmd(update: Update, ctx):
        await update.message.reply_text(render_msg(path))
    return cmd


async def thu5_job(ctx: ContextTypes.DEFAULT_TYPE):
    if dt.datetime.now(TZ).weekday() == 3:  # thứ 5
        await send_admin(ctx.bot, render_thu5())
        relay(render_thu5())


@admin_only
async def thu5(update: Update, ctx):
    await update.message.reply_text(render_thu5())


@admin_only
async def rasoat(update: Update, ctx):
    """Yêu cầu reader rà soát ngay bảng điểm danh hôm nay (chỉ báo cho bạn, không chuyển sang chị) (kết quả được báo riêng trong vài chục giây)."""
    db.execute("INSERT OR REPLACE INTO seen VALUES('review_req','1')")
    db.commit()
    await update.message.reply_text("Đang rà soát bảng điểm danh hôm nay trong nhóm, tôi sẽ báo kết quả ngay (chỉ gửi riêng bạn).")


@admin_only
async def thanhvien(update: Update, ctx):
    """Yêu cầu reader liệt kê thành viên nhóm (chỉ gửi riêng bạn)."""
    db.execute("INSERT OR REPLACE INTO seen VALUES('members_req','1')")
    db.commit()
    await update.message.reply_text("Đang lấy danh sách thành viên nhóm, tôi sẽ gửi ngay (chỉ riêng bạn).")


@admin_only
async def thu4(update: Update, ctx):
    await update.message.reply_text(render_thu4())


@admin_only
async def kt_cmd(update: Update, ctx):
    start = week_monday()
    if not kt_rows(start):
        return await update.message.reply_text("Chưa có dữ liệu Cảm nhận KT tuần này.")
    await update.message.reply_text(kt_frame(start))
    await update.message.reply_text(kt_summary(start))


# ---------- đọc nhóm ----------
async def on_group(update: Update, ctx):
    """Bot nằm trong nhóm: tự ghi nhận bài Mana / Cảm nhận KT (kể cả khi bài được sửa)."""
    msg = update.effective_message
    if not msg or not msg.text:
        return
    if GROUP_ID and update.effective_chat.id != GROUP_ID:
        return
    try:
        if is_mana(msg.text):
            logging.info("Nhóm: ghi nhận bài Mana %s", ingest_mana(msg.text, msg.date.astimezone(TZ).date()))
        elif is_kt(msg.text):
            logging.info("Nhóm: ghi nhận bài KT tuần %s", ingest_kt(msg.text))
    except Exception:
        logging.exception("Lỗi khi xử lý tin trong nhóm")


async def on_added(update: Update, ctx):
    """Khi bot được thêm vào nhóm: báo cho admin ID nhóm và nhắc cấu hình quyền đọc."""
    m = update.my_chat_member
    chat = m.chat
    if chat.type in ("group", "supergroup") and m.new_chat_member.status in ("member", "administrator"):
        me = await ctx.bot.get_me()
        ok = m.new_chat_member.status == "administrator" or me.can_read_all_group_messages
        note = "" if ok else ("\n⚠️ Bot CHƯA đọc được tin thường trong nhóm. Hãy đặt bot làm admin, hoặc vào "
                              "@BotFather: /setprivacy → chọn bot → Disable, rồi xóa và thêm lại bot vào nhóm.")
        logging.info("Bot được thêm vào nhóm %s id=%s", chat.title, chat.id)
        await send_admin(ctx.bot, f"Bot đã vào nhóm \"{chat.title}\" (ID {chat.id}).{note}")


# ---------- RAG ----------
async def ask(update: Update, ctx):
    if update.effective_user.id != ADMIN_ID:
        return
    txt = update.message.text or ""
    if ctx.user_data.pop("await_list", False) and not txt.startswith("/"):
        return await compare(update, txt)
    rep = None if txt.startswith("/") else process_paste(txt)
    if rep:  # dán bài Mana / báo cáo tuần -> tự đối chiếu
        return await update.message.reply_text(rep)
    q = " ".join(ctx.args) if ctx.args else (update.message.text or "")
    if not q.strip() or q.startswith("/"):
        return await update.message.reply_text("Cú pháp: /hoi <câu hỏi>")
    await ctx.bot.send_chat_action(update.effective_chat.id, "typing")
    try:
        await update.message.reply_text(await rag.answer(q))
    except Exception as e:
        await update.message.reply_text(f"Lỗi khi hỏi: {e}")


@admin_only
async def on_document(update: Update, ctx):
    doc = update.message.document
    if not doc.file_name.lower().endswith((".pdf", ".txt", ".md")):
        return await update.message.reply_text("Chỉ nhận .pdf, .txt, .md")
    os.makedirs("docs", exist_ok=True)
    path = os.path.join("docs", os.path.basename(doc.file_name))
    await (await doc.get_file()).download_to_drive(path)
    n = rag.ingest(path)
    await update.message.reply_text(f"Đã nạp {doc.file_name} ({n} đoạn). Giờ bạn có thể hỏi về nó.")


async def setup_profile(app):
    from telegram import BotCommand, InputProfilePhotoStatic
    await app.bot.set_my_commands([
        BotCommand("dd", "Đối chiếu danh sách điểm danh"),
        BotCommand("hoi", "Hỏi trợ lý (RAG)"),
        BotCommand("lop", "Đặt danh sách lớp"),
        BotCommand("danhsach", "Xem danh sách lớp"),
        BotCommand("mau", "Đặt mẫu tin điểm danh"),
        BotCommand("test", "Nhắc điểm danh thử"),
        BotCommand("thanhvien", "Liệt kê thành viên nhóm"),
        BotCommand("rasoat", "Rà soát ngay bảng điểm danh hôm nay"),
        BotCommand("thu3", "Form điểm danh sách Sáng tạo Trời Đất thứ 3"),
        BotCommand("thu7", "Form điểm danh lễ nhóm tối thứ 7"),
        BotCommand("kt", "Khung + báo cáo Cảm nhận KT"),
        BotCommand("chuyen", "Bật/tắt tự gửi báo cáo cho chị"),
        BotCommand("tudong", "Bật/tắt tự động trả lời đang bận"),
        BotCommand("start", "Xem hướng dẫn"),
    ])
    await app.bot.set_my_short_description("Trợ lý điểm danh & hỏi đáp nội quy")
    await app.bot.set_my_description("Dán báo cáo tuần để biết ai chưa điểm danh/làm bài, hoặc hỏi tôi về nội quy và dữ liệu điểm danh.")
    try:
        with open("bot_icon.png", "rb") as f:
            await app.bot.set_my_profile_photo(InputProfilePhotoStatic(photo=f.read()))
    except Exception as e:
        print("Không đặt được ảnh đại diện (hãy dùng BotFather /setuserpic):", e, flush=True)


def main():
    app = Application.builder().token(TOKEN).connect_timeout(30).read_timeout(30).write_timeout(30).pool_timeout(30).concurrent_updates(True).post_init(setup_profile).build()
    for name, fn in [("myid", myid), ("start", start), ("lop", lop), ("danhsach", danhsach),
                     ("mau", mau), ("test", test), ("tudong", tudong), ("thu4", thu4), ("rasoat", rasoat), ("thanhvien", thanhvien), ("thu5", thu5), ("thu7", make_form_cmd("msg_thu7.txt")), ("thu3", make_form_cmd("msg_thu3.txt")), ("giaoduc", make_form_cmd("msg_cn_giaoduc.txt")), ("dangle", make_form_cmd("msg_cn_dangle.txt")), ("kt", kt_cmd), ("chuyen", chuyen), ("dd", dd), ("hoi", ask)]:
        app.add_handler(CommandHandler(name, fn))
    P = filters.ChatType.PRIVATE
    app.add_handler(ChatMemberHandler(on_added, ChatMemberHandler.MY_CHAT_MEMBER))
    app.add_handler(MessageHandler(filters.ChatType.GROUPS & filters.TEXT & filters.UpdateType.MESSAGES,
                                   on_group), group=1)
    app.add_handler(MessageHandler(filters.Document.ALL & P, on_document))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND & P, ask))
    for t in TIMES:
        h, m = map(int, t.split(":"))
        app.job_queue.run_daily(reminder_job, dt.time(h, m, tzinfo=TZ), data=t)
    h, m = map(int, MANA_TIME.split(":"))
    pre = dt.datetime.combine(dt.date.today(), dt.time(h, m)) - dt.timedelta(minutes=5)
    app.job_queue.run_daily(mana_prenotice, dt.time(pre.hour, pre.minute, tzinfo=TZ))
    for path, hhmm in (("msg_cn_giaoduc.txt", CN_GD_TIME), ("msg_cn_dangle.txt", CN_DL_TIME)):
        fh, fm = map(int, hhmm.split(":"))
        app.job_queue.run_daily(make_form_job(path, 6), dt.time(fh, fm, tzinfo=TZ), name=path)  # 6 = Chủ nhật
    t3h, t3m = map(int, THU3_TIME.split(":"))
    app.job_queue.run_daily(make_form_job("msg_thu3.txt", 1), dt.time(t3h, t3m, tzinfo=TZ), name="msg_thu3.txt")  # 1 = thứ 3
    t7h, t7m = map(int, THU7_TIME.split(":"))
    app.job_queue.run_daily(make_form_job("msg_thu7.txt", 5), dt.time(t7h, t7m, tzinfo=TZ), name="msg_thu7.txt")  # 5 = thứ 7
    t5h, t5m = map(int, THU5_TIME.split(":"))
    app.job_queue.run_daily(thu5_job, dt.time(t5h, t5m, tzinfo=TZ))
    th, tm = map(int, THU4_TIME.split(":"))
    app.job_queue.run_daily(thu4_job, dt.time(th, tm, tzinfo=TZ))
    kh, km = map(int, KT_TIME.split(":"))
    kpre = dt.datetime.combine(dt.date.today(), dt.time(kh, km)) - dt.timedelta(minutes=5)
    app.job_queue.run_daily(kt_prenotice, dt.time(kpre.hour, kpre.minute, tzinfo=TZ))
    app.run_polling(allowed_updates=Update.ALL_TYPES)


if __name__ == "__main__":
    main()
