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
import queue
import tkinter as tk
# tkinter 係 Python 内建 GUI 库。
# 呢个 UI 入面嘅窗口、按钮、文字、3D 示意图都係用 tkinter 做。

from tkinter import messagebox
# messagebox 用嚟弹出错误提示。
# 例如 BLE 未连接、发送失败，就会弹窗。

from bleak import BleakClient
# bleak 係 Python BLE 库。
# BleakClient 用嚟连接 BLE device、写入 characteristic、接收 notify。


try:
    import pygame
    PYGAME_AVAILABLE = True
except Exception:
    pygame = None
    PYGAME_AVAILABLE = False
# pygame 用嚟读取 Xbox controller / joystick。
# 如果 pygame 安装唔到，主 BLE UI 仍然可以照常运行，只係手柄功能会 unavailable。


# ============================================================
# UI theme colours
# ============================================================
# 深色 + 青绿色高亮，令 UI 更似科技控制面板。
# ============================================================
BG_MAIN = "#070A12"
BG_PANEL = "#0E1422"
BG_PANEL_ALT = "#101827"
CYAN = "#00E5FF"
CYAN_DARK = "#007C91"
GREEN = "#35F58A"
BLUE = "#2979FF"
PURPLE = "#A855F7"
ORANGE = "#FFB020"
RED = "#FF3B5C"
TEXT_MAIN = "#EAF6FF"
TEXT_MUTED = "#8EA4B8"


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


# ============================================================
# Xbox controller variables
# ============================================================
# 呢度係 Xbox controller 状态。
# controller_enabled = True 先会开始读手柄。
# controller_deadzone 用嚟避免摇杆轻微漂移误触发。
# controller_send_interval 用嚟限制发送频率，避免疯狂 spam Arduino。
# controller_mode：IN = 收线，OUT = 放线。
# ============================================================
controller_enabled = False
joystick = None

last_controller_command = None
last_controller_send_time = 0
last_controller_button_time = 0

controller_deadzone = 0.18
controller_send_interval = 0.18
controller_button_interval = 0.50

# IN = 收线, OUT = 放线.
# RT/LT 仍然可以切换收/放模式；如果两个 trigger 都唔按，就保持上一次模式。
controller_mode = "IN"

# Xbox left stick proportional scheduler.
# 左摇杆唔再只係简单 8 方向，而係根据 X/Y 比例分配到四个角。
# 例如：
# - 正前方：LF + RF 权重接近
# - 正右方：RF + RR 权重接近
# - 右前斜：RF 权重最大，同时 LF/RR 可能有细权重
# scheduler 每次只发送一个 Arduino 指令，但会按权重轮流发，实现比例感。
controller_weight_threshold = 0.12
controller_vector_gain = 1.0
controller_weight_bucket = []
controller_bucket_index = 0
controller_last_vector_key = None


# ============================================================
# BLE send protection
# ============================================================
# 原因：旧版 send_action 会喺 Tkinter 主线程入面等 BLE 发送完成。
# 如果连续按按钮 / Xbox 连续发指令，GUI 可能会 unexpected exit。
# send_busy 用嚟保证同一时间只发一条 BLE 指令。
# ============================================================
send_busy = False
last_send_error_time = 0
send_error_cooldown = 2.0
ble_text_queue = queue.Queue()
ui_log_queue = queue.Queue()

MAX_BLE_MESSAGES_PER_TICK = 12
MAX_LOG_MESSAGES_PER_TICK = 12
MAX_LOG_LINES = 300
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
            connection_label.config(text="● Connected", fg=GREEN)
            add_log("Connected to HMSoft")
        else:
            connection_label.config(text="● Not connected", fg=RED)
            add_log("Connection failed")

    except Exception as e:
        connection_label.config(text="● Not connected", fg=RED)
        messagebox.showerror("BLE connection error", str(e))


