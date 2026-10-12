"""
EyeRest — every 25 minutes, fade the screen to black and back over 5 seconds
as a gentle reminder to rest your eyes.

Every 4th blink is a longer break: a realistic cat walks in, stretches,
lies down for 5 minutes, then walks away (or sooner if you press Escape).
A small heads-up appears 30 seconds before that overlay so you can save
your work; the cat break itself still takes over the screen.
"""

from __future__ import annotations

import argparse
import ctypes
import math
import sys
import threading
import time
import tkinter as tk
from pathlib import Path

INTERVAL_SECONDS = 25 * 60
FADE_SECONDS = 5.0
LONG_BREAK_EVERY = 4
LONG_BREAK_SECONDS = 5 * 60
WARN_SECONDS = 30.0
START_NOW_WARN_SECONDS = 5.0
WALK_SECONDS = 5.0
STRETCH_SECONDS = 2.2
LIE_SECONDS = 1.4
FPS = 60
WALK_FRAME_HZ = 6  # walk cycle swaps per second

SCRIPT_DIR = Path(__file__).resolve().parent
ASSETS_DIR = SCRIPT_DIR / "assets"

# Sprite files contain only the fully opaque cat; their outside pixels are
# transparent so the fixed break-screen background never travels with it.
CAT_FRAMES = {
    "walk": [
        "cat_walk_1_sprite.png",
        "cat_walk_2_sprite.png",
        "cat_walk_3_sprite.png",
        "cat_walk_4_sprite.png",
    ],
    "stretch": ["cat_stretch_sprite.png"],
    "lie": ["cat_lie_sprite.png"],
}

SESSION_BLINK = "blink"
SESSION_LONG = "long"

# Win32 virtual-screen metrics (all monitors)
SM_XVIRTUALSCREEN = 76
SM_YVIRTUALSCREEN = 77
SM_CXVIRTUALSCREEN = 78
SM_CYVIRTUALSCREEN = 79
MONITOR_DEFAULTTOPRIMARY = 1
SPI_SETDESKWALLPAPER = 0x0014
SPI_GETDESKWALLPAPER = 0x0073
MAX_PATH = 260
GWL_EXSTYLE = -20
WS_EX_NOACTIVATE = 0x08000000
WS_EX_TOOLWINDOW = 0x00000080
SWP_NOSIZE = 0x0001
SWP_NOMOVE = 0x0002
SWP_NOACTIVATE = 0x0010
SWP_FRAMECHANGED = 0x0020
HWND_TOPMOST = ctypes.c_void_p(-1)


class _RECT(ctypes.Structure):
    _fields_ = [
        ("left", ctypes.c_long),
        ("top", ctypes.c_long),
        ("right", ctypes.c_long),
        ("bottom", ctypes.c_long),
    ]


class _MONITORINFO(ctypes.Structure):
    _fields_ = [
        ("cbSize", ctypes.c_ulong),
        ("rcMonitor", _RECT),
        ("rcWork", _RECT),
        ("dwFlags", ctypes.c_ulong),
    ]


def virtual_screen_bounds() -> tuple[int, int, int, int]:
    user32 = ctypes.windll.user32
    return (
        user32.GetSystemMetrics(SM_XVIRTUALSCREEN),
        user32.GetSystemMetrics(SM_YVIRTUALSCREEN),
        user32.GetSystemMetrics(SM_CXVIRTUALSCREEN),
        user32.GetSystemMetrics(SM_CYVIRTUALSCREEN),
    )


def primary_monitor_origin(padding: int = 20) -> tuple[int, int]:
    """Top-left corner of the primary monitor (Monitor 1), with padding."""
    user32 = ctypes.windll.user32
    monitor = user32.MonitorFromWindow(None, MONITOR_DEFAULTTOPRIMARY)
    info = _MONITORINFO()
    info.cbSize = ctypes.sizeof(_MONITORINFO)
    if not user32.GetMonitorInfoW(monitor, ctypes.byref(info)):
        return padding, padding
    return info.rcMonitor.left + padding, info.rcMonitor.top + padding


def primary_work_area() -> tuple[int, int, int, int]:
    """Primary monitor work area as (left, top, right, bottom)."""
    user32 = ctypes.windll.user32
    monitor = user32.MonitorFromWindow(None, MONITOR_DEFAULTTOPRIMARY)
    info = _MONITORINFO()
    info.cbSize = ctypes.sizeof(_MONITORINFO)
    if not user32.GetMonitorInfoW(monitor, ctypes.byref(info)):
        x, y = primary_monitor_origin(0)
        return x, y, x + 1920, y + 1080
    area = info.rcWork
    return area.left, area.top, area.right, area.bottom


def _foreground_hwnd() -> int:
    user32 = ctypes.windll.user32
    user32.GetForegroundWindow.restype = ctypes.c_void_p
    hwnd = user32.GetForegroundWindow()
    return int(hwnd) if hwnd else 0


def _restore_foreground(hwnd: int) -> None:
    if not hwnd:
        return
    ctypes.windll.user32.SetForegroundWindow(hwnd)


