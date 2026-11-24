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
        if not autoreply_on():
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
        if not event.is_private or not autoreply_on():
            return
        sender = await event.get_sender()
        if sender is None or getattr(sender, "is_self", False) or getattr(sender, "deleted", False):
            return
        if sender.id == 777000 or (getattr(sender, "bot", False) and not AUTO_BOTS):
            return  # bỏ qua thông báo của Telegram và các bot
        if sender.id in pending:
            return
        pending.add(sender.id)
        asyncio.create_task(delayed_reply(event.chat_id, sender.id, event.message.date))

    if RELAY_TARGET:
        asyncio.create_task(outbox_loop())
    await client.run_until_disconnected()


if __name__ == "__main__":
    asyncio.run(main())