def send_action(command):
    """
    Safer non-blocking BLE send.
    """
    global send_busy
    global last_send_error_time

    if send_busy:
        add_log("BLE busy, skipped: " + command)
        return

    send_busy = True
    add_log("Sending: " + command)

    try:
        future = asyncio.run_coroutine_threadsafe(ble_send(command), loop)
    except Exception as e:
        send_busy = False
        add_log("Send schedule error: " + str(e))
        return

    def on_done(f):
        global send_busy
        global last_send_error_time

        send_busy = False

        try:
            f.result()
            root.after(0, lambda: add_log("Sent: " + command))
        except Exception as e:
            error_text = str(e)

            def report_error():
                global last_send_error_time

                add_log("Send error: " + error_text)

                if PYGAME_AVAILABLE:
                    now = pygame.time.get_ticks() / 1000.0
                else:
                    now = 0

                if now - last_send_error_time > send_error_cooldown:
                    last_send_error_time = now

            root.after(0, report_error)

    future.add_done_callback(on_done)

# ============================================================
# Xbox controller functions
# ============================================================

def init_xbox_controller():
    """
    Connect Xbox controller.

    使用方法：
    1. Mac 先用 Bluetooth 连接 Xbox controller
    2. Python UI 入面按 Connect Xbox
    3. 如果 pygame 读到手柄，就会显示 Xbox: Connected
    """
    global controller_enabled, joystick

    if not PYGAME_AVAILABLE:
        messagebox.showerror(
            "Xbox Controller Error",
            "pygame is not installed. Xbox controller control is unavailable."
        )
        add_log("pygame not installed, Xbox controller unavailable")
        return

    try:
        pygame.init()
        pygame.joystick.init()

        count = pygame.joystick.get_count()

        if count == 0:
            controller_enabled = False
            xbox_status_label.config(text="Xbox: Not found", fg=RED)
            add_log("No Xbox controller found")
            return

        joystick = pygame.joystick.Joystick(0)
        joystick.init()

        controller_enabled = True
        xbox_status_label.config(text="Xbox: Connected", fg=GREEN)
        add_log("Xbox controller connected: " + joystick.get_name())

    except Exception as e:
        controller_enabled = False
        xbox_status_label.config(text="Xbox: Error", fg=RED)
        messagebox.showerror("Xbox Controller Error", str(e))



def joystick_to_weighted_commands(x, y, mode):
    """
    Convert Xbox left stick X/Y into weighted Arduino commands.

    This is proportional control on the Mac side.
    Arduino still receives the existing simple commands:
        LF_IN / RF_IN / RR_IN / RL_IN
        LF_OUT / RF_OUT / RR_OUT / RL_OUT
        FRONT_IN / BACK_IN / LEFT_IN / RIGHT_IN

    Joystick meaning:
        x < 0 = left
        x > 0 = right
        y > 0 = forward/front  （你之前已校准成 y > 0 係前）
        y < 0 = backward/back

    Weight idea:
        LF corner receives forward + left components
        RF corner receives forward + right components
        RR corner receives back + right components
        RL corner receives back + left components

    Example:
        stick forward: LF and RF have similar weights
        stick right: RF and RR have similar weights
        stick front-right: RF strongest, LF and RR smaller depending ratio
    """

    suffix = "_IN" if mode == "IN" else "_OUT"

    # Small stick movement ignored to avoid drift.
    if abs(x) < controller_deadzone and abs(y) < controller_deadzone:
        return []

    # Apply deadzone smoothly.
    def apply_deadzone(value):
        if abs(value) < controller_deadzone:
            return 0.0
        if value > 0:
            return (value - controller_deadzone) / (1.0 - controller_deadzone)
        return (value + controller_deadzone) / (1.0 - controller_deadzone)

    x = apply_deadzone(x) * controller_vector_gain
    y = apply_deadzone(y) * controller_vector_gain

    # Clamp to [-1, 1].
    x = max(min(x, 1.0), -1.0)
    y = max(min(y, 1.0), -1.0)

    forward = max(y, 0.0)
    back = max(-y, 0.0)
    right = max(x, 0.0)
    left = max(-x, 0.0)

    weights = {
        "LF" + suffix: forward + left,
        "RF" + suffix: forward + right,
        "RR" + suffix: back + right,
        "RL" + suffix: back + left,
    }

    # Also allow pure side/pair commands when the stick is almost purely on one axis.
    # This keeps front/back/left/right movement clean when the user pushes straight.
    axis_balance_margin = 0.22
    if forward > controller_weight_threshold and right < axis_balance_margin and left < axis_balance_margin:
        weights["FRONT" + suffix] = forward * 1.4
    if back > controller_weight_threshold and right < axis_balance_margin and left < axis_balance_margin:
        weights["BACK" + suffix] = back * 1.4
    if left > controller_weight_threshold and forward < axis_balance_margin and back < axis_balance_margin:
        weights["LEFT" + suffix] = left * 1.4
    if right > controller_weight_threshold and forward < axis_balance_margin and back < axis_balance_margin:
        weights["RIGHT" + suffix] = right * 1.4

    # Convert weights into repeated command bucket.
    # Bigger weight = appears more times = sent more often.
    command_bucket = []
    for command, weight in weights.items():
        if weight >= controller_weight_threshold:
            repeats = int(round(weight * 5))
            repeats = max(1, min(repeats, 5))
            command_bucket.extend([command] * repeats)

    return command_bucket


