"""Always-on-top driving input overlay for games."""

import ctypes
import math
import sys
import time
import tkinter as tk
from collections import deque
from ctypes import wintypes
from pathlib import Path

try:
    from PIL import Image, ImageTk

    HAS_PIL = True
except ImportError:
    HAS_PIL = False

SCRIPT_DIR = Path(__file__).resolve().parent
WHEEL_IMAGE_PATHS = (
    SCRIPT_DIR / "steering_wheel.png",
    SCRIPT_DIR / "wheel.png",
    SCRIPT_DIR / "assets" / "steering_wheel.png",
)

# --- Win32 / XInput ---

user32 = ctypes.windll.user32

VK_W = 0x57
VK_A = 0x41
VK_S = 0x53
VK_D = 0x44
UPDATE_MS = 16  # ~60 Hz
STICK_DEADZONE = 0.12


class XINPUT_GAMEPAD(ctypes.Structure):
    _fields_ = [
        ("wButtons", wintypes.WORD),
        ("bLeftTrigger", ctypes.c_ubyte),
        ("bRightTrigger", ctypes.c_ubyte),
        ("sThumbLX", wintypes.SHORT),
        ("sThumbLY", wintypes.SHORT),
        ("sThumbRX", wintypes.SHORT),
        ("sThumbRY", wintypes.SHORT),
    ]


class XINPUT_STATE(ctypes.Structure):
    _fields_ = [
        ("dwPacketNumber", wintypes.DWORD),
        ("Gamepad", XINPUT_GAMEPAD),
    ]


def _load_xinput():
    for name in ("xinput1_4", "xinput1_3", "xinput9_1_0"):
        try:
            return ctypes.windll[name]
        except OSError:
            continue
    return None


XINPUT = _load_xinput()
if XINPUT:
    XINPUT.XInputGetState.argtypes = [wintypes.DWORD, ctypes.POINTER(XINPUT_STATE)]
    XINPUT.XInputGetState.restype = wintypes.DWORD


def _key_down(vk: int) -> bool:
    return bool(user32.GetAsyncKeyState(vk) & 0x8000)


def _apply_deadzone(value: float, deadzone: float = STICK_DEADZONE) -> float:
    if abs(value) < deadzone:
        return 0.0
    sign = 1.0 if value > 0 else -1.0
    return sign * (abs(value) - deadzone) / (1.0 - deadzone)


def read_gamepad() -> XINPUT_GAMEPAD | None:
    if not XINPUT:
        return None

    state = XINPUT_STATE()
    for slot in range(4):
        if XINPUT.XInputGetState(slot, ctypes.byref(state)) == 0:
            return state.Gamepad
    return None


def read_inputs() -> dict[str, float]:
    """Return throttle/brake 0–1 and steering -1 (left) to +1 (right)."""
    pad = read_gamepad()

    throttle = 0.0
    brake = 0.0
    steering = 0.0

    if _key_down(VK_W):
        throttle = 1.0
    if _key_down(VK_S):
        brake = 1.0

    if _key_down(VK_A) and not _key_down(VK_D):
        steering = -1.0
    elif _key_down(VK_D) and not _key_down(VK_A):
        steering = 1.0

    if pad:
        throttle = max(throttle, pad.bRightTrigger / 255.0)
        brake = max(brake, pad.bLeftTrigger / 255.0)

        stick_x = pad.sThumbLX / 32768.0
        stick_x = max(-1.0, min(1.0, stick_x))
        stick_steer = _apply_deadzone(stick_x)

        if abs(stick_steer) > 0.0:
            steering = stick_steer

    return {"throttle": throttle, "brake": brake, "steering": steering}


# --- Overlay ---

BG = "#1a1a1a"
FG = "#00ff88"
FG_DIM = "#666666"
TITLE_COLOR = "#e8eaed"
THROTTLE_HI = "#ff4444"
BRAKE_COLOR = "#ff4444"
STEER_L = "#44aaff"
STEER_R = "#ffaa44"
BAR_W = 120
GRAPH_W = 280
GRAPH_H = 110
HISTORY_LEN = 240  # ~4 s at 60 Hz
WHEEL_SIZE = 84
STEER_RANGE_DEG = 180  # lock-to-lock
FONT = ("Consolas", 13, "bold")
FONT_SMALL = ("Consolas", 8)
INPUT_HINT_FONT = ("Consolas", 7)
TITLE_FONT = ("Segoe UI", 10, "bold")
INPUT_HINT_COLOR = "#555555"

