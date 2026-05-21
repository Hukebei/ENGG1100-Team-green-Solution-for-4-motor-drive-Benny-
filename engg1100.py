# ============================================================
# Mac BLE Control Dashboard for ENGG1100 Prototype
# ============================================================
# 呢个 Python 程序係 Mac 端控制面板。
#
# 主要功能：
# 1. 用 BLE 连接 Arduino 上面个 HMSoft / BLE module
# 2. 发送控制指令俾 Arduino
#    例如：LF_IN、AUTO_ON、ALLS、CALIBRATE
# 3. 接收 Arduino 回传嘅姿态数据
#    例如：X: 10  Y: -20  Z: 5  Status: Level
# 4. 将姿态数据画成一个简单 3D 平台图
# 5. 提供手动控制、自稳控制、速度选择、动作时间选择、紧急停止
#
# 运行前要安装 bleak：
# python3 -m pip install bleak
#
# 如果 BLE 连唔到：
# 1. 先确认 Arduino 有供电
# 2. 确认 BLE 模块灯有闪
# 3. 确认 DEVICE_ADDRESS 係你扫描到嘅 HMSoft 地址
# 4. 确认冇其他 Python 程序或者手机 App 正喺连接同一个 BLE
# ============================================================


# ============================================================
# Import libraries
# ============================================================

import asyncio
# asyncio 用嚟处理 BLE 异步任务。
# BLE 连接、发送、接收都唔係即刻完成，所以用 async/await 比较稳定。

import threading
# threading 用嚟开一个后台线程跑 BLE。
# 因为 Tkinter UI 要喺主线程运行，如果 BLE 阻塞主线程，界面会卡死。

import tkinter as tk
# tkinter 係 Python 内建 GUI 库。
# 呢个 UI 入面嘅窗口、按钮、文字、3D 示意图都係用 tkinter 做。

from tkinter import messagebox
# messagebox 用嚟弹出错误提示。
# 例如 BLE 未连接、发送失败，就会弹窗。

from bleak import BleakClient
# bleak 係 Python BLE 库。
# BleakClient 用嚟连接 BLE device、写入 characteristic、接收 notify。


# ============================================================
# BLE device information
# ============================================================

DEVICE_ADDRESS = "FB39458D-5F01-B0CE-AFA4-BC6A781B3407"
# 呢个係你 Mac 扫描到嘅 HMSoft BLE 地址。
# 如果你换咗 BLE 模块，或者 Mac 重新识别地址变咗，就要改呢一行。
#
# 点样搵地址：
# 跑 ble_scan.py，睇 Name: HMSoft 后面嘅 Address。

CHAR_UUID = "0000ffe1-0000-1000-8000-00805f9b34fb"
# 呢个係 HMSoft BLE UART characteristic UUID。
# 你之前 scan services 见到：
# Service: 0000ffe0...
# Characteristic: 0000ffe1...
#
# Mac 发送指令同接收 Arduino 回传，都係通过呢个 UUID。


# ============================================================
# Global BLE variables
# ============================================================

client = None
# client 用嚟存 BLE 连接对象。
# 未连接时係 None。
# 成功连接后，client 就代表同 HMSoft 嘅 BLE connection。

loop = asyncio.new_event_loop()
# 因为 BLE 要用 asyncio，但 Tkinter 主界面唔可以直接 await。
# 所以开一个独立 event loop，放去后台线程跑。


# ============================================================
# Current sensor values
# ============================================================

current_x = 0
current_y = 0
current_z = 0
# 呢三个变量保存 Arduino 最新回传嘅姿态值。
# 主要用嚟画 3D 平台图。
# X/Y 控制平台倾斜方向，Z 暂时只显示。


# =========================
# BLE
# =========================