def _apply_no_activate(win: tk.Misc) -> None:
    """Keep a topmost popup from stealing keyboard focus (Windows)."""
    user32 = ctypes.windll.user32
    user32.GetParent.restype = ctypes.c_void_p
    user32.GetParent.argtypes = [ctypes.c_void_p]
    user32.SetWindowPos.restype = ctypes.c_int
    user32.SetWindowPos.argtypes = [
        ctypes.c_void_p,
        ctypes.c_void_p,
        ctypes.c_int,
        ctypes.c_int,
        ctypes.c_int,
        ctypes.c_int,
        ctypes.c_uint,
    ]
    widget = int(win.winfo_id())
    hwnd = int(user32.GetParent(widget) or widget)
    style = user32.GetWindowLongW(hwnd, GWL_EXSTYLE)
    user32.SetWindowLongW(hwnd, GWL_EXSTYLE, style | WS_EX_NOACTIVATE | WS_EX_TOOLWINDOW)
    user32.SetWindowPos(
        hwnd,
        HWND_TOPMOST,
        0,
        0,
        0,
        0,
        SWP_NOMOVE | SWP_NOSIZE | SWP_NOACTIVATE | SWP_FRAMECHANGED,
    )


def ease_in_out(t: float) -> float:
    t = max(0.0, min(1.0, t))
    return t * t * (3.0 - 2.0 * t)


def refresh_desktop() -> None:
    """Ask Windows to redraw the desktop after a translucent overlay closes."""
    user32 = ctypes.windll.user32
    # Do NOT call SPI_SETDESKWALLPAPER with NULL — that clears the wallpaper.
    buffer = ctypes.create_unicode_buffer(MAX_PATH)
    if user32.SystemParametersInfoW(SPI_GETDESKWALLPAPER, MAX_PATH, buffer, 0):
        path = buffer.value
        if path:
            user32.SystemParametersInfoW(SPI_SETDESKWALLPAPER, 0, path, 0)
    for class_name in ("Progman", "WorkerW", "Shell_TrayWnd"):
        hwnd = user32.FindWindowW(class_name, None)
        if hwnd:
            user32.InvalidateRect(hwnd, None, True)
            user32.UpdateWindow(hwnd)


def close_overlay(win: tk.Misc, done: threading.Event | None = None) -> None:
    """Safely tear down an overlay without leaving compositor artifacts."""
    try:
        win.attributes("-alpha", 0.0)
    except tk.TclError:
        pass
    try:
        win.withdraw()
        win.update_idletasks()
        win.destroy()
    except tk.TclError:
        pass
    refresh_desktop()
    if done is not None:
        done.set()


class CatBreakWarning:
    """Small non-activating corner toast; does not block the rest of the desktop."""

    WIDTH = 368
    HEIGHT = 132
    PAD = 16

    def __init__(
        self,
        master: tk.Misc,
        remaining_fn: object,
        break_seconds: float,
    ) -> None:
        self._remaining_fn = remaining_fn
        self._closed = False
        previous = _foreground_hwnd()
        self.win = tk.Toplevel(master)
        self.win.overrideredirect(True)
        self.win.attributes("-topmost", True)
        self.win.configure(bg="#25211d")
        self.win.resizable(False, False)

        frame = tk.Frame(self.win, bg="#25211d", padx=14, pady=12)
        frame.pack(fill="both", expand=True)
        self._countdown = tk.Label(
            frame,
            text="",
            bg="#25211d",
            fg="#fff4e8",
            font=("Segoe UI Semibold", 16),
            anchor="w",
        )
        self._countdown.pack(anchor="w")
        tk.Label(
            frame,
            text="The screen will go dark for a cat break.",
            bg="#25211d",
            fg="#f3ebe3",
            font=("Segoe UI", 10),
            anchor="w",
        ).pack(anchor="w", pady=(4, 0))
        minutes = max(1, int(round(break_seconds / 60)))
        tk.Label(
            frame,
            text=f"Save your work. About {minutes} min once it starts. Click to dismiss.",
            bg="#25211d",
            fg="#c4b5a5",
            font=("Segoe UI", 9),
            anchor="w",
            wraplength=self.WIDTH - 36,
            justify="left",
        ).pack(anchor="w", pady=(2, 0))

        self._place()
        self.win.update_idletasks()
        try:
            _apply_no_activate(self.win)
        except (tk.TclError, OSError, AttributeError):
            pass
        _restore_foreground(previous)

        def bind_dismiss(widget: tk.Misc) -> None:
            widget.bind("<Button-1>", lambda _event: self.destroy())
            for child in widget.winfo_children():
                bind_dismiss(child)

        bind_dismiss(self.win)
        self._tick()

    def _place(self) -> None:
        left, top, right, bottom = primary_work_area()
        x = right - self.WIDTH - self.PAD
        y = bottom - self.HEIGHT - self.PAD
        x = max(left + self.PAD, x)
        y = max(top + self.PAD, y)
        self.win.geometry(f"{self.WIDTH}x{self.HEIGHT}+{x}+{y}")

    def _tick(self) -> None:
        if self._closed:
            return
        try:
            remaining = float(self._remaining_fn())  # type: ignore[operator]
            secs = max(0, int(math.ceil(remaining)))
            self._countdown.config(text=f"Cat break in {secs // 60}:{secs % 60:02d}")
        except tk.TclError:
            return
        self.win.after(200, self._tick)

    def destroy(self) -> None:
        if self._closed:
            return
        self._closed = True
        try:
            self.win.destroy()
        except tk.TclError:
            pass