CUBE_BRIGHT = ("#ececec", "#a3a3a3", "#6b6b6b")
CUBE_WATERMARK = ("#2a2a2a", "#232323", "#1c1c1c")


def _iso(x: float, y: float, z: float, cx: float, cy: float, edge: float) -> tuple[float, float]:
    px = cx + (x - y) * edge * 0.866
    py = cy + (x + y) * edge * 0.5 - z * edge
    return px, py


def draw_cube_logo(
    canvas: tk.Canvas,
    cx: float,
    cy: float,
    size: float,
    *,
    faces: tuple[str, str, str] = CUBE_BRIGHT,
    tag: str = "",
) -> None:
    """Isometric cube mark (Cursor-style)."""
    edge = size / 2.15
    top_c, left_c, right_c = faces

    top = (
        _iso(0, 0, 1, cx, cy, edge),
        _iso(1, 0, 1, cx, cy, edge),
        _iso(1, 1, 1, cx, cy, edge),
        _iso(0, 1, 1, cx, cy, edge),
    )
    left = (
        _iso(0, 0, 0, cx, cy, edge),
        _iso(0, 1, 0, cx, cy, edge),
        _iso(0, 1, 1, cx, cy, edge),
        _iso(0, 0, 1, cx, cy, edge),
    )
    right = (
        _iso(1, 1, 0, cx, cy, edge),
        _iso(1, 0, 0, cx, cy, edge),
        _iso(1, 0, 1, cx, cy, edge),
        _iso(1, 1, 1, cx, cy, edge),
    )

    for face, color in ((left, left_c), (right, right_c), (top, top_c)):
        canvas.create_polygon(*face, fill=color, outline="", tags=tag)


class MetricoreBrand:
    """Header brand: cube logo + METRICORE title."""

    LOGO_CANVAS = 20

    def __init__(self, parent: tk.Frame):
        self.frame = tk.Frame(parent, bg=BG)
        self.frame.pack(side="left")

        pad = self.LOGO_CANVAS // 2 + 1
        self.logo = tk.Canvas(
            self.frame,
            width=self.LOGO_CANVAS + 2,
            height=self.LOGO_CANVAS + 2,
            bg=BG,
            highlightthickness=0,
        )
        self.logo.pack(side="left", padx=(0, 5))
        draw_cube_logo(self.logo, pad, pad, self.LOGO_CANVAS, faces=CUBE_BRIGHT)

        self.title = tk.Label(
            self.frame,
            text="METRICORE",
            font=TITLE_FONT,
            fg=TITLE_COLOR,
            bg=BG,
        )
        self.title.pack(side="left")

    def widgets(self):
        return (self.frame, self.logo, self.title)


def _steer_label(value: float) -> tuple[str, str]:
    pct = int(round(abs(value) * 100))
    if pct == 0:
        return "0%", FG_DIM
    if value < 0:
        return f"L {pct}%", STEER_L
    return f"R {pct}%", STEER_R