def ble_notification_handler(sender, data):
    """
    呢个函数会喺 Arduino 经 BLE 发数据返嚟时自动触发。

    sender:
        BLE characteristic 来源，通常唔需要理。

    data:
        Arduino 发返嚟嘅 bytes。
        例如：
        b"X: 12  Y: -35  Z: 4  Status: Front low"

    点解要 root.after？
        Tkinter UI 只可以喺主线程安全更新。
        BLE notify 可能喺后台线程触发。
        所以用 root.after(0, ...) 叫 Tkinter 主线程去更新 UI。
    """

    text = data.decode(errors="ignore").strip()
    # 将 bytes 转成 string。
    # errors="ignore" 係为了防止偶尔 BLE 数据乱码令程序崩溃。
    # strip() 去走头尾空格同换行。

    if text:
        root.after(0, update_received_text, text)
        # 如果收到文字，就交俾 update_received_text() 更新 UI。


async def ble_connect():
    """
    连接 BLE 模块。

    步骤：
    1. 用 DEVICE_ADDRESS 建立 BleakClient
    2. await client.connect()
    3. 如果连接成功，就 start_notify()
    4. 之后 Arduino 一有回传，就会进入 ble_notification_handler()

    return:
        True  = 连接成功
        False = 连接失败
    """

    global client

    client = BleakClient(DEVICE_ADDRESS)
    # 建立 BLE client，但呢一刻未连接。

    await client.connect()
    # 真正连接 BLE device。

    if client.is_connected:
        await client.start_notify(CHAR_UUID, ble_notification_handler)
        # 开启 notify。
        # 即係 Arduino 之后 BT.println() 返嚟嘅内容，
        # Mac 呢边会自动收到。

    return client.is_connected


async def ble_send(command):
    """
    经 BLE 发送一条指令俾 Arduino。

    command 例子：
        "LF_IN"
        "LF_OUT"
        "AUTO_ON"
        "AUTO_OFF"
        "ALLS"
        "CALIBRATE"

    重点：
        Arduino 端用 readStringUntil('\\n') 读取。
        所以呢度必须喺每条指令尾加 "\\n"。
    """

    global client

    if client is None or not client.is_connected:
        raise RuntimeError("BLE is not connected")
        # 如果未连接就按按钮，会弹出错误。

    await client.write_gatt_char(
        CHAR_UUID,
        (command + "\n").encode(),
        response=False
    )
    # 将 command 加换行，再 encode 成 bytes，写入 BLE characteristic。


async def ble_disconnect():
    """
    断开 BLE 连接。

    为安全起见，断开前会先发送：
    AUTO_OFF：关闭自稳
    ALLS：停止所有电机

    咁样就算你直接关窗口，都唔会令电机继续郁。
    """

    global client

    if client is not None and client.is_connected:
        try:
            await client.write_gatt_char(CHAR_UUID, b"AUTO_OFF\n", response=False)
            await client.write_gatt_char(CHAR_UUID, b"ALLS\n", response=False)
            await client.stop_notify(CHAR_UUID)
        except:
            pass
            # 关闭时如果 BLE 已经断开，呢度可能报错。
            # 但关窗口时唔需要因为呢个 crash。

        await client.disconnect()


def run_loop():
    """
    后台 asyncio loop。

    Tkinter 要占住主线程跑 UI。
    BLE async function 又需要 event loop。
    所以我哋开一个后台线程运行 loop.run_forever()。
    """

    asyncio.set_event_loop(loop)
    loop.run_forever()


threading.Thread(target=run_loop, daemon=True).start()
# 启动后台 BLE 线程。
# daemon=True 表示主程序结束时，呢个线程都会自动结束。


# =========================
# GUI helpers
# =========================

def connect_action():
    """
    Connect BLE 按钮执行嘅函数。

    因为 ble_connect() 係 async function，
    Tkinter button 唔可以直接 await。
    所以用 asyncio.run_coroutine_threadsafe()
    将 ble_connect() 丢去后台 loop 执行。
    """

    try:
        future = asyncio.run_coroutine_threadsafe(ble_connect(), loop)
        connected = future.result(timeout=15)
        # 最多等 15 秒。
        # 如果 15 秒都连接唔到，就当 timeout。

        if connected:
            connection_label.config(text="● Connected", fg="#30D158")
            add_log("Connected to HMSoft")
        else:
            connection_label.config(text="● Not connected", fg="#FF453A")
            add_log("Connection failed")

    except Exception as e:
        connection_label.config(text="● Not connected", fg="#FF453A")
        messagebox.showerror("BLE connection error", str(e))