def poll_xbox_controller():
    """
    Poll Xbox controller without blocking Tkinter.

    Buttons:
        A = STOP ALL
        Y = AUTO ON
        B = AUTO OFF
        X = CALIBRATE

    Triggers:
        RT = IN / 收线 mode
        LT = OUT / 放线 mode

    Left stick:
        Select motor direction.
    """
    global last_controller_command
    global last_controller_send_time
    global last_controller_button_time
    global controller_mode
    global controller_weight_bucket
    global controller_bucket_index
    global controller_last_vector_key

    if controller_enabled and joystick is not None and PYGAME_AVAILABLE:
        try:
            pygame.event.pump()

            x_axis = joystick.get_axis(0)
            y_axis = joystick.get_axis(1)

            # Common Xbox trigger mapping on macOS / pygame:
            # LT = axis 4, RT = axis 5, idle usually -1 and pressed usually goes toward 1.
            lt = joystick.get_axis(4) if joystick.get_numaxes() > 4 else -1
            rt = joystick.get_axis(5) if joystick.get_numaxes() > 5 else -1

            a_pressed = joystick.get_button(0) if joystick.get_numbuttons() > 0 else 0
            b_pressed = joystick.get_button(1) if joystick.get_numbuttons() > 1 else 0
            x_pressed = joystick.get_button(2) if joystick.get_numbuttons() > 2 else 0
            y_pressed = joystick.get_button(3) if joystick.get_numbuttons() > 3 else 0

            # RT = retract / 收线, LT = release / 放线
            if rt > 0.2:
                controller_mode = "IN"
            elif lt > 0.2:
                controller_mode = "OUT"

            xbox_mode_label.config(text="Mode: " + controller_mode)
            # Show live joystick value in the Xbox panel.
            try:
                xbox_axis_label.config(text=f"Stick X={x_axis:+.2f}   Y={y_axis:+.2f}")
            except Exception:
                pass

            now = pygame.time.get_ticks() / 1000.0

            # Button shortcuts with cooldown, to avoid repeated spam while holding button.
            # 如果这一轮已经发送按钮指令，就不要同一轮再发送摇杆指令。
            button_command_sent = False

            if now - last_controller_button_time > controller_button_interval:
                if a_pressed:
                    send_action("ALLS")
                    last_controller_button_time = now
                    last_controller_send_time = now
                    button_command_sent = True

                elif y_pressed:
                    send_action("AUTO_ON")
                    last_controller_button_time = now
                    last_controller_send_time = now
                    button_command_sent = True

                elif b_pressed:
                    send_action("AUTO_OFF")
                    last_controller_button_time = now
                    last_controller_send_time = now
                    button_command_sent = True

                elif x_pressed:
                    send_action("CALIBRATE")
                    last_controller_button_time = now
                    last_controller_send_time = now
                    button_command_sent = True

            if button_command_sent:
                root.after(80, poll_xbox_controller)
                return

            # Proportional left-stick motor control.
            # Build a weighted command bucket from the current joystick vector.
            global controller_weight_bucket
            global controller_bucket_index
            global controller_last_vector_key

            vector_key = (
                round(x_axis, 1),
                round(y_axis, 1),
                controller_mode
            )

            if vector_key != controller_last_vector_key:
                controller_weight_bucket = joystick_to_weighted_commands(x_axis, y_axis, controller_mode)
                controller_bucket_index = 0
                controller_last_vector_key = vector_key

            if controller_weight_bucket:
                if now - last_controller_send_time > controller_send_interval:
                    command = controller_weight_bucket[controller_bucket_index % len(controller_weight_bucket)]
                    send_action(command)
                    last_controller_command = command
                    last_controller_send_time = now
                    controller_bucket_index += 1
            else:
                last_controller_command = None

        except Exception as e:
            add_log("Xbox error: " + str(e))

    root.after(80, poll_xbox_controller)


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
            attitude_value.config(fg=GREEN)
            status_card.config(bg="#092015")
            # Level 时绿色，表示平台接近水平。
        else:
            attitude_value.config(fg=ORANGE)
            status_card.config(bg="#261B00")
            # 唔水平时橙色，提醒需要修正。

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
    关闭时唔再用 future.result() 阻塞等待 BLE，避免关窗口时 unexpected exit。
    """
    try:
        asyncio.run_coroutine_threadsafe(ble_disconnect(), loop)
    except Exception:
        pass

    root.destroy()


def make_card(parent, title):
    """
    创建一个科技感深色卡片区域。
    用 cyan 标题 + 深蓝灰背景，视觉上更似控制台 panel。
    """

    frame = tk.LabelFrame(
        parent,
        text="  " + title + "  ",
        bg=BG_PANEL,
        fg=CYAN,
        font=("Arial", 12, "bold"),
        padx=12,
        pady=10,
        labelanchor="n",
        bd=2,
        relief="groove"
    )
    return frame


def make_button(parent, text, command, bg=GREEN, fg="#000000", width=14):
    """
    创建统一科技感按钮。
    加 hover 效果，按钮摸上去会变亮。
    """

    btn = tk.Button(
        parent,
        text=text,
        command=command,
        bg=bg,
        fg=fg,
        activebackground=CYAN,
        activeforeground="#000000",
        relief="flat",
        bd=0,
        width=width,
        height=2,
        font=("Arial", 11, "bold"),
        cursor="hand2"
    )

    def on_enter(event):
        btn.config(bg=CYAN, fg="#000000")

    def on_leave(event):
        btn.config(bg=bg, fg=fg)

    btn.bind("<Enter>", on_enter)
    btn.bind("<Leave>", on_leave)

    return btn


def select_time(buttons, selected_button):
    """
    时间按钮选中效果。
    """

    for b in buttons:
        b.config(bg=BG_PANEL_ALT, fg=TEXT_MAIN)
    selected_button.config(bg=BLUE, fg="#FFFFFF")


def select_speed(buttons, selected_button):
    """
    速度按钮选中效果。
    """

    for b in buttons:
        b.config(bg=BG_PANEL_ALT, fg=TEXT_MAIN)
    selected_button.config(bg=GREEN, fg="#000000")


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

    # HUD style shadow + platform body
    canvas_3d.create_polygon(shadow, fill="#101827", outline="")
    canvas_3d.create_polygon(flat_points, fill="#063D4A", outline=CYAN, width=3)

    # 对角线，方便睇平台变形/倾斜
    canvas_3d.create_line(fl[0], fl[1], br[0], br[1], fill=GREEN, dash=(4, 3))
    canvas_3d.create_line(fr[0], fr[1], bl[0], bl[1], fill=GREEN, dash=(4, 3))

    # 四角标签
    canvas_3d.create_text(fl[0] - 22, fl[1] - 10, text="LF", fill=TEXT_MAIN, font=("Arial", 10, "bold"))
    canvas_3d.create_text(fr[0] + 22, fr[1] - 10, text="RF", fill=TEXT_MAIN, font=("Arial", 10, "bold"))
    canvas_3d.create_text(br[0] + 24, br[1] + 10, text="RR", fill=TEXT_MAIN, font=("Arial", 10, "bold"))
    canvas_3d.create_text(bl[0] - 24, bl[1] + 10, text="RL", fill=TEXT_MAIN, font=("Arial", 10, "bold"))

    canvas_3d.create_text(cx, 24, text="3D PLATFORM ATTITUDE", fill=CYAN, font=("Arial", 13, "bold"))
    canvas_3d.create_text(cx, 215, text=f"X={x}   Y={y}", fill=TEXT_MUTED, font=("Menlo", 11))


# =========================
# Root window
# =========================

root = tk.Tk()
# 创建主窗口。

def handle_tk_error(exc, val, tb):
    """
    Catch Tkinter callback errors instead of allowing the GUI to exit directly.
    Terminal 会打印完整 traceback，Log 会显示简短错误。
    """
    import traceback

    error_text = "".join(traceback.format_exception(exc, val, tb))
    print(error_text)

    try:
        add_log("GUI error: " + str(val))
    except Exception:
        pass

root.report_callback_exception = handle_tk_error

# Step 2 UI edits for root window, header, and main layout background
root.title("ENGG1100 Stabilisation Command Center")
root.geometry("1080x800")
root.configure(bg=BG_MAIN)
# 设置窗口标题、大小、背景色、最小尺寸。
root.minsize(900, 650)
# 设置窗口标题、大小、背景色、最小尺寸。

root.protocol("WM_DELETE_WINDOW", close_action)
# 用户按关闭窗口时，会先执行 close_action()，确保电机停止。


# =========================
# Header
# =========================

header = tk.Frame(root, bg=BG_MAIN)
header.pack(fill="x", padx=20, pady=(16, 10))

header_text = tk.Frame(header, bg=BG_MAIN)
header_text.pack(side="left")

title_label = tk.Label(
    header_text,
    text="ENGG1100 STABILISATION COMMAND CENTER",
    bg=BG_MAIN,
    fg=CYAN,
    font=("Arial", 22, "bold")
)
title_label.pack(anchor="w")

subtitle_label = tk.Label(
    header_text,
    text="BLE Motion Control  •  Tilt Feedback  •  Xbox Manual Override",
    bg=BG_MAIN,
    fg=TEXT_MUTED,
    font=("Menlo", 11)
)
subtitle_label.pack(anchor="w", pady=(2, 0))

connection_label = tk.Label(
    header,
    text="● Not connected",
    bg=BG_MAIN,
    fg=RED,
    font=("Arial", 13, "bold")
)
connection_label.pack(side="right", padx=10)

connect_btn = make_button(
    header,
    "Connect BLE",
    connect_action,
    bg=BLUE,
    width=14
)
connect_btn.pack(side="right", padx=10)


# =========================
# Scrollable main layout
# =========================
# 因为内容比较多，窗口细嘅时候会显示唔晒。
# 所以用 Canvas + Scrollbar 做可滚动区域。
# =========================

outer_frame = tk.Frame(root, bg=BG_MAIN)
outer_frame.pack(fill="both", expand=True)

scroll_canvas = tk.Canvas(
    outer_frame,
    bg=BG_MAIN,
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

main = tk.Frame(scroll_canvas, bg=BG_MAIN)

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

    原本用 int(event.delta / 120)，但 Mac 触控板嘅 delta 有时好细，
    int() 会变成 0，所以滚动完全冇反应。

    呢个版本只判断方向：
    event.delta > 0 = 向上滚
    event.delta < 0 = 向下滚
    咁样 MacBook 触控板会稳定好多。
    """
    if hasattr(event, "num") and event.num == 4:
        scroll_canvas.yview_scroll(-1, "units")
    elif hasattr(event, "num") and event.num == 5:
        scroll_canvas.yview_scroll(1, "units")
    elif event.delta > 0:
        scroll_canvas.yview_scroll(-1, "units")
    elif event.delta < 0:
        scroll_canvas.yview_scroll(1, "units")

