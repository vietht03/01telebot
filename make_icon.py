from PIL import Image, ImageDraw
S = 1024
img = Image.new("RGB", (S, S))
px = img.load()
for y in range(S):  # gradient xanh dương -> tím
    for x in range(S):
        t = (x + y) / (2 * S)
        px[x, y] = (int(40 + 90 * t), int(110 - 40 * t), int(230 - 20 * t))
d = ImageDraw.Draw(img)
# ăng-ten
d.line([(512, 215), (512, 140)], fill="white", width=26)
d.ellipse([482, 100, 542, 160], fill="#FFD166")
# đầu robot
d.rounded_rectangle([200, 230, 824, 800], radius=130, fill="white")
# tai
d.rounded_rectangle([140, 440, 200, 590], radius=30, fill="white")
d.rounded_rectangle([824, 440, 884, 590], radius=30, fill="white")
# mắt
for cx in (390, 634):
    d.ellipse([cx - 62, 350, cx + 62, 474], fill="#2B3A8C")
    d.ellipse([cx - 22, 372, cx + 8, 402], fill="white")
# dấu tick điểm danh
d.line([(380, 625), (475, 715), (655, 540)], fill="#22C55E", width=50, joint="curve")
for p in [(380, 625), (475, 715), (655, 540)]:
    d.ellipse([p[0] - 25, p[1] - 25, p[0] + 25, p[1] + 25], fill="#22C55E")
img.resize((640, 640), Image.LANCZOS).save("bot_icon.png")