class InputRow:
    """Single labeled value + horizontal bar."""

    def __init__(
        self,
        parent: tk.Frame,
        title: str,
        bar_mode: str = "fill",
        *,
        inputs: str | None = None,
        column: int | None = None,
        start_row: int = 0,
        pady: tuple[int, int] = (0, 0),
    ):
        self.bar_mode = bar_mode  # "fill" or "center"
        col_pad = (0, 12) if column == 0 else (0, 0)

        self.title_frame = tk.Frame(parent, bg=BG)
        self.label_title = tk.Label(
            self.title_frame, text=title, font=FONT_SMALL, fg=FG_DIM, bg=BG
        )
        self.label_title.pack(side="left")

        self.label_inputs = None
        if inputs:
            self.label_inputs = tk.Label(
                self.title_frame,
                text=inputs,
                font=INPUT_HINT_FONT,
                fg=INPUT_HINT_COLOR,
                bg=BG,
            )
            self.label_inputs.pack(side="left", padx=(5, 0))

        self.label_value = tk.Label(parent, text="0%", font=FONT, fg=FG, bg=BG)

        self.bar_bg = tk.Canvas(parent, width=BAR_W, height=6, bg="#333333", highlightthickness=0)

        if column is not None:
            self.title_frame.grid(row=start_row, column=column, sticky="w", padx=col_pad, pady=pady)
            self.label_value.grid(row=start_row + 1, column=column, sticky="w", padx=col_pad)
            self.bar_bg.grid(
                row=start_row + 2, column=column, sticky="w", pady=(2, 0), padx=col_pad
            )
        else:
            self.title_frame.pack(anchor="w")
            self.label_value.pack(anchor="w")
            self.bar_bg.pack(pady=(2, 0), anchor="w")

        if bar_mode == "center":
            mid = BAR_W // 2
            self.bar_bg.create_line(mid, 0, mid, 6, fill="#555555")
            self.bar_fill = self.bar_bg.create_rectangle(mid, 0, mid, 6, fill=STEER_R, outline="")
        else:
            self.bar_fill = self.bar_bg.create_rectangle(0, 0, 0, 6, fill=FG, outline="")

    def widgets(self):
        items = [self.title_frame, self.label_title, self.label_value, self.bar_bg]
        if self.label_inputs is not None:
            items.append(self.label_inputs)
        return tuple(items)

    def set_fill(self, value: float, color: str):
        """value 0–1 for fill bars."""
        pct = int(round(value * 100))
        # Only color value and title when there's input
        display_color = color if value > 0 else FG_DIM
        self.label_value.config(text=f"{pct}%", fg=display_color)
        self.label_title.config(fg=display_color)
        fill_w = int(BAR_W * value)
        self.bar_bg.coords(self.bar_fill, 0, 0, fill_w, 6)
        self.bar_bg.itemconfig(self.bar_fill, fill=color)

    def set_center(self, value: float):
        """value -1 to +1 for center bars."""
        text, color = _steer_label(value)
        self.label_value.config(text=text, fg=color)
        self.label_title.config(fg=color)

        mid = BAR_W // 2
        extent = int(mid * abs(value))
        if value < 0:
            self.bar_bg.coords(self.bar_fill, mid - extent, 0, mid, 6)
            self.bar_bg.itemconfig(self.bar_fill, fill=STEER_L)
        elif value > 0:
            self.bar_bg.coords(self.bar_fill, mid, 0, mid + extent, 6)
            self.bar_bg.itemconfig(self.bar_fill, fill=STEER_R)
        else:
            self.bar_bg.coords(self.bar_fill, mid, 0, mid, 6)


class InputTraceGraph:
    """Scrolling throttle / brake traces (sim-style input graph)."""

    def __init__(self, parent: tk.Frame):
        self._history: deque[tuple[float, float]] = deque(maxlen=HISTORY_LEN)
        self._widgets: list[tk.Widget] = []

        header = tk.Frame(parent, bg=BG)
        header.pack(anchor="w", pady=(0, 2))
        self._widgets.append(header)

        tk.Label(header, text="TRACE", font=FONT_SMALL, fg=FG_DIM, bg=BG).pack(side="left")

        for label, color in (("T", FG), ("B", BRAKE_COLOR)):
            tk.Label(header, text=f" {label}", font=("Consolas", 7, "bold"), fg=color, bg=BG).pack(
                side="left", padx=(6, 0)
            )

        self.canvas = tk.Canvas(
            parent,
            width=GRAPH_W,
            height=GRAPH_H,
            bg="#0d0d0d",
            highlightthickness=1,
            highlightbackground="#333333",
        )
        self.canvas.pack(anchor="w")
        self._widgets.append(self.canvas)

    def widgets(self):
        return self._widgets

    def push(self, throttle: float, brake: float):
        self._history.append((throttle, brake))
        self._draw()

    def _value_y(self, value: float, pad: int = 3) -> int:
        """Map 0–1 input to canvas Y (bottom = 0)."""
        span = GRAPH_H - pad * 2
        return GRAPH_H - pad - int(value * span)

    def _draw(self):
        c = self.canvas
        c.delete("all")

        w, h = GRAPH_W, GRAPH_H
        pad = 3

        draw_cube_logo(c, w / 2, h / 2, 88, faces=CUBE_WATERMARK, tag="watermark")

        for pct in (0.25, 0.5, 0.75):
            y = self._value_y(pct, pad)
            c.create_line(0, y, w, y, fill="#222222")

        for pct in (0.25, 0.5, 0.75):
            x = int(w * pct)
            c.create_line(x, 0, x, h, fill="#1a1a1a")

        n = len(self._history)
        if n < 2:
            return

        throttle_pts: list[float] = []
        brake_pts: list[float] = []

        for i, (t, b) in enumerate(self._history):
            x = int(i / (HISTORY_LEN - 1) * (w - 1))
            throttle_pts.extend([x, self._value_y(t, pad)])
            brake_pts.extend([x, self._value_y(b, pad)])

        c.create_line(*brake_pts, fill=BRAKE_COLOR, width=1.5, smooth=True)
        c.create_line(*throttle_pts, fill=FG, width=1.5, smooth=True)


