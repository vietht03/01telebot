#!/data/data/com.termux/files/usr/bin/bash
# Chạy bot + chương trình đọc nhóm, tự chạy lại nếu bị lỗi. Dùng qua tmux (xem start.sh).
cd "$(dirname "$0")/.." || exit 1
termux-wake-lock 2>/dev/null   # giữ CPU thức để Android không ngủ làm bot dừng
( while true; do python bot.py >> bot.log 2>&1; echo "$(date) bot dừng, chạy lại sau 10 giây" >> bot.log; sleep 10; done ) &
( while true; do python reader.py >> reader.log 2>&1; code=$?
  [ "$code" -eq 78 ] && { echo "$(date) chưa đăng nhập Telegram" >> reader.log; break; }
  echo "$(date) reader dừng, chạy lại sau 10 giây" >> reader.log; sleep 10; done ) &
wait
