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


async def notify_admin(text: str, log_it: bool = True):
    import httpx
    try:
        if log_it:
            bot.rag.log_event("gửi bạn", text)
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
                bot.rag.log_event("chuyển cho chị", text)
                await notify_admin(f"✅ Đã gửi cho chị ({RELAY_TARGET}): {first}", log_it=False)
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
# ---- rà soát bảng điểm danh: forward nguyên bảng gốc rồi nhắc người chưa điểm danh ----
# Mỗi dòng: ⭕ = đã điểm danh, ❌ = hôm đó không tham gia, còn lại (trống/chưa có ⭕) = chưa điểm danh.
# Báo cho bạn ngay, rồi chuyển tiếp sang chị (RELAY_TARGET) từ tài khoản của bạn.
GD = ("diem danh giao duc truoc le chu nhat", "Giáo dục trước lễ CN")
DL_CN = ("diem danh dang le chu nhat", "Dâng lễ Chủ nhật")
LN = ("diem danh tham gia le nhom", "Tham gia lễ nhóm")
ST = ("diem danh giao duc sach sang tao troi dat", "Giáo dục sách Sáng tạo Trời Đất")
REVIEWS = [  # id, thứ (0=T2), giờ rà soát, các bảng; nhac=True: chỉ nhắc, không forward bảng
    # mỗi lượt: tin 1 = forward nguyên bảng gốc (cho bạn qua Saved Messages và cho chị qua tài khoản của bạn),
    # tin 2 = "Dạ anh chị @a @b điểm danh ... giúp em nha ạ" cho những người chưa điểm danh
    dict(id="t3", wd=1, at=_hm(os.getenv("THU3_REVIEW_TIME", "21:50")), tables=[ST]),
    dict(id="t5", wd=3, at=_hm(os.getenv("THU5_REVIEW_TIME", "08:50")), tables=[("diem danh jondo dan", "Jondo Dan")], count=True),
    dict(id="t4", wd=2, at=_hm(os.getenv("THU4_REVIEW_TIME", "19:10")), tables=[("diem danh dang le thu 4", "Dâng lễ thứ 4")]),
    dict(id="cn_gd", wd=6, at=_hm(os.getenv("CN_GD_REVIEW_TIME", "10:00")), tables=[GD]),
    dict(id="t7", wd=5, at=_hm(os.getenv("THU7_REVIEW_TIME", "21:00")), tables=[LN]),
    dict(id="t7_nhac", wd=5, at=_hm(os.getenv("THU7_NHAC_TIME", "20:58")), tables=[LN], nhac=True),
    dict(id="cn", wd=6, at=_hm(os.getenv("CN_REVIEW_TIME", "11:40")), tables=[GD, DL_CN]),
    # hằng ngày: forward nguyên bài Mana (17:30) / bài Cảm nhận KT (23:30) trong nhóm, rồi tới dòng "có ... chưa ..."
    dict(id="mana", days=bot.MANA_DAYS, at=_hm(bot.MANA_TIME), post="mana"),
    dict(id="kt", days=bot.KT_DAYS, at=_hm(bot.KT_TIME), post="kt"),
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


def table_message(msgs, title: str):
    """Tin mới nhất chứa bảng `title` (msgs xếp từ mới đến cũ)."""
    for m in msgs:
        text = m.text or ""
        if title in bot.norm(text) and sum(1 for x in text.splitlines() if bot.ROW.match(x)) >= 3:
            return m
    return None


async def forward_table(group, msg, relay_out: bool, count_text: str = None):
    """Forward nguyên bảng gốc cho bạn (Saved Messages), báo số liệu (nếu có), rồi nếu relay_out thì forward cho chị bằng tài khoản của bạn."""
    global relay_entity
    await client.forward_messages("me", msg.id, group)
    await notify_admin("📌 Đã forward nguyên bảng điểm danh vào Saved Messages của bạn.")
    if count_text:
        await notify_admin(count_text)
    if relay_out and bot.relay_on():
        if relay_entity is None:
            relay_entity = await client.get_entity(RELAY_TARGET)
        await client.forward_messages(relay_entity, msg.id, group)
        await notify_admin(f"✅ Đã forward nguyên bảng cho chị ({RELAY_TARGET}).")


relay_entity = None


async def run_review(group, rv: dict, at: str, relay_out: bool):
    now = dt.datetime.now(bot.TZ)
    start = dt.datetime.combine(now.date(), dt.time(0, 0), tzinfo=bot.TZ)
    msgs = []
    async for m in client.iter_messages(group, limit=1500):
        if m.date < start:
            break
        msgs.append(m)
    relay_out = relay_out and rv.get("relay", True)
    for title, label in rv["tables"]:
        rows = table_status(msgs, title)
        if rows is None:
            await notify_admin(f"📋 {label} {now:%d/%m}: tôi chưa thấy bảng điểm danh này trong nhóm.")
            continue
        count_text = None
        if rv.get("count"):  # báo số người vắng ngay sau khi forward cho bạn, trước khi chuyển cho chị
            n_ok = sum(1 for _, st, _ in rows if st == "ok")
            n_abs = sum(1 for _, st, _ in rows if st == "absent")
            n_miss = sum(1 for _, st, _ in rows if st == "missing")
            count_text = (f"📊 {label} {now:%d/%m} lúc {at}: {len(rows)} người — đã điểm danh ⭕️ {n_ok}, "
                          f"vắng {n_abs + n_miss} (❌ {n_abs}, chưa điểm danh {n_miss}).")
        if not rv.get("nhac"):  # tin 1: forward nguyên bảng gốc
            await forward_table(group, table_message(msgs, title), relay_out, count_text)
        # tin 2: nhắc những người chưa điểm danh
        miss = [c for c, st, _ in rows if st == "missing"]
        names = bot.load_usernames()
        users = [names[c.split("-")[0].strip()] for c in miss if c.split("-")[0].strip() in names]
        if users:
            text = f"Dạ anh chị {' '.join(users)} điểm danh {label.lower()} giúp em nha ạ"
            await notify_admin(text)
            if relay_out:
                bot.relay(text)
        elif not miss:
            await notify_admin(f"📋 {label} {now:%d/%m} — lúc {at}: tất cả đã điểm danh ⭕️ ✅")
        else:
            await notify_admin(f"📋 {label} {now:%d/%m}: còn {len(miss)} người chưa điểm danh nhưng chưa có @username trong usernames.txt: " + ", ".join(miss))


POSTS = {  # loại bài -> (hàm nhận dạng, nhãn, có lấy từ đầu tuần không)
    "mana": (bot.is_mana, "Mana", False),
    "kt": (bot.is_kt, "Cảm nhận KT", True),
}


async def run_post(group, rv: dict, relay_out: bool):
    """Tin 1: forward nguyên bài Mana/KT mới nhất trong nhóm. Tin 2: dòng 'có ... chưa ...' (gửi bạn rồi chuyển chị)."""
    is_post, label, weekly = POSTS[rv["post"]]
    now = dt.datetime.now(bot.TZ)
    day0 = bot.week_monday() if weekly else now.date()
    start = dt.datetime.combine(day0, dt.time(0, 0), tzinfo=bot.TZ)
    found = None
    async for m in client.iter_messages(group, limit=3000):
        if m.date < start:
            break
        if m.text and is_post(m.text):
            found = m
            break
    if found is None:
        scope = "tuần này" if weekly else "hôm nay"
        return await notify_admin(f"⏰ Đã quá {rv['at']:%H:%M} nhưng tôi chưa thấy bài {label} {scope} trong nhóm. "
                                  "Bạn có thể copy bài dán vào đây để tôi đối chiếu.")
    await forward_table(group, found, relay_out and rv.get("relay", True))
    line = bot.kt_summary(day0) if weekly else bot.mana_line(now.date())
    await notify_admin(line)
    if relay_out and rv.get("relay", True):
        bot.relay(line)


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
            preq = bot.db.execute("SELECT val FROM seen WHERE key='post_req'").fetchone()
            if preq and preq[0].rstrip("!") in POSTS:  # chạy tay lượt Mana/KT: chỉ gửi bạn; thêm "!" (vd "mana!") thì gửi cả chị
                bot.db.execute("INSERT OR REPLACE INTO seen VALUES('post_req','')"); bot.db.commit()
                await run_post(group, next(r for r in REVIEWS if r.get("post") == preq[0].rstrip("!")), relay_out=preq[0].endswith("!"))
                continue
            req = bot.db.execute("SELECT val FROM seen WHERE key='review_req'").fetchone()
            if req and req[0] == "1":
                bot.db.execute("INSERT OR REPLACE INTO seen VALUES('review_req','0')"); bot.db.commit()
                today = [r for r in REVIEWS if r.get("wd") == now.weekday() and not r.get("nhac")]
                if today:  # rà soát tay: dùng lượt đầy đủ nhất (cuối ngày), chỉ báo riêng bạn
                    await run_review(group, max(today, key=lambda r: r["at"]), f"{now:%H:%M}", relay_out=False)
                else:
                    await notify_admin("Hôm nay không phải thứ 3, 4, 7 hay Chủ nhật nên không có bảng cần rà soát.")
                continue
            for rv in REVIEWS:
                if now.weekday() not in (rv.get("days") or (rv["wd"],)):
                    continue
                due = dt.datetime.combine(now.date(), rv["at"], tzinfo=bot.TZ)
                key = f"review_done:{rv['id']}"
                done = bot.db.execute("SELECT val FROM seen WHERE key=?", (key,)).fetchone()
                if due <= now < due + dt.timedelta(seconds=REVIEW_GRACE) and not (done and done[0] == now.date().isoformat()):
                    bot.db.execute("INSERT OR REPLACE INTO seen VALUES(?,?)", (key, now.date().isoformat())); bot.db.commit()
                    if bot.rag.paused():  # /tat: bỏ qua lượt này
                        continue
                    if rv.get("post"):
                        await run_post(group, rv, relay_out=True)
                    else:
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
    wd = ["Thứ 2", "Thứ 3", "Thứ 4", "Thứ 5", "Thứ 6", "Thứ 7", "Chủ nhật"]
    lines = []
    for rv in REVIEWS:
        days = "hằng ngày" if len(rv.get("days") or ()) == 7 else wd[rv["wd"]] if "wd" in rv else "các ngày " + ",".join(wd[d] for d in rv["days"])
        what = ("bảng " + ", ".join(lb for _, lb in rv["tables"])) if rv.get("tables") else ("bài " + POSTS[rv["post"]][1])
        how = "chỉ nhắc người chưa điểm danh" if rv.get("nhac") else "forward bảng gốc cho bạn rồi chuyển chị + nhắc người chưa điểm danh"
        lines.append(f"- {days} {rv['at']:%H:%M}: rà soát {what} ({how})")
    bot.rag.set_schedule("schedule_reader", "\n".join(lines))

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