def send_action(command):
    """
    所有按钮最终都会调用呢个函数发送指令。

    例如：
    左前收按钮 -> send_action("LF_IN")
    STOP ALL -> send_action("ALLS")
    CALIBRATE -> send_action("CALIBRATE")
    """

    try:
        future = asyncio.run_coroutine_threadsafe(ble_send(command), loop)
        future.result(timeout=5)
        # 最多等 5 秒发送完成。

        add_log("Sent: " + command)

    except Exception as e:
        messagebox.showerror("Send error", str(e))


def update_received_text(text):
    """
    处理 Arduino 回传内容。

    Arduino 回传常见格式：
        X: 10  Y: -20  Z: 5  Status: Front low

    呢个函数会：
    1. 将原文放入 Log
    2. 如果有 Status，就更新大字姿态显示
    3. 如果有 X/Y/Z，就解析成数字
    4. 用 X/Y 更新 3D 平台图
    """

    global current_x, current_y, current_z

    add_log("Arduino: " + text)

    # -------------------------
    # 处理 Status
    # -------------------------
    if "Status:" in text:
        status = text.split("Status:")[-1].strip()
        # 取 Status: 后面嘅文字。
        # 例如 "Status: Front low" -> "Front low"

        attitude_value.config(text=status)

        if status == "Level":
            attitude_value.config(fg="#30D158")
            status_card.config(bg="#11291D")
            # Level 时绿色，表示平台接近水平。
        else:
            attitude_value.config(fg="#FFD60A")
            status_card.config(bg="#332B00")
            # 唔水平时黄色，提醒需要修正。

    # -------------------------
    # 处理 X/Y/Z
    # -------------------------
    if "X:" in text and "Y:" in text and "Z:" in text:
        xyz_value.config(text=text)

        try:
            # 将：
            # "X: 10  Y: -20  Z: 5  Status: Level"
            # 变成：
            # ["X", "10", "Y", "-20", "Z", "5", "Status", "Level"]
            parts = text.replace(":", " ").split()

            x_index = parts.index("X") + 1
            y_index = parts.index("Y") + 1
            z_index = parts.index("Z") + 1

            current_x = int(parts[x_index])
            current_y = int(parts[y_index])
            current_z = int(parts[z_index])

            draw_3d_platform(current_x, current_y)
            # 用最新 X/Y 重新画 3D 平台。

        except:
            pass
            # 如果某次 BLE 数据唔完整，就直接忽略。
            # 避免 UI 因为一条坏数据崩溃。


def add_log(text):
    """
    将一行文字加入右下角 Log。
    see(tk.END) 会自动滚到最新一行。
    """

    log_box.insert(tk.END, text + "\n")
    log_box.see(tk.END)


def close_action():
    """
    用户关闭窗口时执行。

    会先尝试：
    1. AUTO_OFF
    2. ALLS
    3. disconnect BLE

    然后再关闭 GUI。
    """

    try:
        future = asyncio.run_coroutine_threadsafe(ble_disconnect(), loop)
        future.result(timeout=5)
    except:
        pass

    root.destroy()


def make_card(parent, title):
    """
    创建一个深色卡片区域。

    parent:
        卡片放喺边个 frame 入面。

    title:
        卡片标题，例如 "Speed / 速度"。
    """

    frame = tk.LabelFrame(
        parent,
        text=title,
        bg="#1C1C1E",
        fg="#F2F2F7",
        font=("Arial", 12, "bold"),
        padx=10,
        pady=8,
        labelanchor="n"
    )
    return frame


def make_button(parent, text, command, bg="#30D158", fg="#000000", width=14):
    """
    创建统一风格按钮。

    parent:
        按钮放喺边个区域。

    text:
        按钮文字。

    command:
        点击按钮后执行嘅函数。

    bg:
        背景色。

    fg:
        文字色。

    width:
        按钮宽度。
    """

    return tk.Button(
        parent,
        text=text,
        command=command,
        bg=bg,
        fg=fg,
        activebackground="#3A3A3C",
        activeforeground="#FFFFFF",
        relief="flat",
        bd=0,
        width=width,
        height=2,
        font=("Arial", 11, "bold")
    )


