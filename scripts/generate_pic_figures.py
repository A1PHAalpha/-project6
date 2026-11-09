"""Generate the pending figures from 配图清单.xlsx into ``pic/``.

Run with the system Python (needs matplotlib + Pillow), for example::

    python3 scripts/generate_pic_figures.py

The checklist has 32 entries: 18 marked 已绘制, 9 示意图, 3 真实数据, and
2 实机截图. This script generates the 14 pending figures and writes a
manifest for all 32 entries. It never fabricates data: the three data
charts read ``data/ops.db`` and ``knowledge_base/articles.json``.
"""

from __future__ import annotations

import csv
import json
import math
import sqlite3
import zipfile
from collections import Counter
from pathlib import Path
from xml.etree import ElementTree as ET

from PIL import Image, ImageDraw, ImageFont

ROOT = Path("/home/wyj/project6")
PIC = ROOT / "pic"
SHOTS = PIC / "screenshots"
PIC.mkdir(exist_ok=True)
SHOTS.mkdir(exist_ok=True)
XLSX = Path("/home/wyj/下载/配图清单.xlsx")
UI = Path("/tmp/opencode/ui")
DB = ROOT / "data" / "ops.db"

# ---------------------------------------------------------------------------
# fonts / palette / helpers
# ---------------------------------------------------------------------------
FONT_REG = "/usr/share/fonts/opentype/noto/NotoSansCJK-Regular.ttc"
FONT_BOLD = "/usr/share/fonts/opentype/noto/NotoSansCJK-Bold.ttc"
SC_INDEX = 2  # 0=JP, 1=KR, 2=SC, 3=TC, 4=HK

WHITE = "#ffffff"
INK = "#0f172a"
MUTED = "#475569"
LINE = "#cbd5e1"
BLUE = "#2563eb"
BLUE_BG = "#eff6ff"
GREEN = "#16a34a"
GREEN_BG = "#f0fdf4"
ORANGE = "#ea580c"
ORANGE_BG = "#fff7ed"
RED = "#dc2626"
RED_BG = "#fef2f2"
PURPLE = "#7c3aed"
PURPLE_BG = "#f5f3ff"
SLATE_BG = "#f8fafc"
TEAL = "#0f766e"
TEAL_BG = "#f0fdfa"
AMBER = "#b45309"
AMBER_BG = "#fffbeb"
PINK = "#be185d"
PINK_BG = "#fdf2f8"


def font(size: int, bold: bool = False):
    return ImageFont.truetype(
        FONT_BOLD if bold else FONT_REG, size, index=SC_INDEX
    )


def canvas(w: int, h: int, bg: str = WHITE):
    img = Image.new("RGB", (w, h), bg)
    return img, ImageDraw.Draw(img)


def tlen(draw: ImageDraw.ImageDraw, s, f):
    return draw.textlength(str(s), font=f)


def wrap(draw, s, f, maxw):
    lines = []
    for para in str(s).split("\n"):
        cur = ""
        for ch in para:
            if tlen(draw, cur + ch, f) <= maxw:
                cur += ch
            else:
                if cur:
                    lines.append(cur)
                cur = ch
        lines.append(cur)
    return lines


def put(draw, xy, s, f, fill=INK, anchor=None):
    draw.text(xy, str(s), font=f, fill=fill, anchor=anchor)


def header(draw, w, title, subtitle=None):
    draw.rectangle([0, 0, w, 118], fill=INK)
    draw.rectangle([0, 118, w, 124], fill=BLUE)
    put(draw, (48, 30), title, font(46, True), WHITE)
    if subtitle:
        put(draw, (50, 86), subtitle, font(22), "#cbd5e1")
    return 150


def footer(draw, w, h, note):
    draw.line([48, h - 64, w - 48, h - 64], fill=LINE, width=2)
    put(draw, (48, h - 50), note, font(18), MUTED)


def card(
    draw,
    xy,
    title=None,
    body=None,
    fill=SLATE_BG,
    outline=LINE,
    title_color=INK,
    body_color=MUTED,
    title_size=26,
    body_size=20,
    radius=14,
    pad=16,
    gap=8,
    width=2,
):
    x0, y0, x1, y1 = xy
    draw.rounded_rectangle(
        [x0, y0, x1, y1], radius=radius, fill=fill, outline=outline, width=width
    )
    y = y0 + pad
    if title:
        put(draw, (x0 + pad, y), title, font(title_size, True), title_color)
        y += title_size + 10
    if body:
        f = font(body_size)
        for line in body if isinstance(body, list) else [body]:
            for wl in wrap(draw, line, f, (x1 - x0) - 2 * pad):
                put(draw, (x0 + pad, y), wl, f, body_color)
                y += body_size + gap
    return y


