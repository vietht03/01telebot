#!/usr/bin/env bash
# Kiểm tra bot trên điện thoại (qua adb) và báo Telegram cho admin khi bot chết / sống lại.
# Chạy định kỳ bằng systemd timer (xem watchdog/*.service, *.timer).
DIR="$(cd "$(dirname "$0")/.." && pwd)"
STATE="$HOME/.cache/telebot-watchdog.state"   # dòng: <ok|dead|unreach> <fail_count> <last_alert_epoch>
mkdir -p "$(dirname "$STATE")"
TOKEN=$(grep -m1 '^BOT_TOKEN=' "$DIR/.env" | cut -d= -f2-)
ADMIN=$(grep -m1 '^ADMIN_ID=' "$DIR/.env" | cut -d= -f2-)
send() { curl -s -m 20 "https://api.telegram.org/bot${TOKEN}/sendMessage" --data-urlencode "chat_id=${ADMIN}" --data-urlencode "text=$1" >/dev/null; }

read -r prev fails last < "$STATE" 2>/dev/null || { prev=ok; fails=0; last=0; }
now=$(date +%s)

if ! timeout 15 adb get-state 2>/dev/null | grep -q device; then
  status=unreach
else
  n=$(timeout 15 adb shell "ps -A | grep -c python" 2>/dev/null | tr -d '\r')
  [ "${n:-0}" -ge 2 ] && status=ok || status=dead
fi

case "$status" in
  ok)
    [ "$prev" != ok ] && send "✅ Bot trên điện thoại đã chạy lại bình thường."
    echo "ok 0 0" > "$STATE" ;;
  dead)
    # báo ngay khi phát hiện, nhắc lại mỗi 60 phút nếu vẫn chết
    if [ "$prev" != dead ] || [ $((now - last)) -ge 3600 ]; then
      send "🚨 Bot điểm danh trên điện thoại ĐÃ CHẾT (không thấy tiến trình python). Mở Termux và chạy: bash ~/tele-bot/termux/start.sh"
      last=$now
    fi
    echo "dead 0 $last" > "$STATE" ;;
  unreach)
    fails=$((fails + 1))
    # mất kết nối adb liên tiếp ~15 phút mới báo (có thể rút cáp / điện thoại tắt)
    if [ "$fails" -ge 3 ] && { [ "$prev" != unreach ] || [ $((now - last)) -ge 3600 ]; }; then
      send "⚠️ Laptop không kết nối được điện thoại qua adb (rút cáp, điện thoại tắt hoặc hết pin?). Không kiểm tra được bot."
      last=$now; prev=unreach
    fi
    # giữ trạng thái cũ nếu chưa đủ ngưỡng để không báo 'sống lại' sai
    echo "${prev} ${fails} ${last}" > "$STATE" ;;
esac