root.bind_all("<MouseWheel>", on_mousewheel)
root.bind_all("<Button-4>", on_mousewheel)
root.bind_all("<Button-5>", on_mousewheel)
root.bind_all("<Up>", lambda event: scroll_canvas.yview_scroll(-1, "units"))
root.bind_all("<Down>", lambda event: scroll_canvas.yview_scroll(1, "units"))
root.bind_all("<Prior>", lambda event: scroll_canvas.yview_scroll(-5, "units"))
root.bind_all("<Next>", lambda event: scroll_canvas.yview_scroll(5, "units"))

left_col = tk.Frame(main, bg=BG_MAIN)
left_col.grid(row=0, column=0, sticky="nsew", padx=(18, 10), pady=8)

right_col = tk.Frame(main, bg=BG_MAIN)
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
    bg=BG_PANEL,
    fg=TEXT_MUTED,
    font=("Arial", 30, "bold")
)
attitude_value.pack(pady=(6, 2))

xyz_value = tk.Label(
    status_card,
    text="X: --  Y: --  Z: --",
    bg=BG_PANEL,
    fg=TEXT_MUTED,
    font=("Menlo", 12)
)
xyz_value.pack(pady=2)

canvas_3d = tk.Canvas(
    status_card,
    width=380,
    height=235,
    bg="#050812",
    highlightthickness=0
)
canvas_3d.pack(pady=8)

