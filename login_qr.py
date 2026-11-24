"""Đăng nhập Telegram bằng mã QR (không cần nhận mã qua tin nhắn). Chạy trong terminal của bạn."""
import asyncio, getpass, os
import qrcode
from dotenv import load_dotenv
from telethon import TelegramClient
from telethon.errors import SessionPasswordNeededError

HERE = os.path.dirname(os.path.abspath(__file__))
load_dotenv(os.path.join(HERE, ".env"))
GROUP_ID = int(os.getenv("TG_GROUP_ID", "0"))
PNG = os.path.join(HERE, "qr_login.png")


def show(url: str):
    qr = qrcode.QRCode(border=1)
    qr.add_data(url)
    qr.make()
    print("\033[2J\033[H", end="")  # xóa màn hình
    print("Trên điện thoại: Telegram → Cài đặt → Thiết bị → Liên kết thiết bị máy tính → quét mã này\n")
    qr.print_ascii(invert=True)
    qr.make_image().save(PNG)  # bản ảnh dự phòng nếu mã trong terminal bị méo
    print(f"\n(Nếu quét không được, mở file ảnh {PNG})")


async def main():
    client = TelegramClient(os.path.join(HERE, "tg_reader"), int(os.environ["TG_API_ID"]), os.environ["TG_API_HASH"])
    await client.connect()
    try:
        if not await client.is_user_authorized():
            qr = await client.qr_login()
            while True:
                show(qr.url)
                try:
                    await qr.wait(timeout=25)
                    break
                except asyncio.TimeoutError:
                    await qr.recreate()  # mã QR hết hạn sau ~30 giây, tạo mã mới
                except SessionPasswordNeededError:
                    await client.sign_in(password=getpass.getpass("Mật khẩu xác minh 2 bước: "))
                    break
        me = await client.get_me()
        print(f"\nĐăng nhập thành công: {me.first_name}")
        found = [d.name async for d in client.iter_dialogs() if d.entity.id == GROUP_ID]
        print("Tìm thấy nhóm:", found[0] if found else "KHÔNG THẤY (kiểm tra TG_GROUP_ID)")
    finally:
        await client.disconnect()
        if os.path.exists(PNG):
            os.remove(PNG)
        for f in os.listdir(HERE):
            if f.startswith("tg_reader.session"):
                os.chmod(os.path.join(HERE, f), 0o600)

asyncio.run(main())
