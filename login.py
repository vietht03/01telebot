"""Đăng nhập Telegram MỘT LẦN (chạy trong terminal của bạn). Hỏi số điện thoại, mã xác nhận, mật khẩu 2FA nếu có."""
import os
from dotenv import load_dotenv
from telethon import TelegramClient

HERE = os.path.dirname(os.path.abspath(__file__))
load_dotenv(os.path.join(HERE, ".env"))
GROUP_ID = int(os.getenv("TG_GROUP_ID", "0"))

with TelegramClient(os.path.join(HERE, "tg_reader"), int(os.environ["TG_API_ID"]), os.environ["TG_API_HASH"]) as client:
    me = client.get_me()
    print(f"\nĐăng nhập thành công: {me.first_name}")
    found = [d.name for d in client.iter_dialogs() if d.entity.id == GROUP_ID]
    print("Tìm thấy nhóm:", found[0] if found else "KHÔNG THẤY (kiểm tra TG_GROUP_ID)")
for f in os.listdir(HERE):
    if f.startswith("tg_reader.session"):
        os.chmod(os.path.join(HERE, f), 0o600)
