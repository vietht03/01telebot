#!/data/data/com.termux/files/usr/bin/bash
# Khởi động bot trong tmux (nếu chưa chạy).
DIR="$(cd "$(dirname "$0")/.." && pwd)"
tmux has-session -t bot 2>/dev/null && { echo "Bot đang chạy rồi (tmux attach -t bot để xem)."; exit 0; }
tmux new-session -d -s bot "bash $DIR/termux/run_all.sh"
echo "Đã khởi động bot."