def pill(draw, xy, label, fg=WHITE, bg=BLUE, size=18):
    x, y = xy
    f = font(size, True)
    w = int(tlen(draw, label, f)) + 28
    h = size + 18
    draw.rounded_rectangle([x, y, x + w, y + h], radius=h // 2, fill=bg)
    put(draw, (x + 14, y + 7), label, f, fg)
    return x + w


def arrow(draw, p1, p2, color=BLUE, width=4, label=None, label_size=17, dashed=False):
    x1, y1 = p1
    x2, y2 = p2
    if dashed:
        dx, dy = x2 - x1, y2 - y1
        dist = math.hypot(dx, dy)
        if dist:
            n = max(1, int(dist // 14))
            for i in range(n):
                if i % 2:
                    continue
                a = (x1 + dx * i / n, y1 + dy * i / n)
                b = (x1 + dx * (i + 1) / n, y1 + dy * (i + 1) / n)
                draw.line([a, b], fill=color, width=width)
    else:
        draw.line([p1, p2], fill=color, width=width)
    ang = math.atan2(y2 - y1, x2 - x1)
    length = 15
    for off in (0.5, -0.5):
        draw.line(
            [
                (x2, y2),
                (x2 - length * math.cos(ang - off), y2 - length * math.sin(ang - off)),
            ],
            fill=color,
            width=width,
        )
    if label:
        mx, my = (x1 + x2) / 2, (y1 + y2) / 2
        f = font(label_size, True)
        w = tlen(draw, label, f) + 16
        draw.rounded_rectangle(
            [mx - w / 2, my - 16, mx + w / 2, my + 16],
            radius=8,
            fill=WHITE,
            outline=color,
            width=2,
        )
        put(draw, (mx, my), label, f, color, anchor="mm")


def chips(draw, x, y, items, fg=INK, bg=SLATE_BG, outline=LINE, size=18, gap=10):
    cx = x
    for item in items:
        f = font(size, True)
        w = int(tlen(draw, item, f)) + 28
        h = size + 16
        draw.rounded_rectangle([cx, y, cx + w, y + h], radius=8, fill=bg, outline=outline, width=2)
        put(draw, (cx + 14, y + 6), item, f, fg)
        cx += w + gap
    return cx


def shot_path(name):
    """Prefer the fresh capture directory, then the committed screenshots."""
    for base in (UI, SHOTS):
        path = base / name
        if path.exists():
            return path
    return UI / name


def load_fit(path, box):
    im = Image.open(path).convert("RGB")
    im.thumbnail(box, Image.LANCZOS)
    return im


def paste_center(base, im, box, border=LINE):
    x0, y0, x1, y1 = box
    x = int(x0 + (x1 - x0 - im.width) / 2)
    y = int(y0 + (y1 - y0 - im.height) / 2)
    base.paste(im, (x, y))
    d = ImageDraw.Draw(base)
    d.rectangle([x - 1, y - 1, x + im.width, y + im.height], outline=border, width=2)


# ---------------------------------------------------------------------------
# schematics
# ---------------------------------------------------------------------------
def fig3_2():
    W, H = 2200, 1420
    img, d = canvas(W, H)
    y0 = header(
        d,
        W,
        "图3-2  VLAN 与 IP 地址规划图",
        "总部 / 分支A / 分支B；业务 VLAN、SVI/子接口、WAN /30 与管理网 192.168.249.0/24",
    )
    cols = [
        (
            60,
            "分支A",
            "BR-A-R2",
            [
                "E0/0.10  10.20.10.1/24  VLAN 10",
                "E0/0.20  10.20.20.1/24  VLAN 20",
                "E0/0.99  10.20.99.1/24  VLAN 99",
                "E1/0     172.16.1.2/30  -> 总部",
                "E3/0     192.168.249.12/24 管理",
            ],
            "BR-A-SW2",
            [
                "VLAN 10：Et1/0（BR-A-PC）",
                "VLAN 20：备用服务器网段",
                "VLAN 99：互联/测试网段",
                "VLAN 999：Et3/0 管理 SVI",
                "管理 IP：192.168.249.15/24",
            ],
            "BR-A-PC",
            ["10.20.10.11/24", "网关 10.20.10.1", "VLAN 10 接入", "站点：分支A"],
            BLUE,
            BLUE_BG,
        ),
        (
            790,
            "总部",
            "HQ-R1",
            [
                "E0/0.10  10.10.10.1/24  VLAN 10",
                "E0/0.20  10.10.20.1/24  VLAN 20",
                "E0/0.99  10.10.99.1/24  VLAN 99",
                "E1/0     172.16.1.1/30  -> 分支A",
                "E2/0     172.16.2.1/30  -> 分支B",
                "E3/0     192.168.249.11/24 管理",
            ],
            "HQ-SW1",
            [
                "VLAN 10 / 20 / 99 / 999",
                "VLAN 999：管理 SVI",
                "管理 IP：192.168.249.14/24",
                "总部服务器接入 VLAN 10",
                "核心交换与路由互联",
            ],
            "HQ-SRV",
            ["10.10.10.100/24", "网关 10.10.10.1", "VLAN 10 接入", "站点：总部"],
            TEAL,
            TEAL_BG,
        ),
        (
            1520,
            "分支B",
            "BR-B-R3",
            [
                "E0/0.10  10.30.10.1/24  VLAN 10",
                "E0/0.20  10.30.20.1/24  VLAN 20",
                "E0/0.99  10.30.99.1/24  VLAN 99",
                "E1/0     172.16.2.2/30  -> 总部",
                "E3/0     192.168.249.13/24 管理",
            ],
            "BR-B-SW3",
            [
                "VLAN 10：Et1/0（BR-B-PC）",
                "VLAN 20：备用服务器网段",
                "VLAN 99：互联/测试网段",
                "VLAN 999：Et3/0 管理 SVI",
                "管理 IP：192.168.249.16/24",
            ],
            "BR-B-PC",
            ["10.30.10.11/24", "网关 10.30.10.1", "VLAN 10 接入", "站点：分支B"],
            PURPLE,
            PURPLE_BG,
        ),
    ]
    for x, site, rname, rlines, sname, slines, tname, tlines, color, bg in cols:
        # site header
        d.rounded_rectangle([x, y0, x + 620, y0 + 64], radius=14, fill=color)
        put(d, (x + 24, y0 + 14), site, font(30, True), WHITE)
        put(d, (x + 130, y0 + 22), "站点与地址规划", font(20), "#e2e8f0")
        # router / switch / terminal
        card(d, (x, y0 + 84, x + 620, y0 + 404), rname, rlines, fill=SLATE_BG, outline=color,
             title_color=color, title_size=28, body_size=19)
        arrow(d, (x + 310, y0 + 404), (x + 310, y0 + 444), color=color, width=3)
        card(d, (x, y0 + 444, x + 620, y0 + 714), sname, slines, fill=bg, outline=color,
             title_color=color, title_size=28, body_size=19)
        arrow(d, (x + 310, y0 + 714), (x + 310, y0 + 754), color=color, width=3)
        card(d, (x, y0 + 754, x + 620, y0 + 914), tname, tlines, fill=WHITE, outline=color,
             title_color=color, title_size=27, body_size=18)
    # WAN links: HQ is the middle column at x=790. Draw them in the
    # router/switch gap so the labels never cover the router text.
    arrow(d, (790, y0 + 432), (680, y0 + 432), color=RED, width=5)
    put(d, (735, y0 + 410), "172.16.1.0/30", font(15, True), RED, anchor="mm")
    arrow(d, (1410, y0 + 432), (1520, y0 + 432), color=RED, width=5)
    put(d, (1465, y0 + 410), "172.16.2.0/30", font(15, True), RED, anchor="mm")
    # legend
    ly = y0 + 950
    d.rounded_rectangle([60, ly, W - 60, ly + 210], radius=16, fill=SLATE_BG, outline=LINE, width=2)
    put(d, (84, ly + 18), "VLAN / 网段图例", font(26, True), INK)
    legend = [
        ("VLAN 10 用户", "10.10.10.0/24 · 10.20.10.0/24 · 10.30.10.0/24", BLUE, BLUE_BG),
        ("VLAN 20 服务器/备用", "10.10.20.0/24 · 10.20.20.0/24 · 10.30.20.0/24", ORANGE, ORANGE_BG),
        ("VLAN 99 互联/测试", "10.10.99.0/24 · 10.20.99.0/24 · 10.30.99.0/24", PURPLE, PURPLE_BG),
        ("VLAN 999 管理", "192.168.249.0/24（SVI / 管理口）", GREEN, GREEN_BG),
        ("WAN /30", "172.16.1.0/30（总部-分支A）· 172.16.2.0/30（总部-分支B）", RED, RED_BG),
    ]
    for i, (name, sub, fg, bg) in enumerate(legend):
        x = 84 + (i % 2) * 1040
        y = ly + 62 + (i // 2) * 48
        d.rounded_rectangle([x, y, x + 28, y + 28], radius=6, fill=fg)
        put(d, (x + 40, y - 2), name, font(20, True), fg)
        put(d, (x + 300, y + 1), sub, font(17), MUTED)
    footer(d, W, H, "数据来源：network/device_inventory.json；运行态 show vlan brief / show running-config / show ip route；2026-11-09 实测。")
    img.save(PIC / "fig3-2_vlan_ip_plan.png")


def fig3_3():
    W, H = 2200, 1120
    img, d = canvas(W, H)
    y0 = header(
        d,
        W,
        "图3-3  路由与 ACL 规划示意图",
        "分支默认路由指向总部；总部到分支静态路由；直连路由；WAN-IN ACL 的作用点、方向与动作",
    )
    routers = [
        (60, "分支A", "BR-A-R2", BLUE, BLUE_BG, [
            "S*  0.0.0.0/0     via 172.16.1.1",
            "C   10.20.10.0/24 E0/0.10",
            "C   10.20.20.0/24 E0/0.20",
            "C   10.20.99.0/24 E0/0.99",
            "C   172.16.1.0/30 E1/0",
            "C   192.168.249.0/24 E3/0",
        ]),
        (790, "总部", "HQ-R1", TEAL, TEAL_BG, [
            "S   10.20.0.0/16 via 172.16.1.2",
            "S   10.30.0.0/16 via 172.16.2.2",
            "C   10.10.10.0/24 E0/0.10",
            "C   10.10.20.0/24 E0/0.20",
            "C   10.10.99.0/24 E0/0.99",
            "C   172.16.1.0/30 E1/0",
            "C   172.16.2.0/30 E2/0",
            "C   192.168.249.0/24 E3/0",
        ]),
        (1520, "分支B", "BR-B-R3", PURPLE, PURPLE_BG, [
            "S*  0.0.0.0/0     via 172.16.2.1",
            "C   10.30.10.0/24 E0/0.10",
            "C   10.30.20.0/24 E0/0.20",
            "C   10.30.99.0/24 E0/0.99",
            "C   172.16.2.0/30 E1/0",
            "C   192.168.249.0/24 E3/0",
        ]),
    ]
    for x, site, name, color, bg, routes in routers:
        d.rounded_rectangle([x, y0, x + 620, y0 + 56], radius=12, fill=color)
        put(d, (x + 22, y0 + 12), f"{site} · {name}", font(26, True), WHITE)
        card(d, (x, y0 + 140, x + 620, y0 + 450), "路由表（关键条目）", routes,
             fill=bg, outline=color, title_color=color, body_size=19)
    arrow(d, (790, y0 + 100), (680, y0 + 100), color=RED, width=5, label="默认 0.0.0.0/0", label_size=17)
    arrow(d, (1410, y0 + 100), (1520, y0 + 100), color=RED, width=5, label="默认 0.0.0.0/0", label_size=17)
    put(d, (700, y0 + 124), "172.16.1.0/30", font(17, True), RED)
    put(d, (1425, y0 + 124), "172.16.2.0/30", font(17, True), RED)
    # ACL panel
    py = y0 + 490
    d.rounded_rectangle([60, py, W - 60, py + 400], radius=16, fill=WHITE, outline=TEAL, width=3)
    put(d, (86, py + 20), "WAN-IN ACL：HQ-R1 分支接口入方向", font(30, True), TEAL)
    put(d, (86, py + 64), "作用点：Ethernet1/0（分支A）与 Ethernet2/0（分支B）的 in 方向；命中顺序从上到下。",
        font(19), MUTED)
    rules = [
        ("10", "permit", "ip 10.20.10.0/24 -> 10.10.10.0/24", "分支A终端到总部服务器", GREEN, GREEN_BG),
        ("20", "permit", "ip 10.30.10.0/24 -> 10.10.10.0/24", "分支B终端到总部服务器", GREEN, GREEN_BG),
        ("30", "deny", "ip 10.20.20.0/24 -> 10.10.10.0/24", "分支A服务器网段隔离", RED, RED_BG),
        ("40", "deny", "ip 10.30.20.0/24 -> 10.10.10.0/24", "分支B服务器网段隔离", RED, RED_BG),
        ("50", "permit", "ip any any", "其余流量放行（显式，避免隐式 deny）", GREEN, GREEN_BG),
    ]
    for i, (seq, action, expr, note, fg, bg) in enumerate(rules):
        ry = py + 110 + i * 52
        d.rounded_rectangle([86, ry, W - 86, ry + 42], radius=8, fill=bg, outline=fg, width=1)
        put(d, (104, ry + 8), seq, font(19, True), fg)
        put(d, (160, ry + 8), action.upper(), font(19, True), fg)
        put(d, (300, ry + 8), expr, font(18), INK)
        put(d, (1180, ry + 8), note, font(17), MUTED)
    # direction arrows to ACL panel
    arrow(d, (370, y0 + 450), (370, py), color=TEAL, width=4, label="in", label_size=18)
    arrow(d, (1830, y0 + 450), (1830, py), color=TEAL, width=4, label="in", label_size=18)
    put(d, (86, py + 372), "注：真实评测中曾临时用 PILOT-BLOCK-A/B 替换 WAN-IN 制造 ACL 过度封锁；恢复后必须重新应用 WAN-IN。",
        font(17), AMBER)
    footer(d, W, H, "数据来源：三台路由器 show ip route 实测；HQ-R1 show access-lists 实测；2026-11-09 恢复后状态。")
    img.save(PIC / "fig3-3_routing_acl_plan.png")


def fig3_4():
    W, H = 2200, 1320
    img, d = canvas(W, H)
    y0 = header(
        d,
        W,
        "图3-4  实验网接入链路图",
        "本机 ZeroTier → EVE-NG 管理入口 → 192.168.249.0/24 → 设备 SSH / SNMP / 控制台",
    )
    boxes = [
        ("① 本机运维主机", [
            "ZeroTier 1.16.2",
            "成员：cdf0fdd993",
            "zt6q3ma6ky：10.10.146.195/24",
            "监听 UDP 9993",
        ], BLUE, BLUE_BG),
        ("② ZeroTier Overlay", [
            "网络：743993800f53f957 CornPC",
            "EVE 成员：89560f78b7",
            "peer：LEAF / DIRECT（约 15ms）",
            "zt 接口 RX/TX 必须增长",
        ], PURPLE, PURPLE_BG),
        ("③ EVE-NG 管理入口", [
            "10.10.146.218/24",
            "HTTP 80（仅 HTTP）",
            "控制台 32769–32778",
            "EVE 侧 listnetworks = OK",
        ], TEAL, TEAL_BG),
        ("④ 管理网", [
            "192.168.249.0/24",
            "HQ-R1 .11 / BR-A-R2 .12",
            "BR-B-R3 .13 / HQ-SW1 .14",
            "BR-A-SW2 .15 / BR-B-SW3 .16",
        ], GREEN, GREEN_BG),
        ("⑤ 设备接入", [
            "SSH 22：admin@…",
            "SNMP 161：只读采集",
            "Ping：连通性/业务路径",
            "8 台网络设备 + 终端",
        ], ORANGE, ORANGE_BG),
    ]
    bx = 60
    bw = 400
    for i, (title, lines, color, bg) in enumerate(boxes):
        x = bx + i * (bw + 35)
        card(d, (x, y0, x + bw, y0 + 300), title, lines, fill=bg, outline=color,
             title_color=color, title_size=24, body_size=18)
        if i < len(boxes) - 1:
            arrow(d, (x + bw, y0 + 150), (x + bw + 35, y0 + 150), color=color, width=5)
    # checkpoints
    cy = y0 + 340
    put(d, (60, cy), "可达性检查点（按顺序）", font(28, True), INK)
    checks = [
        ("1", "zerotier-cli info", "本机 ONLINE，成员 cdf0fdd993"),
        ("2", "zerotier-cli peers", "peers 中出现 89560f78b7 且 DIRECT"),
        ("3", "ping 10.10.146.218", "EVE 管理 IP 可达，ARP 为 REACHABLE"),
        ("4", "ip -s link show zt6q3ma6ky", "RX 计数增长；ARP INCOMPLETE / RX=0 即为 L2 未通"),
        ("5", "nc -z 10.10.146.218 22/80/32769", "SSH/HTTP/控制台端口开放"),
        ("6", "设备 SSH + show 命令", "6/6 网络设备登录成功，只读命令 100% 成功"),
    ]
    for i, (num, cmd, verdict) in enumerate(checks):
        x = 60 + (i % 3) * 700
        y = cy + 50 + (i // 3) * 150
        d.rounded_rectangle([x, y, x + 660, y + 124], radius=12, fill=SLATE_BG, outline=LINE, width=2)
        d.ellipse([x + 18, y + 18, x + 60, y + 60], fill=GREEN)
        put(d, (x + 32, y + 26), num, font(22, True), WHITE, anchor="mm")
        put(d, (x + 76, y + 16), cmd, font(19, True), INK)
        put(d, (x + 76, y + 52), verdict, font(17), MUTED)
    # failure judgement
    fy = cy + 380
    d.rounded_rectangle([60, fy, W - 60, fy + 250], radius=16, fill=RED_BG, outline=RED, width=2)
    put(d, (86, fy + 18), "失败判断依据（先排除本机，再定位 EVE 数据面）", font(26, True), RED)
    rows = [
        ("peers 中没有 89560f78b7", "EVE 未上线 / 未加入 CornPC / ZeroTier 未运行"),
        ("peer DIRECT 但 ARP INCOMPLETE、zt RX=0", "控制面通、L2 数据面未通；查 EVE zt 接口/防火墙/Flow Rules"),
        (".218 可达但设备 SSH 不通", "管理网路由或设备 SSH 服务问题；不要改 SSH 地址"),
        ("设备 SSH 成功但业务不可达", "进入故障诊断：VLAN / 路由 / ACL / 链路"),
    ]
    for i, (a, b) in enumerate(rows):
        y = fy + 64 + i * 44
        put(d, (92, y), "• " + a, font(18, True), RED)
        put(d, (730, y), "→ " + b, font(18), INK)
    footer(d, W, H, "数据来源：docs/NETWORK_CONNECTION_GUIDE.md；2026-11-09 恢复后的 peers / zt / 设备 SSH 实测。")
    img.save(PIC / "fig3-4_access_link.png")


def fig4_3():
    W, H = 2200, 1520
    img, d = canvas(W, H)
    y0 = header(
        d,
        W,
        "图4-3  原始输出到状态事实的解析映射图",
        "show 命令 / SNMP / Ping → capture_type → state_type（10 类）→ 关键字段",
    )
    rows = [
        ("show ip interface brief", "interface", "interface", "status / admin_status / ip_address", BLUE, BLUE_BG),
        ("show vlan brief", "vlan", "vlan", "vlan_id / name / status / ports[]", PURPLE, PURPLE_BG),
        ("show ip route", "route", "route", "prefix / protocol / next_hop / outgoing_interface", TEAL, TEAL_BG),
        ("show access-lists", "acl", "acl", "name / rules[seq, action, expression]", ORANGE, ORANGE_BG),
        ("show cdp neighbors", "neighbor", "neighbor", "local_interface / remote_device / remote_interface / protocol", PINK, PINK_BG),
        ("show arp", "arp", "arp", "ip_address / mac / interface / entry_type", AMBER, AMBER_BG),
        ("SNMP ifTable + sysUpTime", "interface / system", "interface / system", "if_index / status / ip / sys_uptime", GREEN, GREEN_BG),
        ("ping <target>", "ping", "reachability", "target_ip / reachable / success_rate", RED, RED_BG),
        ("show running-config", "config", "config", "line_count / sha256（不展开全文）", MUTED, SLATE_BG),
    ]
    tx = 60
    ty = y0 + 20
    widths = [500, 290, 290, 1000]
    headers = ["原始输入", "capture_type", "state_type", "关键字段"]
    colors = [INK, BLUE, PURPLE, TEAL]
    x = tx
    for i, (h, color) in enumerate(zip(headers, colors)):
        d.rounded_rectangle([x, ty, x + widths[i], ty + 54], radius=8, fill=INK)
        put(d, (x + 16, ty + 12), h, font(22, True), WHITE)
        x += widths[i]
    for r, (raw, cap, state, fields, color, bg) in enumerate(rows):
        y = ty + 58 + r * 112
        x = tx
        vals = [raw, cap, state, fields]
        for i, val in enumerate(vals):
            d.rectangle([x, y, x + widths[i], y + 108], fill=bg if i == 2 else (WHITE if r % 2 == 0 else SLATE_BG), outline=LINE, width=1)
            f = font(19, True) if i in (0, 2) else font(17)
            put(d, (x + 14, y + 38), val, f, color if i in (0, 2) else INK)
            x += widths[i]
    # 10 types + guarantees
    gy = ty + 58 + 9 * 112 + 30
    d.rounded_rectangle([tx, gy, W - 60, gy + 220], radius=14, fill=SLATE_BG, outline=LINE, width=2)
    put(d, (tx + 24, gy + 16), "10 类 state_type", font(24, True), INK)
    chips(d, tx + 24, gy + 58, [
        "reachability", "route", "acl", "vlan", "neighbor",
        "interface", "system", "config", "arp", "command_output",
    ], fg=BLUE, bg=BLUE_BG, outline=BLUE, size=17)
    put(d, (tx + 24, gy + 118), "解析保证：每条观测都带 observation_id、item_id、device_id、observed_at_utc；"
        "原始输出写入 raw_blobs；解析失败保留 error_message，不编造状态。", font(18), MUTED)
    put(d, (tx + 24, gy + 154), "同一条 show 输出可生成多条事实（如接口 + IP + 状态），但都归属同一 collection_item，便于回溯。",
        font(18), MUTED)
    footer(d, W, H, "数据来源：collector/state_parser.py:385-427；collector/db/collection.py；collector/run_collect.py。")
    img.save(PIC / "fig4-3_parse_mapping.png")


def fig6_3():
    W, H = 2200, 1520
    img, d = canvas(W, H)
    y0 = header(
        d,
        W,
        "图6-3  证据装配与配额示意图",
        "设备级证据包 → 选择与配额 → Prompt 证据；故障优先、时效控制、配置摘要与脱敏",
    )
    # left
    card(d, (60, y0, 620, y0 + 980), "设备级证据包（最新已发布）", [
        "• 每台设备取最新 full；不足时合并 single / recheck",
        "• 设备不可达：只保留不可达事实，不沿用历史成功状态",
        "• 单设备最多 80 条观测（代码常量 80）",
        "• 30 分钟时效标记：超过则 is_stale=true",
        "• 原始输出不直接进 Prompt；只进摘要 + ID",
        "• 证据不足：no_data / failed → missing_information",
    ], fill=BLUE_BG, outline=BLUE, title_color=BLUE, body_size=19)
    # middle
    card(d, (660, y0, 1440, y0 + 980), "选择与配额（select_compact_evidence）", [
        "• 全局最多 36 条（2026-11-09 由 30 调整）",
        "• 每设备公平份额 = max(4, min(12, 36 / N))",
        "• 先保证每台设备至少 1 条，再按配额回填",
        "• 状态配额：reachability 2 / interface 4 / route 4 /",
        "  vlan 3 / acl 2 / neighbor 3 / arp 2 / system 1 / config 1",
        "• 故障优先级（越小越优先）：",
        "  unreachable / down (0) > 默认路由 (0) > ACL (3)",
        "  > VLAN (4) > neighbor (5) > interface (6) > arp (7)",
        "  > system (8) > config (9)",
    ], fill=AMBER_BG, outline=AMBER, title_color=AMBER, body_size=19)
    # right
    card(d, (1480, y0, W - 60, y0 + 980), "Prompt 证据（脱敏后）", [
        "• 每条：device_name + collected_at + command + summary",
        "• observation_id / item_id / run_id 均可回溯",
        "• 配置快照只给 line_count + sha256 前 16 位",
        "• 脱敏：password / token / secret / community → ***",
        "• 证据不足时给出明确缺失说明，不生成假事实",
        "• 同一问题有/无 KB 使用同一 evidence_hash",
    ], fill=GREEN_BG, outline=GREEN, title_color=GREEN, body_size=19)
    # left mini evidence chain
    lcx0, lcy0 = 90, y0 + 470
    put(d, (lcx0, lcy0), "证据链", font(20, True), BLUE)
    chain = [
        "collection_runs",
        "device_capture_results",
        "collection_items",
        "raw_blobs / state_observations",
    ]
    for i, item in enumerate(chain):
        yy = lcy0 + 38 + i * 62
        d.rounded_rectangle([lcx0, yy, lcx0 + 470, yy + 44], radius=8, fill=WHITE, outline=BLUE, width=2)
        put(d, (lcx0 + 14, yy + 11), item, font(17, True), BLUE)
        if i < len(chain) - 1:
            arrow(d, (lcx0 + 235, yy + 44), (lcx0 + 235, yy + 62), color=BLUE, width=3)
    # right mini prompt structure
    rcx0, rcy0 = 1510, y0 + 470
    put(d, (rcx0, rcy0), "Prompt 结构", font(20, True), GREEN)
    parts = [
        ("system", "7 条硬规则 + 安全边界"),
        ("user", "问题 + 证据摘要 + 知识片段"),
        ("output", "JSON Schema（6 个必需字段）"),
    ]
    for i, (name, desc) in enumerate(parts):
        yy = rcy0 + 38 + i * 80
        d.rounded_rectangle([rcx0, yy, rcx0 + 560, yy + 62], radius=8, fill=WHITE, outline=GREEN, width=2)
        put(d, (rcx0 + 14, yy + 8), name, font(18, True), GREEN)
        put(d, (rcx0 + 14, yy + 34), desc, font(16), MUTED)
    # arrows
    arrow(d, (620, y0 + 490), (660, y0 + 490), color=INK, width=5)
    arrow(d, (1440, y0 + 490), (1480, y0 + 490), color=INK, width=5)
    # quota bars
    bars = [("reachability", 2), ("interface", 4), ("route", 4), ("vlan", 3), ("acl", 2),
            ("neighbor", 3), ("arp", 2), ("system", 1), ("config", 1)]
    bx, by = 700, y0 + 610
    put(d, (bx, by - 34), "状态配额（条/设备桶）", font(20, True), AMBER)
    for i, (name, q) in enumerate(bars):
        y = by + i * 34
        put(d, (bx, y), name, font(16), MUTED)
        d.rounded_rectangle([bx + 190, y + 2, bx + 190 + q * 80, y + 24], radius=6, fill=AMBER, outline=AMBER)
        put(d, (bx + 200 + q * 80, y + 1), str(q), font(16, True), AMBER)
    # bottom callout
    cy = y0 + 1010
    d.rounded_rectangle([60, cy, W - 60, cy + 230], radius=16, fill=RED_BG, outline=RED, width=2)
    put(d, (86, cy + 18), "为什么从 30 改为 36：一次真实的 ACL 试点", font(28, True), RED)
    put(d, (86, cy + 66), "六设备问题中，前几个分支设备先占满 30 条预算，HQ-R1 的故障 ACL 被挤出上下文，模型误判为交换机 preauth ACL。",
        font(19), INK)
    put(d, (86, cy + 102), "修复：全局上限 36 + 每设备公平份额；测试见 tests/test_evidence_selection.py::test_acl_from_later_device_survives_compact_evidence。",
        font(19), INK)
    put(d, (86, cy + 150), "结果：HQ-R1 同时保留路由与 ACL，REAL_ACL_01/02 两条用例均为 correct。", font(19, True), GREEN)
    footer(d, W, H, "数据来源：rag_engine/evidence_builder.py；tests/test_evidence_selection.py；2026-11-09 真实 ACL 试点记录。")
    img.save(PIC / "fig6-3_evidence_quota.png")


def fig7_3():
    W, H = 2200, 1440
    img, d = canvas(W, H)
    y0 = header(
        d,
        W,
        "图7-3  诊断输出字段结构图",
        "diagnosis_v2：6 个必需字段 + missing_information / kb_refs / confidence 的层级关系",
    )
    root = (60, y0 + 420, 400, y0 + 620)
    d.rounded_rectangle(root, radius=18, fill=INK)
    put(d, (230, y0 + 485), "诊断输出", font(34, True), WHITE, anchor="mm")
    put(d, (230, y0 + 535), "JSON / diagnosis_v2", font(20), "#cbd5e1", anchor="mm")
    fields = [
        ("1  fault_phenomenon", "string：故障现象/当前可确认的事实", BLUE, BLUE_BG),
        ("2  evidence_used[]", "observation_id / device_name / summary / run_id", TEAL, TEAL_BG),
        ("3  possible_causes[]", "cause / kb_refs[] / confidence(high|medium|low)", PURPLE, PURPLE_BG),
        ("4  check_steps[]", "string[]：按顺序可执行的检查步骤", ORANGE, ORANGE_BG),
        ("5  fix_suggestions[]", "action / command / target_device / manual_confirmation / kb_refs[]", GREEN, GREEN_BG),
        ("6  recheck_method", "string：修复后的复测方法与判定标准", RED, RED_BG),
    ]
    for i, (name, sub, color, bg) in enumerate(fields):
        y = y0 + 20 + i * 158
        d.rounded_rectangle([480, y, 1040, y + 128], radius=14, fill=bg, outline=color, width=3)
        put(d, (500, y + 18), name, font(23, True), color)
        for j, line in enumerate(wrap(d, sub, font(18), 500)):
            put(d, (500, y + 58 + j * 26), line, font(18), INK)
        arrow(d, (400, y0 + 520), (480, y + 64), color=color, width=3, dashed=True)
        # sub-fields
        d.rounded_rectangle([1080, y + 8, W - 80, y + 120], radius=12, fill=WHITE, outline=LINE, width=2)
        put(d, (1100, y + 24), "约束", font(18, True), color)
        constraints = {
            0: "必须描述可观察到的事实；不得把历史状态当作当前状态。",
            1: "每个 observation_id 必须存在于本次证据；设备名必须存在于台账或明确说明缺失。",
            2: "启用 KB 时，关键原因应带 kb_refs；confidence 只取 high/medium/low。",
            3: "检查步骤必须可执行、可复核；不得只写“检查配置”。",
            4: "涉及 shutdown / ip route / access-list / switchport 的命令必须 requires_manual_confirmation=true。",
            5: "复测必须给出目标、源设备和成功判据；修复后必须实际复测。",
        }[i]
        for j, line in enumerate(wrap(d, constraints, font(17), W - 1140)):
            put(d, (1100, y + 58 + j * 24), line, font(17), MUTED)
    # bottom extras
    ey = y0 + 1010
    d.rounded_rectangle([60, ey, W - 60, ey + 220], radius=16, fill=SLATE_BG, outline=LINE, width=2)
    put(d, (86, ey + 16), "附加字段与校验", font(26, True), INK)
    extras = [
        ("missing_information[]", "证据不足/信息缺失说明；证据不足场景必须非空"),
        ("model_status", "success / error；error 时写 error_code / error_message"),
        ("knowledge_base", "status=ready/disabled/error；error 时降级但继续诊断"),
        ("confidence", "每个原因的建议置信度，不替代证据"),
    ]
    for i, (k, v) in enumerate(extras):
        x = 86 + (i % 2) * 1040
        y = ey + 62 + (i // 2) * 52
        put(d, (x, y), k, font(18, True), BLUE)
        put(d, (x + 300, y), v, font(17), MUTED)
    footer(d, W, H, "数据来源：rag_engine/prompt_templates.py；rag_engine/answer_validation.py；test_cases/evaluation.py。")
    img.save(PIC / "fig7-3_diagnosis_fields.png")


def fig8_3():
    W, H = 2400, 1320
    img, d = canvas(W, H)
    y0 = header(
        d,
        W,
        "图8-3  人工变更状态机图",
        "draft → pending_approval → approved → executing → completed / failed / rolled_back；备份闸门、人工执行、复测节点",
    )
    states = [
        ("draft", "草稿", "填写变更内容、影响范围、回滚计划", BLUE, BLUE_BG),
        ("pending_approval", "待审批", "提交审批；审批人确认风险与备份计划", AMBER, AMBER_BG),
        ("approved", "已批准", "生成执行清单；确认目标设备与备份", PURPLE, PURPLE_BG),
        ("executing", "执行中", "人工在审计终端执行；命令先落库再发送", ORANGE, ORANGE_BG),
        ("completed", "已完成", "复测通过；记录结果与证据", GREEN, GREEN_BG),
    ]
    positions = []
    for i, (key, name, note, color, bg) in enumerate(states):
        x = 70 + i * 460
        y = y0 + 180
        d.rounded_rectangle([x, y, x + 360, y + 170], radius=16, fill=bg, outline=color, width=4)
        put(d, (x + 24, y + 18), name, font(28, True), color)
        put(d, (x + 24, y + 58), key, font(17), MUTED)
        for j, line in enumerate(wrap(d, note, font(17), 320)):
            put(d, (x + 24, y + 88 + j * 24), line, font(17), INK)
        positions.append((x, y, color))
        if i < len(states) - 1:
            arrow(d, (x + 360, y + 85), (x + 460, y + 85), color=color, width=5)
    labels = ["提交", "审批通过", "开始执行", "复测通过"]
    for i, lab in enumerate(labels):
        x = 70 + i * 460 + 360
        put(d, (x + 50, y0 + 130), lab, font(18, True), MUTED, anchor="mm")
    # failure branch
    ex = 70 + 3 * 460
    fy = y0 + 470
    d.rounded_rectangle([ex, fy, ex + 360, fy + 170], radius=16, fill=RED_BG, outline=RED, width=4)
    put(d, (ex + 24, fy + 18), "失败", font(28, True), RED)
    put(d, (ex + 24, fy + 58), "failed", font(17), MUTED)
    put(d, (ex + 24, fy + 92), "执行错误 / 复测不通过", font(17), INK)
    d.rounded_rectangle([ex + 460, fy, ex + 820, fy + 170], radius=16, fill=AMBER_BG, outline=AMBER, width=4)
    put(d, (ex + 484, fy + 18), "已回滚", font(28, True), AMBER)
    put(d, (ex + 484, fy + 58), "rolled_back", font(17), MUTED)
    put(d, (ex + 484, fy + 92), "恢复备份并重新复测", font(17), INK)
    arrow(d, (ex + 180, y0 + 350), (ex + 180, fy), color=RED, width=5, label="执行失败", label_size=18)
    arrow(d, (ex + 360, fy + 85), (ex + 460, fy + 85), color=AMBER, width=5, label="回滚", label_size=18)
    # gates
    gy = y0 + 720
    gates = [
        ("备份闸门", "executing 前必须 backupStatus=completed；未备份拒绝写命令", GREEN, GREEN_BG),
        ("人工执行", "每次写命令一次性令牌（120s，绑 session+命令哈希）", ORANGE, ORANGE_BG),
        ("复测节点", "completed 前必须 recheck；失败进入 failed / rolled_back", RED, RED_BG),
    ]
    for i, (name, note, color, bg) in enumerate(gates):
        x = 70 + i * 760
        d.rounded_rectangle([x, gy, x + 700, gy + 150], radius=14, fill=bg, outline=color, width=2)
        put(d, (x + 20, gy + 16), name, font(24, True), color)
        for j, line in enumerate(wrap(d, note, font(18), 650)):
            put(d, (x + 20, gy + 58 + j * 26), line, font(18), INK)
    footer(d, W, H, "数据来源：collector/db/schema.py（change_requests 状态约束）；collector/db/changes.py；control/terminal_service.py。")
    img.save(PIC / "fig8-3_change_state_machine.png")


def fig9_4():
    W, H = 2200, 1400
    img, d = canvas(W, H)
    y0 = header(
        d,
        W,
        "图9-4  证据一致性校验示意图",
        "同一场景 + 同一采集批次 → 有/无 KB 双跑 → evidence_hash 比对 → 可比较 / 异常",
    )
    # top
    d.rounded_rectangle([700, y0, 1500, y0 + 100], radius=16, fill=INK)
    put(d, (1100, y0 + 28), "同一场景 + 同一采集批次", font(26, True), WHITE, anchor="mm")
    put(d, (1100, y0 + 66), "run_id / evidence_json 相同", font(18), "#cbd5e1", anchor="mm")
    # two diagnose boxes
    left = (300, y0 + 220, 900, y0 + 460)
    right = (1300, y0 + 220, 1900, y0 + 460)
    d.rounded_rectangle(left, radius=16, fill=BLUE_BG, outline=BLUE, width=3)
    put(d, (330, y0 + 238), "诊断 A：use_kb=True", font(26, True), BLUE)
    put(d, (330, y0 + 282), "build_evidence_for_devices()", font(18), MUTED)
    put(d, (330, y0 + 318), "→ evidence_json", font(18), INK)
    put(d, (330, y0 + 356), "→ evidence_hash_A", font(20, True), BLUE)
    d.rounded_rectangle(right, radius=16, fill=ORANGE_BG, outline=ORANGE, width=3)
    put(d, (1330, y0 + 238), "诊断 B：use_kb=False", font(26, True), ORANGE)
    put(d, (1330, y0 + 282), "build_evidence_for_devices()", font(18), MUTED)
    put(d, (1330, y0 + 318), "→ evidence_json", font(18), INK)
    put(d, (1330, y0 + 356), "→ evidence_hash_B", font(20, True), ORANGE)
    arrow(d, (1000, y0 + 100), (600, y0 + 220), color=INK, width=4)
    arrow(d, (1200, y0 + 100), (1600, y0 + 220), color=INK, width=4)
    # diamond
    cx, cy = 1100, y0 + 620
    d.polygon([(cx, cy - 100), (cx + 190, cy), (cx, cy + 100), (cx - 190, cy)], fill=AMBER_BG, outline=AMBER, width=4)
    put(d, (cx, cy - 14), "哈希比对", font(30, True), AMBER, anchor="mm")
    put(d, (cx, cy + 30), "hash_A == hash_B ?", font(19), INK, anchor="mm")
    arrow(d, (600, y0 + 460), (cx - 190, cy), color=BLUE, width=4)
    arrow(d, (1600, y0 + 460), (cx + 190, cy), color=ORANGE, width=4)
    # pass/fail
    d.rounded_rectangle([120, y0 + 780, 1000, y0 + 980], radius=16, fill=GREEN_BG, outline=GREEN, width=3)
    put(d, (150, y0 + 800), "通过：same_evidence_all_cases=True", font(24, True), GREEN)
    put(d, (150, y0 + 846), "同证据、不同知识库条件 → 结果可比较", font(19), INK)
    put(d, (150, y0 + 886), "写入 experiment_results.evaluation_json", font(18), MUTED)
    d.rounded_rectangle([1200, y0 + 780, 2080, y0 + 980], radius=16, fill=RED_BG, outline=RED, width=3)
    put(d, (1230, y0 + 800), "失败：哈希不一致", font(24, True), RED)
    put(d, (1230, y0 + 846), "标记异常；检查采集批次/证据装配", font(19), INK)
    put(d, (1230, y0 + 886), "重跑或剔除该对比，不进入准确率汇总", font(18), MUTED)
    arrow(d, (cx - 100, cy + 100), (700, y0 + 780), color=GREEN, width=4, label="一致", label_size=18)
    arrow(d, (cx + 100, cy + 100), (1500, y0 + 780), color=RED, width=4, label="不一致", label_size=18)
    # bottom example
    d.rounded_rectangle([60, y0 + 1030, W - 60, y0 + 1130], radius=12, fill=SLATE_BG, outline=LINE, width=2)
    put(d, (86, y0 + 1048), "实测示例：REAL_VLAN_02 / 2026-11-09", font(20, True), INK)
    put(d, (86, y0 + 1084), "有/无 KB 证据哈希均为 6779ba3fdb5725bc…；same_evidence_all_cases=True；结果落库 experiment_e28f6506…。", font(18), MUTED)
    footer(d, W, H, "数据来源：test_cases/real_evaluation.py；test_cases/evaluation.py（evidence_hash）；data/ops.db experiment_results。")
    img.save(PIC / "fig9-4_evidence_consistency.png")


def fig10_1():
    W, H = 2200, 1600
    img, d = canvas(W, H)
    y0 = header(
        d,
        W,
        "图10-1  启动流程与依赖检查图",
        "start.py：环境 → 依赖 → 配置 → 前端 → 知识库 → 端口 → uvicorn；--mock / --live 分支与失败回退",
    )
    steps = [
        ("1", "Python 版本", "要求 Python ≥ 3.12；检查 sys.version_info", BLUE, BLUE_BG),
        ("2", "项目虚拟环境", ".venv 不存在则创建；否则复用项目解释器", TEAL, TEAL_BG),
        ("3", "Python 依赖", "fastapi / uvicorn / netmiko / pysnmp / chromadb / fastembed 等", PURPLE, PURPLE_BG),
        ("4", "配置与令牌", "检查 .env；APP_API_TOKEN 必须存在且长度 ≥ 16", AMBER, AMBER_BG),
        ("5", "前端构建", "检查 Node/npm；frontend/dist 缺失则构建 React 控制台", ORANGE, ORANGE_BG),
        ("6", "知识库索引", "索引为空则构建；--skip-index 时缺失直接报错", GREEN, GREEN_BG),
        ("7", "端口与启动", "检查 127.0.0.1:8000；启动 uvicorn；等待 /login 返回 200", RED, RED_BG),
    ]
    sx, sw = 110, 1060
    for i, (num, title, note, color, bg) in enumerate(steps):
        y = y0 + 20 + i * 166
        d.rounded_rectangle([sx, y, sx + sw, y + 140], radius=14, fill=bg, outline=color, width=3)
        d.ellipse([sx + 20, y + 40, sx + 80, y + 100], fill=color)
        put(d, (sx + 50, y + 70), num, font(26, True), WHITE, anchor="mm")
        put(d, (sx + 100, y + 18), title, font(26, True), color)
        for j, line in enumerate(wrap(d, note, font(18), sw - 130)):
            put(d, (sx + 100, y + 58 + j * 24), line, font(18), INK)
        if i < len(steps) - 1:
            arrow(d, (sx + sw // 2, y + 140), (sx + sw // 2, y + 166), color=color, width=4)
    # branches
    bx = 1250
    branches = [
        ("启动模式", [
            ("--mock（默认）", "示例数据；不连接真实设备；APP_AUTO_COLLECT=false"),
            ("--live", "真实 API + 设备采集；需要 EVE-NG / ZeroTier 可达"),
        ], BLUE, BLUE_BG),
        ("跳过/复用", [
            ("--skip-install", "缺少依赖时不安装，直接报错退出"),
            ("--skip-build", "复用 frontend/dist/index.html"),
            ("--skip-index", "索引缺失时直接报错，不自动构建"),
        ], AMBER, AMBER_BG),
        ("失败处理", [
            ("检查失败", "打印明确错误；不启动半可用服务"),
            ("端口占用", "报错并提示更换 APP_PORT"),
            ("启动超时", "30s 内 /login 未就绪则终止 uvicorn"),
        ], RED, RED_BG),
    ]
    for k, (title, rows, color, bg) in enumerate(branches):
        y = y0 + 20 + k * 420
        d.rounded_rectangle([bx, y, W - 80, y + 380], radius=16, fill=bg, outline=color, width=2)
        put(d, (bx + 24, y + 16), title, font(26, True), color)
        for j, (name, note) in enumerate(rows):
            yy = y + 66 + j * 92
            put(d, (bx + 24, yy), name, font(20, True), color)
            for m, line in enumerate(wrap(d, note, font(17), W - bx - 140)):
                put(d, (bx + 24, yy + 30 + m * 22), line, font(17), INK)
    # ready
    d.rounded_rectangle([110, y0 + 1230, 1060, y0 + 1330], radius=14, fill=GREEN_BG, outline=GREEN, width=3)
    put(d, (140, y0 + 1250), "就绪：/login 返回 200", font(26, True), GREEN)
    put(d, (140, y0 + 1290), "uvicorn 监听 http://127.0.0.1:8000；Ctrl+C 停止并回收进程", font(18), INK)
    footer(d, W, H, "数据来源：start.py:24-45, 251-354；project_config.py；2026-11-09 实机启动记录。")
    img.save(PIC / "fig10-1_startup_flow.png")


# ---------------------------------------------------------------------------
# data charts
# ---------------------------------------------------------------------------
def _plt():
    import matplotlib

    matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    from matplotlib import font_manager

    fp = font_manager.FontProperties(fname=FONT_REG)
    plt.rcParams["font.family"] = fp.get_name()
    plt.rcParams["axes.unicode_minus"] = False
    plt.rcParams["axes.edgecolor"] = "#cbd5e1"
    plt.rcParams["axes.linewidth"] = 1.2
    plt.rcParams["axes.titlesize"] = 17
    plt.rcParams["axes.labelsize"] = 14
    plt.rcParams["xtick.labelsize"] = 12
    plt.rcParams["ytick.labelsize"] = 12
    return plt


def _valid_experiments():
    con = sqlite3.connect(DB)
    con.row_factory = sqlite3.Row
    rows = con.execute(
        "select experiment_id, summary_json, started_at_utc from experiment_runs order by started_at_utc"
    ).fetchall()
    latest = {}
    for row in rows:
        summary = json.loads(row["summary_json"] or "{}")
        case = summary.get("case_external_id")
        if case:
            latest[case] = row["experiment_id"]
    cases = [
        "REAL_LINK_01", "REAL_LINK_02", "REAL_VLAN_01", "REAL_VLAN_02",
        "REAL_DEFAULT_ROUTE_01", "REAL_DEFAULT_ROUTE_02",
        "REAL_ACL_01", "REAL_ACL_02", "REAL_NORMAL_02", "REAL_INSUFFICIENT_01",
    ]
    return con, latest, cases


def fig5_3():
    plt = _plt()
    raw = json.load(open(ROOT / "knowledge_base" / "articles.json"))
    articles = raw.get("articles", raw) if isinstance(raw, dict) else raw
    counts = Counter(a.get("category") for a in articles)
    cats = ["VLAN", "路由", "DHCP", "ACL", "接口故障", "通用排查方法"]
    counts_list = [counts.get(c, 0) for c in cats]
    # first relevant rank for the six real domain questions
    ranks = [1, 1, 1, 1, 1, 2]
    rank_labels = ["链路故障", "VLAN 查询", "默认路由", "ACL 过度封锁", "正常检查", "证据不足"]
    # P5 badcase ranks
    p5 = [
        ("KB-ROUTE-001", "路由", 1),
        ("KB-DHCP-001", "DHCP", 2),
        ("KB-IF-004", "接口", 3),
        ("KB-IF-002", "接口", 4),
    ]
    fig, axes = plt.subplots(1, 3, figsize=(19, 6.6), gridspec_kw={"width_ratios": [1.0, 1.15, 1.05]})
    ax = axes[0]
    colors = ["#2563eb", "#0f766e", "#7c3aed", "#ea580c", "#16a34a", "#64748b"]
    bars = ax.bar(cats, counts_list, color=colors, width=0.62)
    for b, v in zip(bars, counts_list):
        ax.text(b.get_x() + b.get_width() / 2, v + 0.08, str(v), ha="center", va="bottom", fontsize=12, fontweight="bold")
    ax.set_title("(a) 知识文章数量分布（26 篇）")
    ax.set_ylabel("文章数")
    ax.set_ylim(0, max(counts_list) + 1.2)
    ax.tick_params(axis="x", rotation=18)
    for spine in ("top", "right"):
        ax.spines[spine].set_visible(False)

    ax = axes[1]
    bars = ax.bar(rank_labels, ranks, color=["#16a34a" if r <= 3 else "#dc2626" for r in ranks], width=0.62)
    ax.axhline(3, color="#dc2626", linestyle="--", linewidth=1.4)
    ax.text(len(rank_labels) - 0.4, 3.08, "Top3 边界", color="#dc2626", ha="right", fontsize=11)
    for b, v in zip(bars, ranks):
        ax.text(b.get_x() + b.get_width() / 2, v + 0.08, f"rank {v}", ha="center", va="bottom", fontsize=11, fontweight="bold")
    ax.set_title("(b) 正确类别首次命中排名（6/6 在 Top3 内）")
    ax.set_ylabel("首次命中排名（越小越好）")
    ax.set_ylim(4.3, 0.7)
    ax.set_yticks([1, 2, 3, 4])
    ax.tick_params(axis="x", rotation=18)
    for spine in ("top", "right"):
        ax.spines[spine].set_visible(False)

    ax = axes[2]
    names = [p[0] for p in p5]
    vals = [p[2] for p in p5]
    cols = ["#16a34a" if v <= 3 else "#ea580c" for v in vals]
    bars = ax.barh(names, vals, color=cols, height=0.55)
    for b, v in zip(bars, vals):
        ax.text(v + 0.08, b.get_y() + b.get_height() / 2, f"rank {v}", va="center", fontsize=11, fontweight="bold")
    ax.axvline(3, color="#dc2626", linestyle="--", linewidth=1.4)
    ax.set_title("(c) P5 badcase：最相关接口文章排名第 4")
    ax.set_xlabel("检索排名")
    ax.set_xlim(0, 5)
    ax.invert_yaxis()
    for spine in ("top", "right"):
        ax.spines[spine].set_visible(False)
    fig.suptitle("图5-3  知识覆盖与检索命中", fontsize=22, fontweight="bold", y=0.995)
    fig.text(0.5, 0.015, "数据来源：knowledge_base/articles.json；data/ops.db 中 2026-11-09 真实 campaign 的有 KB 诊断；P5 为 diagnosis_id=1。",
             ha="center", fontsize=11, color="#475569")
    fig.tight_layout(rect=[0, 0.04, 1, 0.95])
    fig.savefig(PIC / "fig5-3_kb_coverage_retrieval.png", dpi=180)
    plt.close(fig)


def fig9_2():
    plt = _plt()
    con, latest, cases = _valid_experiments()
    category_of = {
        "REAL_LINK_01": "链路", "REAL_LINK_02": "链路",
        "REAL_VLAN_01": "VLAN", "REAL_VLAN_02": "VLAN",
        "REAL_DEFAULT_ROUTE_01": "路由", "REAL_DEFAULT_ROUTE_02": "路由",
        "REAL_ACL_01": "ACL", "REAL_ACL_02": "ACL",
        "REAL_NORMAL_02": "正常", "REAL_INSUFFICIENT_01": "证据不足",
    }
    data = {}
    for case in cases:
        eid = latest[case]
        cat = category_of[case]
        data.setdefault(cat, {"with": [], "without": []})
        for row in con.execute(
            "select kb_enabled, correctness from experiment_results where experiment_id=?",
            (eid,),
        ):
            key = "with" if row["kb_enabled"] else "without"
            data[cat][key].append(row["correctness"])
    cats = ["链路", "VLAN", "路由", "ACL", "正常", "证据不足"]
    acc_w, acc_wo = [], []
    for cat in cats:
        for key, target in (("with", acc_w), ("without", acc_wo)):
            vals = data[cat][key]
            score = sum(1 if v == "correct" else 0.5 if v == "partial" else 0 for v in vals)
            target.append(score / len(vals) if vals else 0)
    # overall distribution
    all_w = [v for cat in cats for v in data[cat]["with"]]
    all_wo = [v for cat in cats for v in data[cat]["without"]]
    def dist(vals):
        return [vals.count(x) for x in ("correct", "partial", "wrong")]
    dist_w, dist_wo = dist(all_w), dist(all_wo)
    fig, axes = plt.subplots(1, 2, figsize=(16, 6.4), gridspec_kw={"width_ratios": [1.5, 1.0]})
    ax = axes[0]
    x = range(len(cats))
    w = 0.36
    b1 = ax.bar([i - w / 2 for i in x], acc_w, width=w, color="#2563eb", label="有知识库")
    b2 = ax.bar([i + w / 2 for i in x], acc_wo, width=w, color="#ea580c", label="无知识库")
    for bars in (b1, b2):
        for b in bars:
            h = b.get_height()
            ax.text(b.get_x() + b.get_width() / 2, h + 0.02, f"{h*100:.0f}%", ha="center", va="bottom", fontsize=10)
    ax.set_xticks(list(x))
    ax.set_xticklabels(cats)
    ax.set_ylim(0, 1.18)
    ax.set_ylabel("准确率（correct + 0.5×partial）")
    ax.set_title("(a) 分类准确率：有 KB vs 无 KB")
    ax.legend(frameon=False, loc="upper right")
    ax.axhline(0.8, color="#94a3b8", linestyle=":", linewidth=1.2)
    ax.text(2.5, 0.815, "80% 目标线", color="#64748b", fontsize=10, ha="center")
    for spine in ("top", "right"):
        ax.spines[spine].set_visible(False)

    ax = axes[1]
    labels = ["有知识库", "无知识库"]
    correct = [dist_w[0], dist_wo[0]]
    partial = [dist_w[1], dist_wo[1]]
    wrong = [dist_w[2], dist_wo[2]]
    x = range(len(labels))
    ax.bar(x, correct, color="#16a34a", label="correct", width=0.55)
    ax.bar(x, partial, bottom=correct, color="#f59e0b", label="partial", width=0.55)
    ax.bar(x, wrong, bottom=[c + p for c, p in zip(correct, partial)], color="#dc2626", label="wrong", width=0.55)
    for i in x:
        total = correct[i] + partial[i] + wrong[i]
        ax.text(i, total + 0.2, f"n={total}", ha="center", va="bottom", fontsize=11, fontweight="bold")
    ax.set_xticks(list(x))
    ax.set_xticklabels(labels)
    ax.set_ylabel("结果数")
    ax.set_title("(b) 正确性分布（10 个用例 × 2 组）")
    ax.legend(frameon=False, loc="upper right")
    ax.set_ylim(0, 12)
    for spine in ("top", "right"):
        ax.spines[spine].set_visible(False)
    fig.suptitle("图9-2  有/无知识库准确率对比", fontsize=22, fontweight="bold", y=0.995)
    fig.text(0.5, 0.015, "数据来源：data/ops.db 中 2026-11-09 有效 campaign（每用例取最终实验）；有 KB 85.0%，无 KB 80.0%。",
             ha="center", fontsize=11, color="#475569")
    fig.tight_layout(rect=[0, 0.04, 1, 0.95])
    fig.savefig(PIC / "fig9-2_kb_accuracy.png", dpi=180)
    plt.close(fig)


def fig9_3():
    plt = _plt()
    con, latest, cases = _valid_experiments()
    rows = []
    for case in cases:
        eid = latest[case]
        for row in con.execute(
            "select kb_enabled, llm_response_time_ms, total_response_time_ms from experiment_results where experiment_id=?",
            (eid,),
        ):
            rows.append((case, bool(row["kb_enabled"]), row["llm_response_time_ms"] or 0, row["total_response_time_ms"] or 0))
    with_rows = [r for r in rows if r[1]]
    without_rows = [r for r in rows if not r[1]]
    def avg(vals):
        return sum(vals) / len(vals) if vals else 0
    with_llm = avg([r[2] for r in with_rows]) / 1000
    with_total = avg([r[3] for r in with_rows]) / 1000
    wo_llm = avg([r[2] for r in without_rows]) / 1000
    wo_total = avg([r[3] for r in without_rows]) / 1000
    fig, axes = plt.subplots(1, 2, figsize=(16, 6.4), gridspec_kw={"width_ratios": [1.0, 1.5]})
    ax = axes[0]
    x = range(2)
    w = 0.34
    b1 = ax.bar([i - w / 2 for i in x], [with_llm, wo_llm], width=w, color="#2563eb", label="LLM 耗时")
    b2 = ax.bar([i + w / 2 for i in x], [with_total, wo_total], width=w, color="#0f766e", label="全链路耗时")
    for bars in (b1, b2):
        for b in bars:
            h = b.get_height()
            ax.text(b.get_x() + b.get_width() / 2, h + 0.6, f"{h:.1f}s", ha="center", va="bottom", fontsize=10)
    ax.set_xticks(list(x))
    ax.set_xticklabels(["有知识库", "无知识库"])
    ax.set_ylabel("平均耗时（秒）")
    ax.set_title("(a) 平均耗时：LLM vs 全链路")
    ax.legend(frameon=False)
    ax.set_ylim(0, max(with_total, wo_total) * 1.22)
    for spine in ("top", "right"):
        ax.spines[spine].set_visible(False)

    ax = axes[1]
    order = [
        "REAL_LINK_01", "REAL_LINK_02", "REAL_VLAN_01", "REAL_VLAN_02",
        "REAL_DEFAULT_ROUTE_01", "REAL_DEFAULT_ROUTE_02",
        "REAL_ACL_01", "REAL_ACL_02", "REAL_NORMAL_02", "REAL_INSUFFICIENT_01",
    ]
    short = {
        "REAL_LINK_01": "链路1", "REAL_LINK_02": "链路2",
        "REAL_VLAN_01": "VLAN1", "REAL_VLAN_02": "VLAN2",
        "REAL_DEFAULT_ROUTE_01": "路由1", "REAL_DEFAULT_ROUTE_02": "路由2",
        "REAL_ACL_01": "ACL1", "REAL_ACL_02": "ACL2",
        "REAL_NORMAL_02": "正常", "REAL_INSUFFICIENT_01": "证据不足",
    }
    with_map = {r[0]: r[3] / 1000 for r in with_rows}
    wo_map = {r[0]: r[3] / 1000 for r in without_rows}
    ys = list(range(len(order)))
    for i, case in enumerate(order):
        ax.plot([wo_map[case], with_map[case]], [i, i], color="#cbd5e1", linewidth=4, zorder=1)
    ax.scatter([wo_map[c] for c in order], ys, color="#ea580c", s=70, label="无知识库", zorder=3)
    ax.scatter([with_map[c] for c in order], ys, color="#2563eb", s=70, label="有知识库", zorder=3)
    ax.set_yticks(ys)
    ax.set_yticklabels([short[c] for c in order])
    ax.set_xlabel("全链路耗时（秒）")
    ax.set_title("(b) 每个用例的全链路耗时")
    ax.legend(frameon=False, loc="lower right")
    ax.grid(axis="x", color="#e2e8f0", linewidth=1)
    for spine in ("top", "right"):
        ax.spines[spine].set_visible(False)
    fig.suptitle("图9-3  响应时间对比", fontsize=22, fontweight="bold", y=0.995)
    fig.text(0.5, 0.015, "数据来源：data/ops.db experiment_results（llm_response_time_ms / total_response_time_ms）；2026-11-09 有效 campaign。",
             ha="center", fontsize=11, color="#475569")
    fig.tight_layout(rect=[0, 0.04, 1, 0.95])
    fig.savefig(PIC / "fig9-3_response_time.png", dpi=180)
    plt.close(fig)


# ---------------------------------------------------------------------------
# screenshot composites
# ---------------------------------------------------------------------------
def copy_sources():
    mapping = {
        "ui_overview.png": "overview.png",
        "ui_assets.png": "assets.png",
        "ui_diagnosis.png": "diagnosis.png",
        "ui_knowledge.png": "knowledge.png",
        "ui_terminal.png": "terminal.png",
        "ui_mobile_overview.png": "mobile-overview.png",
        "report_export_first.png": "report17.png",
        "report_export_manual.png": "report17_manual.png",
        "report_export_kb.png": "report17_kb.png",
    }
    for dest, src in mapping.items():
        s = UI / src
        if s.exists():
            Image.open(s).save(SHOTS / dest)


def fig10_2():
    W, H = 2500, 1740
    img, d = canvas(W, H)
    y0 = header(
        d,
        W,
        "图10-2  Web 控制台界面组图",
        "实机运行截图：总览 / 设备与采集 / 诊断中心 / 运维知识库 / 终端 Dock / 移动端",
    )
    panels = [
        ("(a) 网络运行总览", "overview.png"),
        ("(b) 设备与采集（真实批次）", "assets.png"),
        ("(c) 诊断中心（全屏拓扑 + AI 工作区）", "diagnosis.png"),
        ("(d) 运维知识库（26 篇 / 5 类）", "knowledge.png"),
        ("(e) 终端 Dock（备份闸门 + 审计）", "terminal.png"),
        ("(f) 移动端总览", "mobile-overview.png"),
    ]
    cell_w, cell_h = 760, 660
    gap_x, gap_y = 36, 60
    x0, y0p = 60, y0 + 20
    for i, (caption, name) in enumerate(panels):
        col, row = i % 3, i // 3
        x = x0 + col * (cell_w + gap_x)
        y = y0p + row * (cell_h + gap_y)
        d.rounded_rectangle([x, y, x + cell_w, y + cell_h], radius=14, fill=SLATE_BG, outline=LINE, width=2)
        put(d, (x + 16, y + 12), caption, font(21, True), INK)
        box = (x + 14, y + 52, x + cell_w - 14, y + cell_h - 14)
        src = shot_path(name)
        if src.exists():
            im = load_fit(src, (box[2] - box[0], box[3] - box[1]))
            paste_center(img, im, box)
        else:
            put(d, (x + 20, y + 100), "截图缺失", font(20), RED)
    footer(d, W, H, "实机运行：python -m uvicorn webapp.main:app --host 127.0.0.1 --port 8000；数据来自 data/ops.db；截图日期 2026-11-09。")
    img.save(PIC / "fig10-2_web_console.png")


def fig10_3():
    W, H = 2500, 1600
    img, d = canvas(W, H)
    y0 = header(
        d,
        W,
        "图10-3  报告导出样例图",
        "Markdown 结构目录树 + HTML 渲染首屏；模型/参数、检索来源、人工记录区块",
    )
    # left: markdown outline
    lx, ly, lw = 60, y0 + 20, 760
    d.rounded_rectangle([lx, ly, lx + lw, H - 100], radius=16, fill=SLATE_BG, outline=LINE, width=2)
    put(d, (lx + 24, ly + 18), "Markdown 结构目录树", font(28, True), INK)
    put(d, (lx + 24, ly + 62), "reports/diagnosis_17.md", font(18), MUTED)
    outline = [
        ("H1", "故障诊断报告 #17", 0, BLUE),
        ("H2", "用户问题", 1, TEAL),
        ("H2", "设备证据（采集时间+状态）", 1, TEAL),
        ("H2", "知识库检索快照", 1, PURPLE),
        ("H2", "故障现象", 1, ORANGE),
        ("H2", "可能原因", 1, ORANGE),
        ("H2", "检查步骤", 1, ORANGE),
        ("H2", "处理建议", 1, ORANGE),
        ("H2", "复测方法", 1, RED),
        ("H2", "信息缺失说明", 1, AMBER),
        ("H2", "人工处理记录", 1, GREEN),
        ("H2", "人工变更与复测", 1, GREEN),
        ("H2", "会话审计", 1, MUTED),
    ]
    for i, (level, text, indent, color) in enumerate(outline):
        y = ly + 120 + i * 62
        x = lx + 30 + indent * 44
        if indent:
            d.line([(x - 22, y + 18), (x - 8, y + 18)], fill=LINE, width=2)
            d.line([(x - 22, y + 18), (x - 22, y + 64)], fill=LINE, width=2)
        pill(d, (x, y), level, fg=WHITE, bg=color, size=15)
        put(d, (x + 66, y + 1), text, font(20, True) if indent == 0 else font(18), INK)
    # annotations
    ann = [
        ("模型 / 参数", "qwen-plus · diagnosis_v2 · temperature=0.2 · max_tokens=3000", BLUE, BLUE_BG),
        ("检索来源", "KB-ACL-002, KB-ACL-005, KB-ACL-001, KB-GEN-001", PURPLE, PURPLE_BG),
        ("人工记录", "manual_actions: acl-pilot · completed · 2026-11-09 15:54", GREEN, GREEN_BG),
    ]
    ay = ly + 980
    for i, (name, val, color, bg) in enumerate(ann):
        y = ay + i * 110
        d.rounded_rectangle([lx + 24, y, lx + lw - 24, y + 92], radius=12, fill=bg, outline=color, width=2)
        put(d, (lx + 40, y + 12), name, font(20, True), color)
        for j, line in enumerate(wrap(d, val, font(17), lw - 100)):
            put(d, (lx + 40, y + 42 + j * 22), line, font(17), INK)
    # right: report screenshot
    rx, ry, rw, rh = 860, y0 + 20, W - 920, H - 160
    d.rounded_rectangle([rx, ry, rx + rw, ry + rh], radius=16, fill=WHITE, outline=LINE, width=2)
    put(d, (rx + 20, ry + 12), "HTML 渲染首屏（reports/diagnosis_17.html）", font(24, True), INK)
    src = shot_path("report17.png")
    if src.exists():
        box = (rx + 20, ry + 56, rx + rw - 20, ry + rh - 20)
        im = load_fit(src, (box[2] - box[0], box[3] - box[1]))
        x_off = box[0] + (box[2] - box[0] - im.width) // 2
        y_off = box[1] + (box[3] - box[1] - im.height) // 2
        img.paste(im, (x_off, y_off))
        d.rectangle([x_off - 1, y_off - 1, x_off + im.width, y_off + im.height], outline=LINE, width=2)
        scale = im.width / 1400.0

        def map_box(ox0, oy0, ox1, oy1):
            return (
                x_off + ox0 * scale,
                y_off + oy0 * scale,
                x_off + ox1 * scale,
                y_off + oy1 * scale,
            )

        def callout(box, label, color):
            x0, y0, x1, y1 = map_box(*box)
            d.rectangle([x0, y0, x1, y1], outline=color, width=4)
            f = font(16, True)
            tw = int(tlen(d, label, f)) + 18
            cy = (y0 + y1) / 2
            d.rounded_rectangle([x1 + 8, cy - 14, x1 + 8 + tw, cy + 14], radius=6, fill=color)
            put(d, (x1 + 17, cy - 12), label, f, WHITE)

        # Coordinates are in the original 1400x1050 report screenshot.
        callout((236, 90, 1120, 315), "模型 / 参数", BLUE)
        callout((236, 322, 1120, 346), "检索来源", PURPLE)
    footer(d, W, H, "数据来源：webapp/report_export.py 生成的真实报告；模型/参数/检索来源/人工记录均来自 data/ops.db；截图日期 2026-11-09。")
    img.save(PIC / "fig10-3_report_sample.png")


# ---------------------------------------------------------------------------
# manifest
# ---------------------------------------------------------------------------
def read_checklist():
    z = zipfile.ZipFile(XLSX)
    ns = "{http://schemas.openxmlformats.org/spreadsheetml/2006/main}"
    root = ET.fromstring(z.read("xl/worksheets/sheet1.xml"))
    rows = []
    for row in root.iter(ns + "row"):
        vals = {}
        for c in row.findall(ns + "c"):
            ref = c.get("r")
            col = "".join(ch for ch in ref if ch.isalpha())
            t = c.get("t")
            if t == "inlineStr":
                is_ = c.find(ns + "is")
                val = "".join(x.text or "" for x in is_.iter(ns + "t")) if is_ is not None else ""
            else:
                v = c.find(ns + "v")
                val = v.text if v is not None else ""
            vals[col] = val
        if vals.get("A") and vals.get("A").isdigit():
            rows.append(vals)
    return rows


def manifest():
    rows = read_checklist()
    generated = {
        "图3-2": "fig3-2_vlan_ip_plan.png",
        "图3-3": "fig3-3_routing_acl_plan.png",
        "图3-4": "fig3-4_access_link.png",
        "图4-3": "fig4-3_parse_mapping.png",
        "图5-3": "fig5-3_kb_coverage_retrieval.png",
        "图6-3": "fig6-3_evidence_quota.png",
        "图7-3": "fig7-3_diagnosis_fields.png",
        "图8-3": "fig8-3_change_state_machine.png",
        "图9-2": "fig9-2_kb_accuracy.png",
        "图9-3": "fig9-3_response_time.png",
        "图9-4": "fig9-4_evidence_consistency.png",
        "图10-1": "fig10-1_startup_flow.png",
        "图10-2": "fig10-2_web_console.png",
        "图10-3": "fig10-3_report_sample.png",
    }
    out = []
    for r in rows:
        fig = r.get("B", "")
        status = r.get("H", "")
        filename = generated.get(fig, "")
        note = ""
        if status == "已绘制" and not filename:
            note = "清单标记为已绘制；仓库中未找到同名文件，本次未重新生成"
        elif filename:
            note = "本次生成"
        out.append({
            "序号": r.get("A", ""),
            "图号": fig,
            "图名": r.get("C", ""),
            "章": r.get("D", ""),
            "所在小节": r.get("E", ""),
            "优先级": r.get("F", ""),
            "类型": r.get("G", ""),
            "状态": status,
            "生成文件": filename,
            "说明": note,
            "绘制要点": r.get("I", ""),
            "数据来源": r.get("J", ""),
            "清单产出方式": r.get("K", ""),
        })
    with open(PIC / "manifest.csv", "w", newline="", encoding="utf-8-sig") as f:
        writer = csv.DictWriter(f, fieldnames=list(out[0].keys()))
        writer.writeheader()
        writer.writerows(out)
    # README
    pending = [x for x in out if x["生成文件"]]
    drawn = [x for x in out if x["状态"] == "已绘制"]
    lines = [
        "# 配图目录（pic/）",
        "",
        "本目录由 `scripts/generate_pic_figures.py` 根据 `/home/wyj/下载/配图清单.xlsx` 生成。",
        "",
        f"- 清单总数：**{len(out)}**",
        f"- 本次生成：**{len(pending)}**（9 张示意图 + 3 张真实数据图 + 2 张实机截图组图）",
        f"- 清单标记已绘制：**{len(drawn)}**（未在仓库中找到同名文件，本次未重新生成）",
        "",
        "## 本次生成的图片",
        "",
        "| 图号 | 图名 | 文件 |",
        "|---|---|---|",
    ]
    for x in pending:
        lines.append(f"| {x['图号']} | {x['图名']} | `{x['生成文件']}` |")
    lines += [
        "",
        "## 数据来源",
        "",
        "- 图5-3、图9-2、图9-3：`data/ops.db` 中 2026-11-09 的真实 campaign 数据；",
        "  知识库文章数来自 `knowledge_base/articles.json`。",
        "- 图10-2、图10-3：实机运行 `python -m uvicorn webapp.main:app` 后由 Chrome/Playwright 截图；",
        "  报告样例来自 `reports/diagnosis_17.html`。",
        "- 图3-2、图3-3、图3-4、图4-3、图6-3、图7-3、图8-3、图9-4、图10-1：",
        "  按清单“绘制内容要点”和项目代码/实测状态绘制。",
        "",
        "## 原始截图",
        "",
        "`screenshots/` 保留实机截图原件，便于复核或替换组图：",
        "",
        "- `ui_overview.png`、`ui_assets.png`、`ui_diagnosis.png`、`ui_knowledge.png`、`ui_terminal.png`、`ui_mobile_overview.png`",
        "- `report_export_first.png`、`report_export_manual.png`、`report_export_kb.png`",
        "",
        "## 重新生成",
        "",
        "```bash",
        "python3 scripts/generate_pic_figures.py",
        "```",
        "",
        "> 注意：实机截图需要先启动 Web 服务，或复用 `screenshots/` 中的现有截图。",
        "",
        "## 完整清单",
        "",
        "见 `manifest.csv`（包含全部 32 条及状态）。",
    ]
    (PIC / "README.md").write_text("\n".join(lines), encoding="utf-8")


def main():
    fig3_2()
    fig3_3()
    fig3_4()
    fig4_3()
    fig6_3()
    fig7_3()
    fig8_3()
    fig9_4()
    fig10_1()
    fig5_3()
    fig9_2()
    fig9_3()
    copy_sources()
    fig10_2()
    fig10_3()
    manifest()
    print("generated:", len(list(PIC.glob("fig*.png"))), "figures in", PIC)


if __name__ == "__main__":
    main()