def _make_overlay(
    master: tk.Misc | None,
    title: str,
    bg: str,
) -> tuple[tk.Misc, bool]:
    """Create either a standalone Tk root or a Toplevel under an existing app."""
    x, y, width, height = virtual_screen_bounds()
    if master is None:
        win: tk.Misc = tk.Tk()
        owns_loop = True
    else:
        win = tk.Toplevel(master)
        owns_loop = False
    win.title(title)
    win.overrideredirect(True)
    win.attributes("-topmost", True)
    win.configure(bg=bg)
    win.geometry(f"{width}x{height}+{x}+{y}")
    win.focus_force()
    return win, owns_loop


def blink(
    fade_seconds: float = FADE_SECONDS,
    master: tk.Misc | None = None,
    done: threading.Event | None = None,
) -> None:
    """Fullscreen black overlay: fade to black, then back to normal."""
    win, owns_loop = _make_overlay(master, "EyeRest", "black")
    win.attributes("-alpha", 0.0)

    finished = {"value": False}

    def finish(_event: object | None = None) -> None:
        if finished["value"]:
            return
        finished["value"] = True
        close_overlay(win, done)

    win.bind("<Escape>", finish)
    win.bind("<Button-1>", finish)

    total_steps = max(1, int(fade_seconds * FPS))
    frame_ms = max(1, int(1000 / FPS))
    step = {"i": 0}

    def tick() -> None:
        if finished["value"]:
            return
        i = step["i"]
        if i >= total_steps:
            finish()
            return

        progress = i / (total_steps - 1) if total_steps > 1 else 1.0
        alpha = progress * 2 if progress <= 0.5 else 2 * (1.0 - progress)
        try:
            win.attributes("-alpha", max(0.0, min(1.0, alpha)))
        except tk.TclError:
            finish()
            return

        step["i"] = i + 1
        win.after(frame_ms, tick)

    win.after(0, tick)
    if owns_loop:
        win.mainloop()


def _load_photo(
    path: Path,
    max_side: int,
    master: tk.Misc,
) -> tk.PhotoImage | None:
    """Load a sprite into the given Tk window (master must own the overlay)."""
    if not path.is_file():
        return None
    try:
        from PIL import Image, ImageTk
    except ImportError:
        return tk.PhotoImage(file=str(path), master=master)

    img = Image.open(path).convert("RGBA")
    w, h = img.size
    scale = max_side / max(w, h)
    if abs(scale - 1.0) > 0.01:
        img = img.resize(
            (max(1, int(w * scale)), max(1, int(h * scale))),
            Image.Resampling.LANCZOS,
        )
    return ImageTk.PhotoImage(img, master=master)


def _load_cat_kit(
    max_side: int,
    master: tk.Misc,
) -> dict[str, list[tk.PhotoImage]]:
    """Load walk / stretch / lie sprite frames for a specific Tk window."""
    kit: dict[str, list[tk.PhotoImage]] = {"walk": [], "stretch": [], "lie": []}

    for pose, names in CAT_FRAMES.items():
        for name in names:
            photo = _load_photo(ASSETS_DIR / name, max_side, master)
            if photo is not None:
                kit[pose].append(photo)

    return kit


