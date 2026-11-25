"""Đọc nhóm bằng tài khoản của bạn (CHỈ ĐỌC, không gửi tin). Ghi nhận bài Mana / Cảm nhận KT vào cùng database với bot."""
import asyncio, datetime as dt, logging, os, sys, time
from dotenv import load_dotenv

HERE = os.path.dirname(os.path.abspath(__file__))
os.chdir(HERE)
load_dotenv(os.path.join(HERE, ".env"))
from telethon import TelegramClient, events
import bot  # dùng lại hàm ghi nhận của bot

GROUP_ID = int(os.getenv("TG_GROUP_ID", "0"))
GROUP_TITLE = os.getenv("TG_GROUP_TITLE", "")
log = logging.getLogger("reader")

# ---- tự động trả lời "đang bận" khi có người nhắn riêng mà bạn chưa trả lời ----
AUTO_TEXT = os.getenv("AUTOREPLY_TEXT", "Xin chào, tôi đang bận.")
AUTO_DELAY = float(os.getenv("AUTOREPLY_DELAY_MIN", "10")) * 60      # chờ bao lâu rồi mới trả lời
AUTO_COOLDOWN = float(os.getenv("AUTOREPLY_COOLDOWN_H", "6")) * 3600  # mỗi người tối đa 1 lần / khoảng này
def _hm(v: str) -> dt.time:
    h, m = v.split(":")
    return dt.time(int(h), int(m))


AUTO_START = _hm(os.getenv("AUTOREPLY_START", "08:00"))   # chỉ tự trả lời trong khung giờ này
AUTO_END = _hm(os.getenv("AUTOREPLY_END", "18:00"))


def in_hours(when: dt.datetime) -> bool:
    return AUTO_START <= when.astimezone(bot.TZ).time() < AUTO_END


AUTO_BOTS = os.getenv("AUTOREPLY_ALLOW_BOTS", "0") == "1"             # chỉ dùng để thử
bot.db.execute("CREATE TABLE IF NOT EXISTS autoreply(user_id INTEGER PRIMARY KEY, last_sent REAL)")
pending = set()

# ---- chuyển các báo cáo của bot sang người nhận, gửi từ tài khoản của bạn ----
RELAY_TARGET = os.getenv("RELAY_TARGET", "")
RELAY_MAX_AGE = 3600  # tin quá 1 giờ thì bỏ, không gửi trễ
bot.db.execute("CREATE TABLE IF NOT EXISTS outbox(id INTEGER PRIMARY KEY AUTOINCREMENT, text TEXT, created REAL, status TEXT DEFAULT 'new')")


async def notify_admin(text: str):
    import httpx
    try:
        async with httpx.AsyncClient(timeout=20) as h:
            await h.post(f"https://api.telegram.org/bot{bot.TOKEN}/sendMessage",
                         data={"chat_id": bot.ADMIN_ID, "text": text})
    except Exception:
        log.exception("Không báo được cho admin")


async def outbox_loop():
    target = None
    while True:
        try:
            await asyncio.sleep(5)
            rows = bot.db.execute("SELECT id, text, created FROM outbox WHERE status='new' ORDER BY id").fetchall()
            for oid, text, created in rows:
                if time.time() - created > RELAY_MAX_AGE:
                    bot.db.execute("UPDATE outbox SET status='expired' WHERE id=?", (oid,)); bot.db.commit()
                    log.warning("Bỏ tin quá hạn id=%s", oid)
                    continue
                if target is None:
                    target = await client.get_entity(RELAY_TARGET)
                await client.send_message(target, text, parse_mode=None, link_preview=False)
                bot.db.execute("UPDATE outbox SET status='sent' WHERE id=?", (oid,)); bot.db.commit()
                first = text.strip().splitlines()[0][:60]
                log.info("Đã gửi cho %s: %s", RELAY_TARGET, first)
                await notify_admin(f"✅ Đã gửi cho chị ({RELAY_TARGET}): {first}")
                await asyncio.sleep(2)
        except Exception:
            log.exception("Lỗi khi chuyển tin")
            await asyncio.sleep(30)


def autoreply_on() -> bool:
    row = bot.db.execute("SELECT val FROM seen WHERE key='autoreply'").fetchone()
    return not row or row[0] != "off"