class WindowControls:
    """Windows-style minimize / close buttons."""

    BTN_BG = "#2b2b2b"
    BTN_HOVER = "#3a3a3a"
    BTN_CLOSE_HOVER = "#c42b1c"
    BTN_LOCK_HOVER = "#5a5a5a"
    BTN_FG = "#cccccc"
    BTN_FONT = ("Segoe UI", 10)

    def __init__(self, parent: tk.Frame, root: tk.Tk, on_lock_toggle: callable = None):
        self.root = root
        self.on_lock_toggle = on_lock_toggle
        self.frame = tk.Frame(parent, bg=BG)
        self.frame.pack(side="right")
        self.is_locked = False

        self.lock_btn = self._make_button("🔓", self._toggle_lock, lock=True)
        self.close_btn = self._make_button("✕", self._close, close=True)

    def _make_button(self, text: str, command: callable, *, close: bool = False, lock: bool = False):
        btn = tk.Label(
            self.frame,
            text=text,
            font=self.BTN_FONT,
            fg=self.BTN_FG,
            bg=self.BTN_BG,
            width=2,
            padx=6,
            pady=1,
            cursor="hand2",
        )
        btn.pack(side="left")
        btn.bind("<Button-1>", lambda _e: command())
        btn.bind("<Enter>", lambda _e: btn.config(bg=self.BTN_CLOSE_HOVER if close else self.BTN_LOCK_HOVER))
        btn.bind("<Leave>", lambda _e: btn.config(bg=self.BTN_BG))
        return btn

    def _toggle_lock(self):
        self.is_locked = not self.is_locked
        self.lock_btn.config(text="🔒" if self.is_locked else "🔓")
        if self.on_lock_toggle:
            self.on_lock_toggle(self.is_locked)

    def _close(self):
        self.root.destroy()


class DragHandle:
    """Bottom bar used to drag the overlay."""

    BAR_COLOR = "#444444"
    BAR_HEIGHT = 8

    def __init__(
        self,
        parent: tk.Frame,
        on_start: callable,
        on_motion: callable,
    ):
        self.frame = tk.Frame(parent, bg=parent.cget("bg"), height=self.BAR_HEIGHT)
        self.frame.pack(side="right", anchor="e", fill="x", expand=True, padx=(0, 0), pady=(4, 0))
        self.frame.pack_propagate(False)

        self.bar = tk.Canvas(
            self.frame,
            bg=parent.cget("bg"),
            height=self.BAR_HEIGHT,
            highlightthickness=0,
            cursor="fleur",
        )
        self.bar.pack(fill="x", expand=True)
        self.bar.create_rectangle(0, 0, 1000, self.BAR_HEIGHT, fill=self.BAR_COLOR, outline="")

        for widget in (self.frame, self.bar):
            widget.bind("<Button-1>", on_start)
            widget.bind("<B1-Motion>", on_motion)


def _find_wheel_image() -> Path | None:
    for path in WHEEL_IMAGE_PATHS:
        if path.is_file():
            return path
    return None


def _load_wheel_image(path: Path, size: int) -> Image.Image:
    """Load wheel PNG and strip dark background for overlay blending."""
    img = Image.open(path).convert("RGBA")
    pixels = img.load()
    width, height = img.size

    for y in range(height):
        for x in range(width):
            r, g, b, a = pixels[x, y]
            if r < 40 and g < 40 and b < 40:
                pixels[x, y] = (0, 0, 0, 0)
            elif a > 0:
                lum = int(0.299 * r + 0.587 * g + 0.114 * b)
                gray = int(lum * 0.4 + 50)
                pixels[x, y] = (gray, gray, gray, a)

    return img.resize((size, size), Image.LANCZOS)