draw_3d_platform(0, 0)
# 初始画一个水平平台。

make_button(
    status_card,
    "Request STATUS",
    lambda: send_action("STATUS"),
    bg=CYAN_DARK,
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
    ("左侧 LEFT", "LEFT_IN", "LEFT_OUT"),
    ("右侧 RIGHT", "RIGHT_IN", "RIGHT_OUT"),
]

tk.Label(motor_card, text="Position", bg=BG_PANEL, fg=TEXT_MUTED, font=("Arial", 11, "bold")).grid(row=0, column=0, padx=8, pady=6)
tk.Label(motor_card, text="Retract / 收", bg=BG_PANEL, fg=TEXT_MUTED, font=("Arial", 11, "bold")).grid(row=0, column=1, padx=8, pady=6)
tk.Label(motor_card, text="Release / 放", bg=BG_PANEL, fg=TEXT_MUTED, font=("Arial", 11, "bold")).grid(row=0, column=2, padx=8, pady=6)

for i, (label, in_cmd, out_cmd) in enumerate(rows, start=1):
    tk.Label(
        motor_card,
        text=label,
        bg=BG_PANEL,
        fg=TEXT_MAIN,
        font=("Arial", 12, "bold"),
        width=12
    ).grid(row=i, column=0, padx=8, pady=5)

    make_button(
        motor_card,
        "收",
        lambda c=in_cmd: send_action(c),
        bg=BLUE,
        width=12
    ).grid(row=i, column=1, padx=8, pady=5)

    make_button(
        motor_card,
        "放",
        lambda c=out_cmd: send_action(c),
        bg=PURPLE,
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
    bg=GREEN,
    fg="#000000",
    width=18
).grid(row=0, column=0, padx=8, pady=8)