async def delayed_reply(chat_id, user_id, msg_date):
    try:
        await asyncio.sleep(AUTO_DELAY)
        if not autoreply_on() or not in_hours(dt.datetime.now(bot.TZ)):
            return
        row = bot.db.execute("SELECT last_sent FROM autoreply WHERE user_id=?", (user_id,)).fetchone()
        if row and time.time() - row[0] < AUTO_COOLDOWN:
            return
        recent = await client.get_messages(chat_id, limit=10)
        if any(m.out and m.date >= msg_date for m in recent):
            return  # bạn đã tự trả lời rồi
        await client.send_message(chat_id, AUTO_TEXT)
        bot.db.execute("INSERT OR REPLACE INTO autoreply VALUES(?,?)", (user_id, time.time()))
        bot.db.commit()
        log.info("Đã tự trả lời người dùng %s", user_id)
    except Exception:
        log.exception("Lỗi khi tự trả lời")
    finally:
        pending.discard(user_id)
# ---- rà soát bảng điểm danh: 21:50 thứ 4 (dâng lễ thứ 4) và 19:10 Chủ nhật (giáo dục + dâng lễ CN) ----
# Mỗi dòng: ⭕ = đã điểm danh, ❌ = hôm đó không tham gia, còn lại (trống/chưa có ⭕) = chưa điểm danh.
# Báo cho bạn ngay, rồi chuyển tiếp sang chị (RELAY_TARGET) từ tài khoản của bạn.
GD = ("diem danh giao duc truoc le chu nhat", "Giáo dục trước lễ CN")
DL_CN = ("diem danh dang le chu nhat", "Dâng lễ Chủ nhật")
LN = ("diem danh tham gia le nhom", "Tham gia lễ nhóm")
ST = ("diem danh giao duc sach sang tao troi dat", "Giáo dục sách Sáng tạo Trời Đất")
REVIEWS = [  # id, thứ (0=T2), giờ rà soát, các bảng, có báo cả người ❌ không
    # tối thứ 3 21:50: bảng kết quả + tin nhắc @username, gửi bạn rồi chuyển chị
    dict(id="t3", wd=1, at=_hm(os.getenv("THU3_REVIEW_TIME", "21:50")), tables=[ST], absent=False, frame=True, list=False),
    dict(id="t4", wd=2, at=_hm(os.getenv("THU4_REVIEW_TIME", "21:50")), tables=[("diem danh dang le thu 4", "Dâng lễ thứ 4")], absent=True),
    dict(id="cn_gd", wd=6, at=_hm(os.getenv("CN_GD_REVIEW_TIME", "10:00")), tables=[GD], absent=False),
    # tối thứ 7: 20:58 tin nhắc có @username, 21:00 bảng kết quả + ai chưa điểm danh
    dict(id="t7", wd=5, at=_hm(os.getenv("THU7_REVIEW_TIME", "21:00")), tables=[LN], absent=True, frame=True, mention=False),
    dict(id="t7_nhac", wd=5, at=_hm(os.getenv("THU7_NHAC_TIME", "20:58")), tables=[LN], nhac=True),
    dict(id="cn", wd=6, at=_hm(os.getenv("CN_REVIEW_TIME", "19:10")), tables=[GD, DL_CN], absent=True),
]
REVIEW_GRACE = 30 * 60  # reader khởi động trễ quá 30 phút sau giờ hẹn thì bỏ, không báo muộn
_RANK = {"missing": 0, "absent": 1, "ok": 2}


def mark_status(rest: str):
    """-> (trạng thái, ghi chú): ok nếu có ⭕, absent nếu có ❌, còn lại missing."""
    if "⭕" in rest:
        return "ok", ""
    if "❌" in rest:
        return "absent", rest.replace("❌", "", 1).strip(" -/")
    return "missing", ""


def table_status(msgs, title: str):
    """Gộp mọi bản của một bảng điểm danh trong các tin -> [(mã, trạng thái, ghi chú)]; None nếu không thấy bảng."""
    rows, found = {}, False
    for m in msgs:
        text = m.text or ""
        if title not in bot.norm(text):
            continue
        lines = [bot.ROW.match(x) for x in text.splitlines()]
        lines = [x for x in lines if x]
        if len(lines) < 3:
            continue
        found = True
        for mt in lines:
            st = mark_status(mt.group(2).strip())
            old = rows.get(mt.group(1))
            if old is None or _RANK[st[0]] > _RANK[old[0]]:
                rows[mt.group(1)] = st
    return [(c, st, note) for c, (st, note) in rows.items()] if found else None


def review_frame(rows, label: str, day: dt.date) -> str:
    """Bảng kết quả gộp: ⭕️ đã điểm danh, ❌ báo vắng (kèm lý do), ➖ chưa điểm danh."""
    lines = [f"🌈 KẾT QUẢ ĐIỂM DANH {label.upper()} [{day:%d.%m}]", ""]
    for i, (code, st, note) in enumerate(rows, 1):
        mark = {"ok": "⭕️", "absent": "❌" + (f"-{note}" if note else ""), "missing": "➖"}[st]
        lines.append(f"{i}. {code}/{mark}")
    return "\n".join(lines + ["", "-Hết-"])


