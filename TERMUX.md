# Chạy bot trên điện thoại Android (Termux)

> Chỉ chạy bot ở **một nơi tại một thời điểm**. Bot Telegram chỉ cho một nơi nhận tin, và tài khoản đăng nhập
> (`tg_reader.session`) dùng đồng thời ở hai máy có thể bị Telegram hủy phiên. Phải tắt bản trên máy tính trước.

## 1. Cài ứng dụng (trên điện thoại)
1. Cài **Termux** và **Termux:Boot** từ **F-Droid** (không dùng bản trên Google Play, đã cũ).
2. Mở **Termux:Boot** một lần để nó đăng ký chạy khi bật máy.

## 2. Chống Android tắt app chạy nền (quan trọng nhất)
Cài đặt → Pin (hoặc Quản lý ứng dụng) → **Termux** và **Termux:Boot**:
- Tối ưu hóa pin: **Không hạn chế** / Không tối ưu.
- Cho phép **hoạt động nền** và **tự khởi động**.
- Mở danh sách ứng dụng gần đây, **khóa** Termux lại (kéo xuống / biểu tượng ổ khóa), không vuốt tắt.
- Tắt chế độ tiết kiệm pin.
Giữ điện thoại **cắm sạc** khi có thể và **luôn có mạng** (Wi-Fi hoặc 4G).

## 3. Mở cổng nhận file trong Termux
```bash
pkg update -y && pkg install -y openssh
passwd            # đặt mật khẩu tạm
sshd              # bật SSH (cổng 8022)
whoami            # ghi lại tên user, ví dụ u0_a123
ifconfig          # tìm địa chỉ IP của wlan0, ví dụ 192.168.1.20
```

## 4. Gửi code từ máy tính sang điện thoại (chạy trên máy tính, cùng mạng Wi-Fi)
```bash
cd ~/Desktop
tar czf /tmp/telebot.tgz --exclude=tele-bot/.venv --exclude='tele-bot/__pycache__' --exclude='*.bak' --exclude=tele-bot/.git tele-bot
scp -P 8022 /tmp/telebot.tgz TÊN_USER@IP_ĐIỆN_THOẠI:~/
rm /tmp/telebot.tgz
```
Gói này có `.env` và file đăng nhập Telegram nên không gửi qua chat hay đám mây.

## 5. Cài trên điện thoại
```bash
cd ~ && tar xzf telebot.tgz && rm telebot.tgz
cd tele-bot && bash termux/setup.sh
```

## 6. Chuyển bot sang điện thoại
Trên **máy tính**, tắt bản cũ trước:
```bash
systemctl --user disable --now diemdanh-bot diemdanh-reader diemdanh-keepawake
```
Trên **điện thoại**:
```bash
bash termux/start.sh
tail -f bot.log reader.log      # thấy "Application started" và "Đã quét ..." là chạy
```
Kiểm tra: nhắn bot `/myid` trong Telegram, bot trả lời là xong.

## 7. Dọn dẹp
```bash
pkill sshd        # tắt cổng nhận file
```
Bot tự chạy lại mỗi khi điện thoại bật (nhờ Termux:Boot) và tự khởi động lại nếu bị lỗi.

## Lưu ý
- Điện thoại **tắt Wi-Fi mà không có 4G** thì bot mất mạng và dừng gửi.
- Điện thoại **hết pin hoặc tắt máy** thì bot dừng.
- Xem log: `tmux attach -t bot` hoặc `tail -f ~/tele-bot/bot.log`. Dừng bot: `tmux kill-session -t bot`.