make_button(
    auto_card,
    "AUTO OFF",
    lambda: send_action("AUTO_OFF"),
    bg=ORANGE,
    fg="#000000",
    width=18
).grid(row=0, column=1, padx=8, pady=8)

make_button(
    auto_card,
    "ALL IN 全收",
    lambda: send_action("ALL_IN"),
    bg=BLUE,
    width=18
).grid(row=1, column=0, padx=8, pady=8)

make_button(
    auto_card,
    "ALL OUT 全放",
    lambda: send_action("ALL_OUT"),
    bg=PURPLE,
    width=18
).grid(row=1, column=1, padx=8, pady=8)

make_button(
    status_card,
    "CALIBRATE 归零",
    lambda: send_action("CALIBRATE"),
    bg=GREEN,
    fg="#000000",
    width=18
).pack(pady=(0, 8))


# =========================
# Right: Xbox Controller
# =========================

xbox_card = make_card(right_col, "Xbox Controller / 手柄控制")
xbox_card.pack(fill="x", pady=(0, 12))

xbox_status_label = tk.Label(
    xbox_card,
    text="Xbox: Not connected",
    bg=BG_PANEL,
    fg=RED,
    font=("Arial", 12, "bold")
)
xbox_status_label.grid(row=0, column=0, columnspan=2, padx=8, pady=6)