def review_text(rows, label: str, day: dt.date, at: str, with_absent: bool = True):
    """-> (văn bản, có người cần báo hay không)."""
    miss = [c for c, st, _ in rows if st == "missing"]
    absent = [f"{c} ({n})" if n else c for c, st, n in rows if st == "absent"] if with_absent else []
    head = f"📋 {label} {day:%d/%m} — lúc {at}:"
    if not miss and not absent:
        return f"{head} tất cả đã điểm danh ⭕️ ✅", False
    parts = [head]
    if miss:
        parts.append(f"⭕️ Chưa điểm danh ({len(miss)}): " + ", ".join(miss))
    if absent:
        parts.append(f"❌ Không tham gia ({len(absent)}): " + ", ".join(absent))
    return "\n".join(parts), True


async def run_review(group, rv: dict, at: str, relay_out: bool):
    now = dt.datetime.now(bot.TZ)
    start = dt.datetime.combine(now.date(), dt.time(0, 0), tzinfo=bot.TZ)
    msgs = []
    async for m in client.iter_messages(group, limit=1500):
        if m.date < start:
            break
        msgs.append(m)
    outs, notices, mentions = [], [], []
    relay_out = relay_out and rv.get("relay", True)
    for title, label in rv["tables"]:
        rows = table_status(msgs, title)
        if rows is not None and rv.get("nhac"):  # 21:58: chỉ nhắc những người vẫn chưa điểm danh
            miss = [c for c, st, _ in rows if st == "missing"]
            names = bot.load_usernames()
            users = [names[c.split("-")[0].strip()] for c in miss if c.split("-")[0].strip() in names]
            if users:
                text = f"Dạ anh chị {', '.join(users)} điểm danh {label.lower()} giúp em nha ạ"
                await notify_admin(text)
                if relay_out:
                    bot.relay(text)
            continue
        if rows is not None and rv.get("frame"):  # gửi bảng điểm danh trước
            frame = review_frame(rows, label, now.date())
            await notify_admin(frame)
            if relay_out:
                bot.relay(frame)
        if rows is None:
            notices.append(f"📋 {label} {now:%d/%m}: tôi chưa thấy bảng điểm danh này trong nhóm.")
            continue
        if not rv.get("list", True):  # chỉ gửi bảng + tin nhắc @username, không gửi danh sách chưa điểm danh
            mention = bot.mention_msg([c for c, st, _ in rows if st == "missing"], f"điểm danh {label}")
            if mention:
                mentions.append(mention)
            continue
        text, alert = review_text(rows, label, now.date(), at, rv["absent"])
        if alert:
            outs.append(text)
            mention = rv.get("mention", True) and bot.mention_msg([c for c, st, _ in rows if st == "missing"], f"điểm danh {label}")
            if mention:
                mentions.append(mention)
        else:
            notices.append(text)
    for t in notices:
        await notify_admin(t)
    if outs or mentions:
        full = "\n\n".join(outs)
        if full:
            await notify_admin(full)
        if full and relay_out:
            bot.relay(full)  # reader sẽ gửi cho chị từ tài khoản của bạn (tắt bằng /chuyen off)
        for mention in mentions:  # tin nhắc người còn thiếu: gửi bạn, rồi chuyển chị khi là lượt tự động
            await notify_admin(mention)
            if relay_out:
                bot.relay(mention)


async def list_members(group) -> str:
    """Danh sách thành viên nhóm dạng '1. @username' (người không có username thì ghi tên)."""
    lines = []
    async for u in client.iter_participants(group):
        if getattr(u, "bot", False) or getattr(u, "deleted", False):
            continue
        name = " ".join(x for x in (u.first_name, u.last_name) if x) or str(u.id)
        lines.append(f"@{u.username}" if u.username else f"(không có username) {name}")
    body = "\n".join(f"{i}. {x}" for i, x in enumerate(lines, 1))
    return f"👥 Nhóm có {len(lines)} thành viên (không tính bot):\n{body}"