class SteeringWheel:
    """Rotating wheel graphic mapped to ±270° (540° lock-to-lock)."""

    SMOOTH_FACTOR = 0.12  # 0-1, higher = smoother/slower, lower = faster/snappier

    def __init__(self, parent: tk.Frame, *, column: int = 1, start_row: int = 0):
        self._half_range = STEER_RANGE_DEG / 2
        self._cx = WHEEL_SIZE // 2
        self._cy = WHEEL_SIZE // 2
        self._outer_r = int(WHEEL_SIZE * 0.42)
        self._inner_r = max(6, int(WHEEL_SIZE * 0.12))
        self._spoke_angles = (0, 120, 240)
        self._photo = None
        self._image_source = None
        self._use_image = False
        self._current_steering = 0.0  # Smoothed steering value

        wheel_path = _find_wheel_image()
        if wheel_path and HAS_PIL:
            self._image_source = _load_wheel_image(wheel_path, WHEEL_SIZE)
            self._use_image = True
        elif wheel_path and not HAS_PIL:
            print(f"Found {wheel_path.name} but Pillow is not installed.")
            print("Run: pip install Pillow\n")

        self.canvas = tk.Canvas(
            parent, width=WHEEL_SIZE, height=WHEEL_SIZE, bg=BG, highlightthickness=0
        )
        self.canvas.grid(row=start_row, column=column, rowspan=3, sticky="n", padx=(0, 8), pady=(20, 0))
        self._draw(0.0)

    def widget(self):
        return self.canvas

    def set_steering(self, value: float):
        target = max(-1.0, min(1.0, value))
        # Interpolate smoothly towards target
        self._current_steering += (target - self._current_steering) * self.SMOOTH_FACTOR
        self._draw(self._current_steering)

    def _point(self, radius: float, angle_rad: float) -> tuple[float, float]:
        return (
            self._cx + radius * math.sin(angle_rad),
            self._cy - radius * math.cos(angle_rad),
        )

    def _draw(self, steering: float):
        c = self.canvas
        c.delete("all")

        if self._use_image and self._image_source is not None:
            angle = -steering * self._half_range
            rotated = self._image_source.rotate(angle, resample=Image.BICUBIC)
            self._photo = ImageTk.PhotoImage(rotated)
            c.create_image(self._cx, self._cy, image=self._photo)
            c.create_line(self._cx, 2, self._cx, 10, fill="#2a2a2a", width=2)
            return

        rotation = math.radians(steering * self._half_range)
        cx, cy = self._cx, self._cy
        r, ir = self._outer_r, self._inner_r

        c.create_oval(cx - r, cy - r, cx + r, cy + r, outline="#777777", width=3)

        grip_r = r - 4
        for grip_angle in range(0, 360, 30):
            a = math.radians(grip_angle) + rotation
            gx, gy = self._point(grip_r, a)
            c.create_oval(gx - 2, gy - 2, gx + 2, gy + 2, fill="#555555", outline="")

        for i, base_angle in enumerate(self._spoke_angles):
            a = math.radians(base_angle) + rotation
            x1, y1 = self._point(ir, a)
            x2, y2 = self._point(r - 2, a)
            color = "#ffcc66" if i == 0 else "#aaaaaa"
            width = 3 if i == 0 else 2
            c.create_line(x1, y1, x2, y2, fill=color, width=width)

        c.create_oval(
            cx - ir, cy - ir, cx + ir, cy + ir, fill="#333333", outline="#bbbbbb", width=2
        )

        # Top alignment mark (fixed, shows center reference)
        c.create_line(cx, cy - r - 2, cx, cy - r + 5, fill="#2a2a2a", width=2)


class BrakeLight:
    """Brake light indicator text label."""

    def __init__(self, parent: tk.Frame):
        self.label = tk.Label(
            parent,
            text="BRAKE",
            font=TITLE_FONT,
            fg=FG_DIM,
            bg=BG,
            padx=8,
        )
        self.label.pack(side="left", padx=(12, 0))
        self.is_active = False

    def toggle(self):
        """Toggle brake light on/off."""
        self.is_active = not self.is_active
        color = BRAKE_COLOR if self.is_active else FG_DIM
        self.label.config(fg=color)