def long_break(
    duration_seconds: float = LONG_BREAK_SECONDS,
    master: tk.Misc | None = None,
    done: threading.Event | None = None,
) -> None:
    """Block the screen; cat walks in, stretches, lies down, then leaves."""
    x, y, width, height = virtual_screen_bounds()
    frame_ms = max(1, int(1000 / FPS))

    BG = "#1c1916"
    FLOOR = "#2a2520"
    win, owns_loop = _make_overlay(master, "EyeRest - long break", BG)
    win.attributes("-alpha", 1.0)

    canvas = tk.Canvas(
        win,
        width=width,
        height=height,
        bg=BG,
        highlightthickness=0,
    )
    canvas.pack(fill="both", expand=True)
    canvas.create_rectangle(0, 0, width, height, fill=BG, outline="")

    rest_x = width * 0.75
    rest_y = height * 0.58
    start_x = width + 220
    exit_x = -260
    cat_size = min(480, int(min(width, height) * 0.48))

    floor_y = height * 0.72
    canvas.create_oval(
        rest_x - width * 0.16,
        floor_y - 28,
        rest_x + width * 0.16,
        floor_y + 36,
        fill=FLOOR,
        outline="",
    )

    kit = _load_cat_kit(cat_size, master=win)
    win._cat_kit = kit  # type: ignore[attr-defined]

    finished = {"value": False}

    def finish() -> None:
        if finished["value"]:
            return
        finished["value"] = True
        close_overlay(win, done)

    if not kit["walk"]:
        canvas.create_text(
            width / 2,
            height / 2,
            text="(cat images missing)",
            fill="#f7e8d8",
            font=("Segoe UI", 22),
        )
        win.after(2000, finish)
        if owns_loop:
            win.mainloop()
        return

    first = kit["walk"][0]
    cat_id = canvas.create_image(start_x, rest_y, image=first, anchor="center")

    message = canvas.create_text(
        width / 2,
        height * 0.18,
        text="Time for a longer break",
        fill="#f3ebe3",
        font=("Segoe UI Semibold", 28),
        anchor="center",
    )
    subtitle = canvas.create_text(
        width / 2,
        height * 0.18 + 42,
        text="A little friend is settling in...",
        fill="#c4b5a5",
        font=("Segoe UI", 16),
        anchor="center",
    )
    timer_text = canvas.create_text(
        width / 2,
        height * 0.18 + 84,
        text="",
        fill="#e8d3b8",
        font=("Segoe UI", 18),
        anchor="center",
    )
    hint = canvas.create_text(
        width / 2,
        height * 0.92,
        text="Press Esc to gently send the cat away",
        fill="#8a7f74",
        font=("Segoe UI", 13),
        anchor="center",
    )

    state = {
        "phase": "walk",
        "phase_t0": time.perf_counter(),
        "cat_x": float(start_x),
        "pose": "walk",
        "walk_frame": 0,
        "exit_from_x": float(rest_x),
    }

    def set_pose(pose: str, frame_index: int | None = None) -> None:
        frames = kit.get(pose) or kit["lie"]
        if not frames:
            return
        if pose == "walk":
            idx = (frame_index if frame_index is not None else state["walk_frame"]) % len(frames)
            state["walk_frame"] = idx
            canvas.itemconfigure(cat_id, image=frames[idx])
        else:
            canvas.itemconfigure(cat_id, image=frames[0])
        state["pose"] = pose

    def place_cat(nx: float, ny: float, bob: float = 0.0) -> None:
        state["cat_x"] = nx
        canvas.coords(cat_id, nx, ny + bob)

    def begin_exit(_event: object | None = None) -> None:
        if state["phase"] in ("exit", "done") or finished["value"]:
            return
        state["exit_from_x"] = state["cat_x"]
        state["phase"] = "exit"
        state["phase_t0"] = time.perf_counter()
        set_pose("walk", 0)
        canvas.itemconfigure(hint, text="Okay... walking away...")
        canvas.itemconfigure(subtitle, text="See you after a few more blinks.")

    win.bind("<Escape>", begin_exit)

    def format_mmss(seconds: float) -> str:
        secs = max(0, int(math.ceil(seconds)))
        return f"{secs // 60}:{secs % 60:02d} left"

    def tick() -> None:
        if finished["value"]:
            return
        if state["phase"] == "done":
            finish()
            return

        now = time.perf_counter()
        elapsed = now - state["phase_t0"]

        try:
            if state["phase"] == "walk":
                t = ease_in_out(min(1.0, elapsed / WALK_SECONDS))
                nx = start_x + (rest_x - start_x) * t
                bob = abs(math.sin(elapsed * WALK_FRAME_HZ * math.pi)) * 8
                frame_i = int(elapsed * WALK_FRAME_HZ) % max(1, len(kit["walk"]))
                set_pose("walk", frame_i)
                place_cat(nx, rest_y, bob)
                canvas.itemconfigure(subtitle, text="Walking over to rest with you...")
                canvas.itemconfigure(timer_text, text=format_mmss(duration_seconds))
                if elapsed >= WALK_SECONDS:
                    place_cat(rest_x, rest_y, 0)
                    state["phase"] = "stretch"
                    state["phase_t0"] = now
                    set_pose("stretch")
                    canvas.itemconfigure(subtitle, text="Big stretch...")

            elif state["phase"] == "stretch":
                t = ease_in_out(min(1.0, elapsed / STRETCH_SECONDS))
                bob = math.sin(t * math.pi) * -12
                place_cat(rest_x, rest_y, bob)
                canvas.itemconfigure(timer_text, text=format_mmss(duration_seconds))
                if elapsed >= STRETCH_SECONDS:
                    state["phase"] = "lie_down"
                    state["phase_t0"] = now
                    set_pose("lie")
                    canvas.itemconfigure(subtitle, text="Lying down...")

            elif state["phase"] == "lie_down":
                t = ease_in_out(min(1.0, elapsed / LIE_SECONDS))
                bob = (1.0 - t) * 18
                place_cat(rest_x, rest_y + 10 * t, bob)
                canvas.itemconfigure(timer_text, text=format_mmss(duration_seconds))
                if elapsed >= LIE_SECONDS:
                    place_cat(rest_x, rest_y + 10, 0)
                    state["phase"] = "rest"
                    state["phase_t0"] = now
                    canvas.itemconfigure(
                        subtitle,
                        text="Your cat is resting. Stretch, look away, breathe.",
                    )

            elif state["phase"] == "rest":
                set_pose("lie")
                bob = math.sin(elapsed * 1.15) * 3.5
                place_cat(rest_x, rest_y + 10, bob)
                remaining = duration_seconds - elapsed
                canvas.itemconfigure(timer_text, text=format_mmss(remaining))
                if remaining <= 0:
                    state["exit_from_x"] = state["cat_x"]
                    state["phase"] = "exit"
                    state["phase_t0"] = now
                    set_pose("walk", 0)
                    canvas.itemconfigure(hint, text="Break over - off the cat goes...")
                    canvas.itemconfigure(subtitle, text="Nice rest. Back to it gently.")

            elif state["phase"] == "exit":
                t = ease_in_out(min(1.0, elapsed / WALK_SECONDS))
                from_x = state["exit_from_x"]
                nx = from_x + (exit_x - from_x) * t
                bob = abs(math.sin(elapsed * WALK_FRAME_HZ * math.pi)) * 8
                frame_i = int(elapsed * WALK_FRAME_HZ) % max(1, len(kit["walk"]))
                set_pose("walk", frame_i)
                place_cat(nx, rest_y, bob)
                if elapsed >= WALK_SECONDS:
                    state["phase"] = "done"
                    finish()
                    return
        except tk.TclError:
            finish()
            return

        _ = message
        win.after(frame_ms, tick)

    win.after(0, tick)
    if owns_loop:
        win.mainloop()