def select_time(buttons, selected_button):
    """
    时间按钮选中效果。

    先将同组所有时间按钮变回灰色，
    再将当前按钮变蓝色。
    """

    for b in buttons:
        b.config(bg="#2C2C2E")
    selected_button.config(bg="#0A84FF")


def select_speed(buttons, selected_button):
    """
    速度按钮选中效果。

    先将 LOW/MID/HIGH 变回灰色，
    再将选中按钮变绿色。
    """

    for b in buttons:
        b.config(bg="#2C2C2E")
    selected_button.config(bg="#30D158")


# =========================
# 3D display
# =========================

def draw_3d_platform(x, y):
    """
    用 Tkinter Canvas 画一个假 3D 平台。

    x / y：
        Arduino 回传嘅校准后倾斜数值。

    你当前标定逻辑：
        X negative = front low
        X positive = back low
        Y positive = right low
        Y negative = left low

    视觉效果：
        用四个角嘅高度变化模拟平台倾斜。
        呢个唔係真正 3D engine，但足够展示状态。
    """

    canvas_3d.delete("all")
    # 每次重画前，先清空旧图。

    cx = 190
    cy = 115
    # 3D 图中心点。

    w = 180
    h = 90
    # 平台视觉宽度同高度。

    x_tilt = max(min(x / 14, 40), -40)
    y_tilt = max(min(y / 14, 40), -40)
    # 将真实 X/Y 缩放成画面用嘅偏移量。
    # max/min 限制最大倾斜幅度，避免图形飞出画布。

    # Calibration:
    # X negative = front low
    # X positive = back low
    # Y positive = right low
    # Y negative = left low
    front_drop = -x_tilt
    back_drop = x_tilt
    right_drop = y_tilt
    left_drop = -y_tilt

    # 四个角：
    # fl = front left
    # fr = front right
    # br = back right
    # bl = back left
    fl = (cx - w / 2 - 25, cy - h / 2 + front_drop + left_drop)
    fr = (cx + w / 2 - 25, cy - h / 2 + front_drop + right_drop)
    br = (cx + w / 2 + 25, cy + h / 2 + back_drop + right_drop)
    bl = (cx - w / 2 + 25, cy + h / 2 + back_drop + left_drop)

    points = [fl, fr, br, bl]

    # 阴影
    shadow = []
    for px, py in points:
        shadow.extend((px + 12, py + 18))

    # 平台主体 polygon 点
    flat_points = []
    for p in points:
        flat_points.extend(p)

    canvas_3d.create_polygon(shadow, fill="#3A3A3C", outline="")
    canvas_3d.create_polygon(flat_points, fill="#64D2FF", outline="#0A84FF", width=3)

    # 对角线，方便睇平台变形/倾斜
    canvas_3d.create_line(fl[0], fl[1], br[0], br[1], fill="#0A84FF", dash=(4, 3))
    canvas_3d.create_line(fr[0], fr[1], bl[0], bl[1], fill="#0A84FF", dash=(4, 3))

    # 四角标签
    canvas_3d.create_text(fl[0] - 22, fl[1] - 10, text="LF", fill="#FFFFFF", font=("Arial", 10, "bold"))
    canvas_3d.create_text(fr[0] + 22, fr[1] - 10, text="RF", fill="#FFFFFF", font=("Arial", 10, "bold"))
    canvas_3d.create_text(br[0] + 24, br[1] + 10, text="RR", fill="#FFFFFF", font=("Arial", 10, "bold"))
    canvas_3d.create_text(bl[0] - 24, bl[1] + 10, text="RL", fill="#FFFFFF", font=("Arial", 10, "bold"))

    canvas_3d.create_text(cx, 24, text="3D Platform Attitude", fill="#F2F2F7", font=("Arial", 13, "bold"))
    canvas_3d.create_text(cx, 215, text=f"X={x}   Y={y}", fill="#A1A1AA", font=("Menlo", 11))


# =========================
# Root window
# =========================

root = tk.Tk()
# 创建主窗口。

root.title("ENGG1100 Control Dashboard")
root.geometry("1000x760")
root.configure(bg="#111113")
root.minsize(900, 650)
# 设置窗口标题、大小、背景色、最小尺寸。