class InputOverlay:
    def __init__(self):
        self.root = tk.Tk()
        self.root.title("METRICORE")
        self.root.configure(bg=BG)
        self.root.attributes("-topmost", True)
        self.root.overrideredirect(True)
        self.root.attributes("-alpha", 0.88)

        self._drag_x = 0
        self._drag_y = 0
        self._dragging_enabled = True  # Start enabled (unlocked)
        self._main_frame = None

        frame = tk.Frame(self.root, bg=BG, padx=10, pady=6)
        frame.pack()
        self._main_frame = frame

        top_bar = tk.Frame(frame, bg=BG)
        top_bar.pack(fill="x", pady=(0, 2))

        self.brand = MetricoreBrand(top_bar)
        
        self.controls = WindowControls(top_bar, self.root, on_lock_toggle=self._on_lock_toggle)
        
        self.time_label = tk.Label(
            top_bar,
            text="00:00:00",
            font=TITLE_FONT,
            fg=TITLE_COLOR,
            bg=BG,
            padx=12,
        )
        self.time_label.pack(side="right", padx=(12, 0))

        main_row = tk.Frame(frame, bg=BG)
        main_row.pack(anchor="w")

        col_graph = tk.Frame(main_row, bg=BG)
        col_graph.pack(side="left", padx=(0, 12))
        self.trace = InputTraceGraph(col_graph)

        col_values = tk.Frame(main_row, bg=BG)
        col_values.pack(side="left")

        values_grid = tk.Frame(col_values, bg=BG)
        values_grid.pack(anchor="w")

        self.throttle = InputRow(values_grid, "THROTTLE", inputs="W / RT", column=0)
        self.brake = InputRow(values_grid, "BRAKE", inputs="S / LT", column=1)
        self.steering = InputRow(
            values_grid, "STEER", inputs="A / D · L-Stick", bar_mode="center", column=1, start_row=3, pady=(20, 0)
        )
        self.wheel = SteeringWheel(values_grid, column=0, start_row=3)

        bottom_bar = tk.Frame(frame, bg=BG)
        bottom_bar.pack(fill="x", pady=(6, 0))

        self.root.protocol("WM_DELETE_WINDOW", self.root.destroy)

        self._place_default()
        self._setup_drag_bindings()
        self._tick()

    def _setup_drag_bindings(self):
        """Setup drag bindings on root window."""
        if self._dragging_enabled:
            self.root.bind("<Button-1>", self._start_drag)
            self.root.bind("<B1-Motion>", self._on_drag)
        else:
            self.root.unbind("<Button-1>")
            self.root.unbind("<B1-Motion>")

    def _on_lock_toggle(self, is_locked: bool):
        self._dragging_enabled = not is_locked
        self._setup_drag_bindings()

    def _place_default(self):
        self.root.update_idletasks()
        sw = self.root.winfo_screenwidth()
        x = sw - self.root.winfo_reqwidth() - 24
        self.root.geometry(f"+{x}+24")

    def _start_drag(self, event):
        self._drag_x = event.x_root - self.root.winfo_x()
        self._drag_y = event.y_root - self.root.winfo_y()

    def _on_drag(self, event):
        self.root.geometry(f"+{event.x_root - self._drag_x}+{event.y_root - self._drag_y}")

    def _tick(self):
        if not self.root.winfo_exists():
            return

        inputs = read_inputs()

        self.throttle.set_fill(inputs["throttle"], FG)

        b_pct = int(round(inputs["brake"] * 100))
        brake_color = THROTTLE_HI if b_pct >= 40 else BRAKE_COLOR
        self.brake.set_fill(inputs["brake"], brake_color)

        self.steering.set_center(inputs["steering"])
        self.wheel.set_steering(inputs["steering"])

        self.trace.push(inputs["throttle"], inputs["brake"])

        # Update time display
        current_time = time.strftime("%H:%M:%S")
        self.time_label.config(text=current_time)

        self.root.after(UPDATE_MS, self._tick)

    def run(self):
        self.root.mainloop()


def main():
    if sys.platform != "win32":
        print("This overlay is built for Windows (XInput + Win32 APIs).")
        sys.exit(1)
    if not XINPUT:
        print("Warning: XInput not found — controller inputs will not work.")
        print("Keyboard (W/A/S/D) will still work.\n")

    InputOverlay().run()


if __name__ == "__main__":
    main()
