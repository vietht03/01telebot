#!/data/data/com.termux/files/usr/bin/bash
# Cài bot trên Termux. Chạy trong thư mục dự án: bash termux/setup.sh
set -e
cd "$(dirname "$0")/.."
pkg update -y
pkg install -y python tmux openssh tzdata
pip install -r requirements-termux.txt
mkdir -p ~/.termux/boot
cp termux/boot.sh ~/.termux/boot/start-bot.sh
chmod +x ~/.termux/boot/start-bot.sh termux/run_all.sh
echo
echo "Cài xong. Chạy thử: bash termux/start.sh"