root.protocol("WM_DELETE_WINDOW", close_action)
# 用户按关闭窗口时，会先执行 close_action()，确保电机停止。


# =========================
# Header
# =========================

header = tk.Frame(root, bg="#111113")
header.pack(fill="x", padx=18, pady=(14, 8))

title_label = tk.Label(
    header,
    text="ENGG1100 Stabilisation Control Dashboard",
    bg="#111113",
    fg="#F2F2F7",
    font=("Arial", 22, "bold")
)
title_label.pack(side="left")

connection_label = tk.Label(
    header,
    text="● Not connected",
    bg="#111113",
    fg="#FF453A",
    font=("Arial", 13, "bold")
)
connection_label.pack(side="right", padx=10)

connect_btn = make_button(
    header,
    "Connect BLE",
    connect_action,
    bg="#0A84FF",
    width=14
)
connect_btn.pack(side="right", padx=10)


# =========================
# Scrollable main layout
# =========================
# 因为内容比较多，窗口细嘅时候会显示唔晒。
# 所以用 Canvas + Scrollbar 做可滚动区域。
# =========================

outer_frame = tk.Frame(root, bg="#111113")
outer_frame.pack(fill="both", expand=True)

scroll_canvas = tk.Canvas(
    outer_frame,
    bg="#111113",
    highlightthickness=0
)
scroll_canvas.pack(side="left", fill="both", expand=True)

scrollbar = tk.Scrollbar(
    outer_frame,
    orient="vertical",
    command=scroll_canvas.yview
)
scrollbar.pack(side="right", fill="y")

scroll_canvas.configure(yscrollcommand=scrollbar.set)

main = tk.Frame(scroll_canvas, bg="#111113")

main_window = scroll_canvas.create_window(
    (0, 0),
    window=main,
    anchor="nw"
)

def update_scroll_region(event=None):
    """
    当 main 入面内容尺寸变化时，更新 scroll region。
    如果唔做呢步，滚动条唔知道内容实际有几高。
    """
    scroll_canvas.configure(scrollregion=scroll_canvas.bbox("all"))

def resize_main_width(event):
    """
    当窗口宽度改变时，让 main frame 跟住 canvas 宽度变。
    """
    scroll_canvas.itemconfig(main_window, width=event.width)

main.bind("<Configure>", update_scroll_region)
scroll_canvas.bind("<Configure>", resize_main_width)

def on_mousewheel(event):
    """
    鼠标滚轮 / Mac 触控板滚动。
    如果方向反咗，可以将 -1 改成 1。
    """
    scroll_canvas.yview_scroll(int(-1 * (event.delta / 120)), "units")

root.bind_all("<MouseWheel>", on_mousewheel)

left_col = tk.Frame(main, bg="#111113")
left_col.grid(row=0, column=0, sticky="nsew", padx=(18, 10), pady=8)

right_col = tk.Frame(main, bg="#111113")
right_col.grid(row=0, column=1, sticky="nsew", padx=(10, 18), pady=8)

main.grid_columnconfigure(0, weight=3)
main.grid_columnconfigure(1, weight=2)


# =========================
# Left: Attitude
# =========================

status_card = make_card(left_col, "Platform Attitude / 平台姿态")
status_card.pack(fill="x", pady=(0, 12))

attitude_value = tk.Label(
    status_card,
    text="No data",
    bg="#1C1C1E",
    fg="#8E8E93",
    font=("Arial", 30, "bold")
)
attitude_value.pack(pady=(6, 2))

xyz_value = tk.Label(
    status_card,
    text="X: --  Y: --  Z: --",
    bg="#1C1C1E",
    fg="#A1A1AA",
    font=("Menlo", 12)
)
xyz_value.pack(pady=2)

canvas_3d = tk.Canvas(
    status_card,
    width=380,
    height=235,
    bg="#1C1C1E",
    highlightthickness=0
)
canvas_3d.pack(pady=8)

draw_3d_platform(0, 0)
# 初始画一个水平平台。

