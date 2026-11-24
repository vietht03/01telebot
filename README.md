# 01telebot

Bot Telegram hỗ trợ điểm danh, kèm RAG (hỏi đáp trên tài liệu PDF và dữ liệu điểm danh).

## Chức năng
- Đọc nhóm Telegram (bằng tài khoản người dùng, chỉ đọc) để ghi nhận bài **Mana** và **Cảm nhận KT**.
- Báo cáo tự động cho admin: 17:31 (Mana) và 23:30 (KT, kèm khung đầy đủ).
- Gửi form điểm danh cố định theo lịch (thứ 4, thứ 5) với ngày tự cập nhật.
- Chuyển các báo cáo sang người nhận khác từ tài khoản của admin.
- Tự trả lời "đang bận" khi có người nhắn riêng mà chưa được trả lời.
- RAG: nạp PDF/txt/md, hỏi đáp bằng Gemini hoặc Claude.

## Cài đặt
```bash
python3 -m venv .venv && .venv/bin/pip install -r requirements.txt
cp .env.example .env            # rồi điền BOT_TOKEN, ADMIN_ID, GEMINI_API_KEY, TG_API_ID, TG_API_HASH
cp msg_thu4.example.txt msg_thu4.txt   # chép và sửa danh sách thật
cp msg_thu5.example.txt msg_thu5.txt
.venv/bin/python login_qr.py    # đăng nhập Telegram bằng QR (một lần)
.venv/bin/python bot.py         # bot
.venv/bin/python reader.py      # chương trình đọc nhóm (chạy riêng)
```

## Bảo mật
`.env` và `tg_reader.session*` cho toàn quyền với bot và tài khoản Telegram — **không bao giờ commit**.