async def review_loop(group):
    while True:
        try:
            await asyncio.sleep(20)
            now = dt.datetime.now(bot.TZ)
            mreq = bot.db.execute("SELECT val FROM seen WHERE key='members_req'").fetchone()
            if mreq and mreq[0] == "1":
                bot.db.execute("INSERT OR REPLACE INTO seen VALUES('members_req','0')"); bot.db.commit()
                txt = await list_members(group)
                log.info(txt)
                for i in range(0, len(txt), 3500):
                    await notify_admin(txt[i:i + 3500])
                continue
            req = bot.db.execute("SELECT val FROM seen WHERE key='review_req'").fetchone()
            if req and req[0] == "1":
                bot.db.execute("INSERT OR REPLACE INTO seen VALUES('review_req','0')"); bot.db.commit()
                today = [r for r in REVIEWS if r["wd"] == now.weekday() and not r.get("nhac")]
                if today:  # rà soát tay: dùng lượt đầy đủ nhất (cuối ngày), chỉ báo riêng bạn
                    await run_review(group, max(today, key=lambda r: r["at"]), f"{now:%H:%M}", relay_out=False)
                else:
                    await notify_admin("Hôm nay không phải thứ 3, 4, 7 hay Chủ nhật nên không có bảng cần rà soát.")
                continue
            for rv in REVIEWS:
                if rv["wd"] != now.weekday():
                    continue
                due = dt.datetime.combine(now.date(), rv["at"], tzinfo=bot.TZ)
                key = f"review_done:{rv['id']}"
                done = bot.db.execute("SELECT val FROM seen WHERE key=?", (key,)).fetchone()
                if due <= now < due + dt.timedelta(seconds=REVIEW_GRACE) and not (done and done[0] == now.date().isoformat()):
                    bot.db.execute("INSERT OR REPLACE INTO seen VALUES(?,?)", (key, now.date().isoformat())); bot.db.commit()
                    await run_review(group, rv, f"{due:%H:%M}", relay_out=True)
        except Exception:
            log.exception("Lỗi khi rà soát bảng điểm danh")
            await asyncio.sleep(60)


client = TelegramClient(os.path.join(HERE, "tg_reader"), int(os.environ["TG_API_ID"]), os.environ["TG_API_HASH"])


def handle(msg):
    text = msg.text
    if not text:
        return
    try:
        if bot.is_mana(text):
            log.info("Ghi nhận bài Mana ngày %s", bot.ingest_mana(text, msg.date.astimezone(bot.TZ).date()))
        elif bot.is_kt(text):
            log.info("Ghi nhận bài KT tuần %s", bot.ingest_kt(text))
    except Exception:
        log.exception("Lỗi khi xử lý tin")


async def find_group():
    async for d in client.iter_dialogs():
        if d.entity.id == GROUP_ID:
            return d.entity
    async for d in client.iter_dialogs():
        if (d.name or "").strip().lower() == GROUP_TITLE.lower():
            return d.entity


async def main():
    await client.connect()
    if not await client.is_user_authorized():
        log.error("Chưa đăng nhập. Hãy chạy: .venv/bin/python login.py")
        sys.exit(78)
    group = await find_group()
    if group is None:
        log.error("Không tìm thấy nhóm (TG_GROUP_ID=%s)", GROUP_ID)
        sys.exit(1)
    log.info("Đang đọc nhóm: %s", getattr(group, "title", group.id))

    # đọc lại lịch sử từ đầu tuần để bù những bài đã đăng trước khi chương trình chạy
    monday = dt.datetime.combine(bot.week_monday(), dt.time(0, 0), tzinfo=bot.TZ)
    old = []
    async for m in client.iter_messages(group, limit=3000):
        if m.date < monday:
            break
        old.append(m)
    for m in reversed(old):
        handle(m)
    log.info("Đã quét %d tin từ đầu tuần", len(old))

    @client.on(events.NewMessage(chats=group))
    @client.on(events.MessageEdited(chats=group))
    async def _(event):
        handle(event.message)

    @client.on(events.NewMessage(incoming=True))
    async def _auto(event):
        if not event.is_private or not autoreply_on() or not in_hours(event.message.date):
            return  # ngoài khung giờ làm việc thì không tự trả lời
        sender = await event.get_sender()
        if sender is None or getattr(sender, "is_self", False) or getattr(sender, "deleted", False):
            return
        if sender.id == 777000 or (getattr(sender, "bot", False) and not AUTO_BOTS):
            return  # bỏ qua thông báo của Telegram và các bot
        if sender.id in pending:
            return
        pending.add(sender.id)
        asyncio.create_task(delayed_reply(event.chat_id, sender.id, event.message.date))

    asyncio.create_task(review_loop(group))
    if RELAY_TARGET:
        asyncio.create_task(outbox_loop())
    await client.run_until_disconnected()


if __name__ == "__main__":
    asyncio.run(main())