make_button(
    status_card,
    "Request STATUS",
    lambda: send_action("STATUS"),
    bg="#3A3A3C",
    width=18
).pack(pady=(4, 8))


# =========================
# Left: Manual motor control
# =========================

motor_card = make_card(left_col, "Manual Motor Control / 手动控制")
motor_card.pack(fill="x", pady=(0, 12))

rows = [
    ("左前 LF", "LF_IN", "LF_OUT"),
    ("右前 RF", "RF_IN", "RF_OUT"),
    ("前部 FRONT", "FRONT_IN", "FRONT_OUT"),
    ("后右 RR", "RR_IN", "RR_OUT"),
    ("后左 RL", "RL_IN", "RL_OUT"),
    ("后部 BACK", "BACK_IN", "BACK_OUT"),
]
# 每一行格式：
# 显示文字, 收线指令, 放线指令

tk.Label(motor_card, text="Position", bg="#1C1C1E", fg="#A1A1AA", font=("Arial", 11, "bold")).grid(row=0, column=0, padx=8, pady=6)
tk.Label(motor_card, text="Retract / 收", bg="#1C1C1E", fg="#A1A1AA", font=("Arial", 11, "bold")).grid(row=0, column=1, padx=8, pady=6)
tk.Label(motor_card, text="Release / 放", bg="#1C1C1E", fg="#A1A1AA", font=("Arial", 11, "bold")).grid(row=0, column=2, padx=8, pady=6)

for i, (label, in_cmd, out_cmd) in enumerate(rows, start=1):
    tk.Label(
        motor_card,
        text=label,
        bg="#1C1C1E",
        fg="#F2F2F7",
        font=("Arial", 12, "bold"),
        width=12
    ).grid(row=i, column=0, padx=8, pady=5)

    make_button(
        motor_card,
        "收",
        lambda c=in_cmd: send_action(c),
        bg="#1F6FEB",
        width=12
    ).grid(row=i, column=1, padx=8, pady=5)

    make_button(
        motor_card,
        "放",
        lambda c=out_cmd: send_action(c),
        bg="#5E5CE6",
        width=12
    ).grid(row=i, column=2, padx=8, pady=5)


# =========================
# Right: Auto
# =========================

auto_card = make_card(right_col, "Auto Stabilisation / 自稳")
auto_card.pack(fill="x", pady=(0, 12))

make_button(
    auto_card,
    "AUTO ON",
    lambda: send_action("AUTO_ON"),
    bg="#30D158",
    fg="#000000",
    width=18
).grid(row=0, column=0, padx=8, pady=8)

make_button(
    auto_card,
    "AUTO OFF",
    lambda: send_action("AUTO_OFF"),
    bg="#FF9F0A",
    fg="#000000",
    width=18
).grid(row=0, column=1, padx=8, pady=8)

make_button(
    auto_card,
    "ALL IN 全收",
    lambda: send_action("ALL_IN"),
    bg="#0A84FF",
    width=18
).grid(row=1, column=0, padx=8, pady=8)

make_button(
    auto_card,
    "ALL OUT 全放",
    lambda: send_action("ALL_OUT"),
    bg="#BF5AF2",
    width=18
).grid(row=1, column=1, padx=8, pady=8)

make_button(
    status_card,
    "CALIBRATE 归零",
    lambda: send_action("CALIBRATE"),
    bg="#30D158",
    fg="#000000",
    width=18
).pack(pady=(0, 8))


# =========================
# Right: Speed
# =========================

speed_card = make_card(right_col, "Speed / 速度")
speed_card.pack(fill="x", pady=(0, 12))

speed_buttons = []

def set_speed(command, button):
    """
    用户按 LOW/MID/HIGH 时：
    1. UI 高亮选中按钮
    2. 发送速度指令去 Arduino
    """
    select_speed(speed_buttons, button)
    send_action(command)

b_low = make_button(speed_card, "LOW", lambda: None, width=11)
b_mid = make_button(speed_card, "MID", lambda: None, width=11)
b_high = make_button(speed_card, "HIGH", lambda: None, width=11)

speed_buttons.extend([b_low, b_mid, b_high])