class EyeRestApp:
    """Single-Tk app: timer panel on the main thread, overlays as Toplevels."""

    def __init__(
        self,
        interval_seconds: float,
        fade_seconds: float,
        long_break_seconds: float,
        long_break_every: int,
        demo_first: bool,
        warn_seconds: float = WARN_SECONDS,
    ) -> None:
        self.interval_seconds = interval_seconds
        self.fade_seconds = fade_seconds
        self.long_break_seconds = long_break_seconds
        self.long_break_every = max(1, long_break_every)
        self.demo_first = demo_first
        self.warn_seconds = max(0.0, warn_seconds)

        self._deadline = time.monotonic() + interval_seconds
        self._paused = False
        self._remaining_when_paused = 0.0
        self._blink_count = 0
        self._lock = threading.Lock()
        self._wake_event = threading.Event()
        self._busy = False
        self._warning: CatBreakWarning | None = None
        self._warning_logged = False

        self.root = tk.Tk()
        self.root.title("EyeRest timer")
        self.root.attributes("-topmost", True)
        self.root.resizable(False, False)
        panel_x, panel_y = primary_monitor_origin()
        self.root.geometry(f"+{panel_x}+{panel_y}")
        self.root.configure(bg="#25211d")
        self.root.protocol("WM_DELETE_WINDOW", self.root.iconify)

        self._build_panel()

        self._worker = threading.Thread(
            target=self._reminder_loop,
            name="EyeRest reminder loop",
            daemon=True,
        )
        self._worker.start()

    def set_blink_count(self, blink_count: int) -> None:
        with self._lock:
            self._blink_count = blink_count

    def blink_count(self) -> int:
        with self._lock:
            return self._blink_count

    def current_phase(self) -> int:
        """1-based place in the cycle. Phase 1 = 3 shorts then cat; last = cat next."""
        with self._lock:
            return (self._blink_count % self.long_break_every) + 1

    def set_phase(self, phase: int) -> None:
        """Jump the cycle so the upcoming sessions match this phase."""
        cycle = self.long_break_every
        phase = max(1, min(cycle, int(phase)))
        with self._lock:
            completed_in_cycle = phase - 1
            base = self._blink_count - (self._blink_count % cycle)
            self._blink_count = base + completed_in_cycle
        self._wake_event.set()

    def start_now(self) -> None:
        with self._lock:
            self._paused = False
            self._remaining_when_paused = 0.0
            self._deadline = time.monotonic()
        self._wake_event.set()

    def consume_next_session(self) -> str:
        with self._lock:
            self._blink_count += 1
            if self._blink_count % self.long_break_every == 0:
                return SESSION_LONG
            return SESSION_BLINK

    def planned_next_session(self) -> str:
        with self._lock:
            upcoming = self._blink_count + 1
            if upcoming % self.long_break_every == 0:
                return SESSION_LONG
            return SESSION_BLINK

    def reset(self) -> None:
        with self._lock:
            self._paused = False
            self._remaining_when_paused = 0.0
            self._deadline = time.monotonic() + self.interval_seconds
        self._wake_event.set()

    def pause(self) -> None:
        with self._lock:
            if self._paused:
                return
            self._remaining_when_paused = max(0.0, self._deadline - time.monotonic())
            self._paused = True
        self._wake_event.set()

    def resume(self) -> None:
        with self._lock:
            if not self._paused:
                return
            self._deadline = time.monotonic() + self._remaining_when_paused
            self._paused = False
            self._remaining_when_paused = 0.0
        self._wake_event.set()

    def toggle_pause(self) -> None:
        if self.is_paused():
            self.resume()
        else:
            self.pause()

    def is_paused(self) -> bool:
        with self._lock:
            return self._paused

    def seconds_remaining(self) -> float:
        with self._lock:
            if self._paused:
                return max(0.0, self._remaining_when_paused)
            return max(0.0, self._deadline - time.monotonic())

    def wait_until_due(self) -> None:
        while True:
            with self._lock:
                paused = self._paused
                remaining = (
                    self._remaining_when_paused
                    if paused
                    else self._deadline - time.monotonic()
                )
            if not paused and remaining <= 0:
                return
            timeout = 0.25 if paused else min(max(remaining, 0.0), 0.25)
            self._wake_event.wait(timeout=timeout)
            self._wake_event.clear()

    def _wait_seconds_or_wake(self, seconds: float) -> None:
        deadline = time.monotonic() + max(0.0, seconds)
        while True:
            left = deadline - time.monotonic()
            if left <= 0 or self.is_paused():
                return
            if self.seconds_remaining() > seconds + 0.5:
                return
            self._wake_event.wait(timeout=min(left, 0.25))
            self._wake_event.clear()

    def _open_warning_widget(self) -> None:
        if self._warning is not None:
            try:
                if self._warning.win.winfo_exists():
                    return
            except tk.TclError:
                self._warning = None
        self._warning = CatBreakWarning(
            self.root,
            remaining_fn=self.seconds_remaining,
            break_seconds=self.long_break_seconds,
        )

    def _close_warning_widget(self) -> None:
        warning = self._warning
        self._warning = None
        if warning is not None:
            warning.destroy()

    def _show_cat_warning(self) -> None:
        remaining = self.seconds_remaining()
        lead = remaining if remaining > 0.05 else min(self.warn_seconds, START_NOW_WARN_SECONDS)
        if not self._warning_logged:
            self._warning_logged = True
            secs = max(1, int(math.ceil(lead)))
            print(
                f"[{time.strftime('%H:%M:%S')}] Cat break in {secs}s — "
                "screen will go dark."
            )
        self.root.after(0, self._open_warning_widget)

    def _hide_cat_warning(self) -> None:
        if not self._warning_logged and self._warning is None:
            return
        self._warning_logged = False
        self.root.after(0, self._close_warning_widget)

    def _run_session_on_ui(self, session: str) -> None:
        """Run blink/long-break on the Tk thread as a Toplevel, wait until done."""
        done = threading.Event()

        def start() -> None:
            self._close_warning_widget()
            if session == SESSION_LONG:
                long_break(self.long_break_seconds, master=self.root, done=done)
            else:
                blink(self.fade_seconds, master=self.root, done=done)

        self.root.after(0, start)
        done.wait()

    def _reminder_loop(self) -> None:
        try:
            if self.demo_first:
                print("Demo blink in 2 seconds...")
                time.sleep(2)
                self._run_session_on_ui(SESSION_BLINK)
                self.set_blink_count(1)
                self.reset()
                print(
                    "Next event in "
                    f"{self.interval_seconds / 60:.0f} minutes "
                    f"({time.strftime('%H:%M:%S', time.localtime(time.time() + self.interval_seconds))})."
                )

            while True:
                if self._busy:
                    time.sleep(0.2)
                    continue

                paused = self.is_paused()
                remaining = self.seconds_remaining()
                next_session = self.planned_next_session()
                warn = self.warn_seconds if next_session == SESSION_LONG else 0.0

                if paused:
                    self._hide_cat_warning()
                    self._wake_event.wait(timeout=0.25)
                    self._wake_event.clear()
                    continue

                if next_session == SESSION_LONG and warn > 0 and remaining > 0.05:
                    if remaining <= warn:
                        self._show_cat_warning()
                    else:
                        self._hide_cat_warning()
                    wait_for = remaining if remaining <= warn else remaining - warn
                    self._wake_event.wait(timeout=min(max(wait_for, 0.0), 0.25))
                    self._wake_event.clear()
                    continue

                if remaining > 0.05:
                    self._hide_cat_warning()
                    self._wake_event.wait(timeout=min(max(remaining, 0.0), 0.25))
                    self._wake_event.clear()
                    continue

                if next_session == SESSION_LONG and warn > 0 and not self._warning_logged:
                    self._show_cat_warning()
                    self._wait_seconds_or_wake(min(warn, START_NOW_WARN_SECONDS))
                    self._hide_cat_warning()
                    if self.is_paused() or self.seconds_remaining() > 0.05:
                        continue
                    if self.planned_next_session() != SESSION_LONG:
                        continue

                self._busy = True
                try:
                    session = self.consume_next_session()
                    count = self.blink_count()
                    stamp = time.strftime("%H:%M:%S")

                    if session == SESSION_LONG:
                        print(f"[{stamp}] Long break - cat is coming to rest...")
                    else:
                        print(
                            f"[{stamp}] Rest your eyes - blinking "
                            f"({count % self.long_break_every}/"
                            f"{self.long_break_every} until long break)..."
                        )
                    self._run_session_on_ui(session)
                    self.reset()
                    next_at = time.time() + self.interval_seconds
                    print(
                        "Next event at "
                        f"{time.strftime('%H:%M:%S', time.localtime(next_at))}."
                    )
                finally:
                    self._busy = False
                    self._hide_cat_warning()
        except Exception as exc:  # noqa: BLE001 - keep panel alive, log the failure
            print(f"EyeRest reminder loop error: {exc}")

    def _build_panel(self) -> None:
        frame = tk.Frame(self.root, bg="#25211d", padx=12, pady=10)
        frame.pack()
        title = tk.Label(
            frame,
            text="Next eye rest",
            bg="#25211d",
            fg="#d9c8b6",
            font=("Segoe UI", 9),
            cursor="fleur",
        )
        title.pack(anchor="w")
        countdown = tk.Label(
            frame,
            bg="#25211d",
            fg="#fff4e8",
            font=("Segoe UI Semibold", 18),
            cursor="fleur",
        )
        countdown.pack(anchor="w", pady=(0, 2))
        next_label = tk.Label(
            frame,
            text="",
            bg="#25211d",
            fg="#d9c8b6",
            font=("Segoe UI", 9),
            cursor="fleur",
        )
        next_label.pack(anchor="w")
        status = tk.Label(
            frame,
            text="",
            bg="#25211d",
            fg="#c9a46c",
            font=("Segoe UI", 8),
            cursor="fleur",
        )
        status.pack(anchor="w")
        drag_hint = tk.Label(
            frame,
            text="Drag to move",
            bg="#25211d",
            fg="#7a6f64",
            font=("Segoe UI", 8),
            cursor="fleur",
        )
        drag_hint.pack(anchor="w", pady=(0, 8))

        button_style = {
            "fg": "#ffffff",
            "activeforeground": "#ffffff",
            "relief": "flat",
            "padx": 10,
            "pady": 4,
            "font": ("Segoe UI Semibold", 9),
            "cursor": "hand2",
        }

        cycle = self.long_break_every
        cycle_grid = tk.Frame(frame, bg="#25211d")
        cycle_grid.pack(fill="x", pady=(0, 6))
        cols = 2 if cycle > 2 else cycle
        phase_buttons: dict[int, tk.Button] = {}
        for phase in range(1, cycle + 1):
            shorts_left = cycle - phase
            label = "Cat next" if shorts_left <= 0 else f"{shorts_left} then cat"
            btn = tk.Button(
                cycle_grid,
                text=label,
                command=lambda p=phase: self.set_phase(p),
                bg="#5a6b7a",
                activebackground="#6f8294",
                **button_style,
            )
            row, col = divmod(phase - 1, cols)
            last_row = (cycle - 1) // cols
            padx = (0, 3) if col < cols - 1 else (0, 0)
            pady = (0, 3) if row < last_row else (0, 0)
            btn.grid(row=row, column=col, sticky="ew", padx=padx, pady=pady)
            phase_buttons[phase] = btn
        for col in range(cols):
            cycle_grid.columnconfigure(col, weight=1)

        tk.Button(
            frame,
            text="Start now",
            command=self.start_now,
            bg="#8b5e3c",
            activebackground="#a47149",
            **button_style,
        ).pack(fill="x", pady=(0, 6))

        pause_btn = tk.Button(
            frame,
            text="Pause",
            command=self.toggle_pause,
            bg="#5a6b7a",
            activebackground="#6f8294",
            **button_style,
        )
        pause_btn.pack(fill="x", pady=(0, 6))

        reset_minutes = max(1, int(round(self.interval_seconds / 60)))
        tk.Button(
            frame,
            text=f"Reset {reset_minutes} min",
            command=self.reset,
            bg="#c77d4f",
            activebackground="#dc9568",
            **button_style,
        ).pack(fill="x")

        drag_offset = {"x": 0, "y": 0}

        def start_drag(event: tk.Event) -> None:
            drag_offset["x"] = event.x_root - self.root.winfo_x()
            drag_offset["y"] = event.y_root - self.root.winfo_y()

        def on_drag(event: tk.Event) -> None:
            self.root.geometry(
                f"+{event.x_root - drag_offset['x']}+{event.y_root - drag_offset['y']}"
            )

        for widget in (self.root, frame, title, countdown, next_label, status, drag_hint):
            widget.bind("<ButtonPress-1>", start_drag)
            widget.bind("<B1-Motion>", on_drag)

        def session_caption(session: str, phase: int) -> str:
            shorts_left = self.long_break_every - phase
            if session == SESSION_LONG:
                return "Next: Cat break"
            if shorts_left == 1:
                return "Next: Short blink, then cat"
            return f"Next: Short blink · {shorts_left} then cat"

        def update_countdown() -> None:
            seconds = int(math.ceil(self.seconds_remaining()))
            paused = self.is_paused()
            session = self.planned_next_session()
            phase = self.current_phase()
            countdown.config(text=f"{seconds // 60:02d}:{seconds % 60:02d}")
            next_label.config(text=session_caption(session, phase))

            selected_bg = "#3f7a5a"
            selected_active = "#4f956c"
            idle_bg = "#5a6b7a"
            idle_active = "#6f8294"
            for p, btn in phase_buttons.items():
                selected = p == phase
                btn.config(
                    bg=selected_bg if selected else idle_bg,
                    activebackground=selected_active if selected else idle_active,
                )

            if paused:
                status.config(text="Paused")
                pause_btn.config(text="Resume", bg="#3f7a5a", activebackground="#4f956c")
                countdown.config(fg="#c9a46c")
            elif (
                session == SESSION_LONG
                and self.warn_seconds > 0
                and 0 < seconds <= int(math.ceil(self.warn_seconds))
            ):
                status.config(text="Cat break coming")
                pause_btn.config(text="Pause", bg="#5a6b7a", activebackground="#6f8294")
                countdown.config(fg="#fff4e8")
            else:
                status.config(text="")
                pause_btn.config(text="Pause", bg="#5a6b7a", activebackground="#6f8294")
                countdown.config(fg="#fff4e8")
            self.root.after(250, update_countdown)

        update_countdown()

    def run(self) -> None:
        print("EyeRest is running.")
        print(f"  Interval    : every {self.interval_seconds / 60:.0f} minutes")
        print(f"  Blink       : {self.fade_seconds:.1f}s fade to black and back")
        print(
            f"  Long break  : every {self.long_break_every}th blink, cat rests "
            f"{self.long_break_seconds / 60:.0f} minutes"
        )
        if self.warn_seconds > 0:
            print(
                f"  Heads-up    : {self.warn_seconds:.0f}s before each cat break "
                "(does not steal focus)"
            )
        print("  Press Escape during a blink to skip it.")
        print("  Press Escape during a long break to send the cat away.")
        print("  Use the panel to jump to a cycle phase, or Start now.")
        print("  Press Ctrl+C in this window to quit.\n")
        try:
            self.root.mainloop()
        except KeyboardInterrupt:
            print("\nEyeRest stopped.")
            try:
                self.root.destroy()
            except tk.TclError:
                pass