xbox_mode_label = tk.Label(
    xbox_card,
    text="Mode: IN",
    bg=BG_PANEL,
    fg=TEXT_MAIN,
    font=("Arial", 12, "bold")
)
xbox_mode_label.grid(row=1, column=0, columnspan=2, padx=8, pady=6)

xbox_axis_label = tk.Label(
    xbox_card,
    text="Stick X=+0.00   Y=+0.00",
    bg=BG_PANEL,
    fg=TEXT_MUTED,
    font=("Menlo", 10)
)
xbox_axis_label.grid(row=2, column=0, columnspan=2, padx=8, pady=4)

make_button(
    xbox_card,
    "Connect Xbox",
    init_xbox_controller,
    bg=GREEN,
    fg="#000000",
    width=18
).grid(row=3, column=0, padx=8, pady=8)

make_button(
    xbox_card,
    "STOP ALL",
    lambda: send_action("ALLS"),
    bg=RED,
    fg="#FFFFFF",
    width=18
).grid(row=3, column=1, padx=8, pady=8)

xbox_help = tk.Label(
    xbox_card,
    text="Left stick = proportional direction\nRT = retract / LT = release\nA = stop, Y = auto on, B = auto off, X = calibrate",
    bg=BG_PANEL,
    fg=TEXT_MUTED,
    font=("Arial", 10),
    justify="left"
)
xbox_help.grid(row=4, column=0, columnspan=2, padx=8, pady=6)


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

b_mid.config(bg=GREEN, fg="#000000")
# 默认高亮 MID，因为 Arduino 默认 speedValue = 120。


# =========================
# Right: Time options
# =========================

time_card = make_card(right_col, "Action Duration / 动作时长")
time_card.pack(fill="x", pady=(0, 12))

tk.Label(
    time_card,
    text="Manual action time",
    bg=BG_PANEL,
    fg=TEXT_MUTED,
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

manual_time_buttons[2].config(bg=BLUE, fg="#FFFFFF")
# 默认 UI 高亮 1s。
# 如果 Arduino 默认 runTime 係 200ms，但 UI 高亮 1s 会有少少唔一致。
# 想一致可以改成 manual_time_buttons[0] 或加 0.2s 按钮。


tk.Label(
    time_card,
    text="Auto stabilisation pulse",
    bg=BG_PANEL,
    fg=TEXT_MUTED,
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

auto_time_buttons[1].config(bg=BLUE, fg="#FFFFFF")
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
    bg=RED,
    fg="#FFFFFF",
    width=38
).pack(padx=8, pady=10)

log_card = make_card(right_col, "Log / 回传")
log_card.pack(fill="both", expand=True)

log_box = tk.Text(
    log_card,
    height=12,
    bg="#050812",
    fg=GREEN,
    insertbackground=CYAN,
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
poll_xbox_controller()
root.mainloop()