b_low.config(command=lambda: set_speed("LOW", b_low))
b_mid.config(command=lambda: set_speed("MID", b_mid))
b_high.config(command=lambda: set_speed("HIGH", b_high))

b_low.grid(row=0, column=0, padx=6, pady=8)
b_mid.grid(row=0, column=1, padx=6, pady=8)
b_high.grid(row=0, column=2, padx=6, pady=8)

b_mid.config(bg="#30D158")
# 默认高亮 MID，因为 Arduino 默认 speedValue = 120。


# =========================
# Right: Time options
# =========================

time_card = make_card(right_col, "Action Duration / 动作时长")
time_card.pack(fill="x", pady=(0, 12))

tk.Label(
    time_card,
    text="Manual action time",
    bg="#1C1C1E",
    fg="#A1A1AA",
    font=("Arial", 11, "bold")
).grid(row=0, column=0, columnspan=4, sticky="w", padx=8, pady=(4, 2))

manual_time_buttons = []

manual_times = [
    ("0.25s", "TIME_250"),
    ("0.5s", "TIME_500"),
    ("1s", "TIME_1000"),
    ("2s", "TIME_2000"),
]

def set_manual_time(command, button):
    """
    设置手动动作时间。
    例如按 0.5s，就发送 TIME_500。
    """
    select_time(manual_time_buttons, button)
    send_action(command)

for i, (label, command) in enumerate(manual_times):
    b = make_button(time_card, label, lambda: None, width=8)
    b.config(command=lambda c=command, btn=b: set_manual_time(c, btn))
    b.grid(row=1, column=i, padx=5, pady=5)
    manual_time_buttons.append(b)

manual_time_buttons[2].config(bg="#0A84FF")
# 默认 UI 高亮 1s。
# 如果 Arduino 默认 runTime 係 200ms，但 UI 高亮 1s 会有少少唔一致。
# 想一致可以改成 manual_time_buttons[0] 或加 0.2s 按钮。


tk.Label(
    time_card,
    text="Auto stabilisation pulse",
    bg="#1C1C1E",
    fg="#A1A1AA",
    font=("Arial", 11, "bold")
).grid(row=2, column=0, columnspan=4, sticky="w", padx=8, pady=(10, 2))

auto_time_buttons = []

auto_times = [
    ("0.15s", "AUTOTIME_150"),
    ("0.25s", "AUTOTIME_250"),
    ("0.35s", "AUTOTIME_350"),
    ("0.5s", "AUTOTIME_500"),
]

def set_auto_time(command, button):
    """
    设置自稳每次脉冲时间。
    例如按 0.25s，就发送 AUTOTIME_250。
    """
    select_time(auto_time_buttons, button)
    send_action(command)

for i, (label, command) in enumerate(auto_times):
    b = make_button(time_card, label, lambda: None, width=8)
    b.config(command=lambda c=command, btn=b: set_auto_time(c, btn))
    b.grid(row=3, column=i, padx=5, pady=5)
    auto_time_buttons.append(b)

auto_time_buttons[1].config(bg="#0A84FF")
# 默认 UI 高亮 0.25s。
# 如果 Arduino 默认 autoRunTime 係 50ms，想一致就需要加 0.05s 按钮。


# =========================
# Right: Stop + Log
# =========================

stop_card = make_card(right_col, "Emergency / 安全")
stop_card.pack(fill="x", pady=(0, 12))

make_button(
    stop_card,
    "STOP ALL",
    lambda: send_action("ALLS"),
    bg="#FF453A",
    fg="#FFFFFF",
    width=38
).pack(padx=8, pady=10)

log_card = make_card(right_col, "Log / 回传")
log_card.pack(fill="both", expand=True)

log_box = tk.Text(
    log_card,
    height=12,
    bg="#111113",
    fg="#F2F2F7",
    insertbackground="#FFFFFF",
    font=("Menlo", 10),
    relief="flat",
    bd=0
)
log_box.pack(fill="both", expand=True, padx=6, pady=6)


# ============================================================
# Start Tkinter main loop
# ============================================================
# root.mainloop() 会保持窗口运行。
# 所有按钮、BLE 回传、3D 图更新都喺呢个主循环入面处理。
# ============================================================
root.mainloop()