def run_demo_long(duration_seconds: float, warn_seconds: float) -> None:
    """One cat break, optionally preceded by the heads-up toast, then exit."""
    if warn_seconds <= 0:
        long_break(duration_seconds)
        return

    root = tk.Tk()
    root.withdraw()
    deadline = time.monotonic() + warn_seconds
    print(
        f"Cat break in {warn_seconds:.0f}s — screen will go dark. "
        "You can keep using other windows until then."
    )
    warning = CatBreakWarning(
        root,
        remaining_fn=lambda: max(0.0, deadline - time.monotonic()),
        break_seconds=duration_seconds,
    )
    finished = threading.Event()

    def start_break() -> None:
        warning.destroy()
        print("Long break - cat is coming to rest...")
        long_break(duration_seconds, master=root, done=finished)

    def poll_finished() -> None:
        if finished.is_set():
            try:
                root.destroy()
            except tk.TclError:
                pass
            return
        root.after(100, poll_finished)

    root.after(max(1, int(warn_seconds * 1000)), start_break)
    root.after(100, poll_finished)
    try:
        root.mainloop()
    except KeyboardInterrupt:
        try:
            root.destroy()
        except tk.TclError:
            pass


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Periodic eye-rest blinks, with a longer cat break every 4th blink.",
    )
    parser.add_argument(
        "--interval",
        type=float,
        default=INTERVAL_SECONDS / 60,
        help="Minutes between blinks (default: 25)",
    )
    parser.add_argument(
        "--fade",
        type=float,
        default=FADE_SECONDS,
        help="Seconds for short blink fade (default: 5)",
    )
    parser.add_argument(
        "--long-break",
        type=float,
        default=LONG_BREAK_SECONDS / 60,
        help="Minutes for the cat long break (default: 5)",
    )
    parser.add_argument(
        "--every",
        type=int,
        default=LONG_BREAK_EVERY,
        help="Take a long break every N blinks (default: 4)",
    )
    parser.add_argument(
        "--once",
        action="store_true",
        help="Run a single short blink and exit",
    )
    parser.add_argument(
        "--demo-long",
        action="store_true",
        help="Run one long cat break and exit",
    )
    parser.add_argument(
        "--no-demo",
        action="store_true",
        help="Skip the startup demo blink",
    )
    parser.add_argument(
        "--warn-seconds",
        type=float,
        default=WARN_SECONDS,
        help="Seconds of heads-up before a cat break (default: 30; 0 disables)",
    )
    args = parser.parse_args()

    if args.demo_long:
        run_demo_long(
            duration_seconds=max(5.0, args.long_break * 60),
            warn_seconds=max(0.0, args.warn_seconds),
        )
        return 0

    if args.once:
        blink(args.fade)
        return 0

    app = EyeRestApp(
        interval_seconds=max(0.1, args.interval) * 60,
        fade_seconds=max(0.5, args.fade),
        long_break_seconds=max(5.0, args.long_break * 60),
        long_break_every=max(1, args.every),
        demo_first=not args.no_demo,
        warn_seconds=max(0.0, args.warn_seconds),
    )
    app.run()
    return 0


if __name__ == "__main__":
    sys.exit(main())
