"""
EyeRest — every 25 minutes, fade the screen to black and back over 5 seconds
as a gentle reminder to rest your eyes.

Every 4th blink is a longer break: a realistic cat walks in, stretches,
lies down for 5 minutes, then walks away (or sooner if you press Escape).
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

# Win32 virtual-screen metrics (all monitors)
SM_XVIRTUALSCREEN = 76
SM_YVIRTUALSCREEN = 77
SM_CXVIRTUALSCREEN = 78
SM_CYVIRTUALSCREEN = 79
MONITOR_DEFAULTTOPRIMARY = 1


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


def ease_in_out(t: float) -> float:
    t = max(0.0, min(1.0, t))
    return t * t * (3.0 - 2.0 * t)


def blink(fade_seconds: float = FADE_SECONDS) -> None:
    """Fullscreen black overlay: fade to black, then back to normal."""
    x, y, width, height = virtual_screen_bounds()

    root = tk.Tk()
    root.title("EyeRest")
    root.overrideredirect(True)
    root.attributes("-topmost", True)
    root.configure(bg="black")
    root.geometry(f"{width}x{height}+{x}+{y}")
    root.attributes("-alpha", 0.0)
    root.focus_force()

    dismissed = {"value": False}

    def dismiss(_event: object | None = None) -> None:
        dismissed["value"] = True
        root.destroy()

    root.bind("<Escape>", dismiss)
    root.bind("<Button-1>", dismiss)

    total_steps = max(1, int(fade_seconds * FPS))
    frame_ms = max(1, int(1000 / FPS))
    step = {"i": 0}

    def tick() -> None:
        if dismissed["value"]:
            return
        i = step["i"]
        if i >= total_steps:
            root.destroy()
            return

        progress = i / (total_steps - 1) if total_steps > 1 else 1.0
        alpha = progress * 2 if progress <= 0.5 else 2 * (1.0 - progress)
        root.attributes("-alpha", max(0.0, min(1.0, alpha)))

        step["i"] = i + 1
        root.after(frame_ms, tick)

    root.after(0, tick)
    root.mainloop()


def _load_photo(path: Path, max_side: int) -> tk.PhotoImage | None:
    if not path.is_file():
        return None
    try:
        from PIL import Image, ImageTk
    except ImportError:
        return tk.PhotoImage(file=str(path))

    img = Image.open(path).convert("RGBA")
    w, h = img.size
    scale = max_side / max(w, h)
    if abs(scale - 1.0) > 0.01:
        img = img.resize(
            (max(1, int(w * scale)), max(1, int(h * scale))),
            Image.Resampling.LANCZOS,
        )
    return ImageTk.PhotoImage(img)


def _load_cat_kit(max_side: int) -> dict[str, list[tk.PhotoImage]]:
    """Load walk / stretch / lie sprite frames."""
    kit: dict[str, list[tk.PhotoImage]] = {"walk": [], "stretch": [], "lie": []}

    for pose, names in CAT_FRAMES.items():
        for name in names:
            photo = _load_photo(ASSETS_DIR / name, max_side)
            if photo is not None:
                kit[pose].append(photo)

    return kit


def long_break(duration_seconds: float = LONG_BREAK_SECONDS) -> None:
    """Block the screen; cat walks in, stretches, lies down, then leaves.

    Uses an opaque warm backdrop from the start so neither the desktop nor a
    chroma-key color can bleed into the cat.
    """
    x, y, width, height = virtual_screen_bounds()
    frame_ms = max(1, int(1000 / FPS))

    # Warm charcoal — complements cream fur; opaque so colors stay true
    BG = "#1c1916"
    FLOOR = "#2a2520"
    root = tk.Tk()
    root.title("EyeRest - long break")
    root.overrideredirect(True)
    root.attributes("-topmost", True)
    root.configure(bg=BG)
    root.geometry(f"{width}x{height}+{x}+{y}")
    root.attributes("-alpha", 1.0)
    root.focus_force()

    canvas = tk.Canvas(
        root,
        width=width,
        height=height,
        bg=BG,
        highlightthickness=0,
    )
    canvas.pack(fill="both", expand=True)
    background = canvas.create_rectangle(
        0, 0, width, height, fill=BG, outline=""
    )

    rest_x = width * 0.75
    rest_y = height * 0.58
    start_x = width + 220
    exit_x = -260
    cat_size = min(480, int(min(width, height) * 0.48))

    # Soft floor shadow under the resting spot
    floor_y = height * 0.72
    floor_shadow = canvas.create_oval(
        rest_x - width * 0.16,
        floor_y - 28,
        rest_x + width * 0.16,
        floor_y + 36,
        fill=FLOOR,
        outline="",
    )

    kit = _load_cat_kit(cat_size)
    # Prevent GC of PhotoImage refs
    root._cat_kit = kit  # type: ignore[attr-defined]

    if not any(kit.values()):
        canvas.create_text(
            width / 2,
            height / 2,
            text="(cat images missing)",
            fill="#f7e8d8",
            font=("Segoe UI", 22),
        )
        root.after(2000, root.destroy)
        root.mainloop()
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
        # walk -> stretch -> lie_down -> rest -> exit -> done
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
        if state["phase"] in ("exit", "done"):
            return
        state["exit_from_x"] = state["cat_x"]
        state["phase"] = "exit"
        state["phase_t0"] = time.perf_counter()
        set_pose("walk", 0)
        canvas.itemconfigure(hint, text="Okay... walking away...")
        canvas.itemconfigure(subtitle, text="See you after a few more blinks.")

    root.bind("<Escape>", begin_exit)

    def format_mmss(seconds: float) -> str:
        secs = max(0, int(math.ceil(seconds)))
        return f"{secs // 60}:{secs % 60:02d} left"

    def tick() -> None:
        if state["phase"] == "done":
            root.destroy()
            return

        now = time.perf_counter()
        elapsed = now - state["phase_t0"]

        if state["phase"] == "walk":
            t = ease_in_out(min(1.0, elapsed / WALK_SECONDS))
            nx = start_x + (rest_x - start_x) * t
            # Walk bob + alternate stride frames
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
            # Settle forward a little during stretch
            t = ease_in_out(min(1.0, elapsed / STRETCH_SECONDS))
            bob = math.sin(t * math.pi) * -12  # dips into the stretch
            place_cat(rest_x, rest_y, bob)
            canvas.itemconfigure(timer_text, text=format_mmss(duration_seconds))
            if elapsed >= STRETCH_SECONDS:
                state["phase"] = "lie_down"
                state["phase_t0"] = now
                set_pose("lie")
                canvas.itemconfigure(subtitle, text="Lying down...")

        elif state["phase"] == "lie_down":
            t = ease_in_out(min(1.0, elapsed / LIE_SECONDS))
            # Soft drop into resting pose
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
            # Gentle breathing
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
                root.destroy()
                return

        _ = message
        root.after(frame_ms, tick)

    root.after(0, tick)
    root.mainloop()


class TimerPanel:
    """Small top-left countdown panel shared safely with the reminder loop."""

    def __init__(self, interval_seconds: float) -> None:
        self.interval_seconds = interval_seconds
        self._deadline = time.monotonic() + interval_seconds
        self._lock = threading.Lock()
        self._reset_event = threading.Event()
        self._thread = threading.Thread(
            target=self._run_window,
            name="EyeRest timer panel",
            daemon=True,
        )
        self._thread.start()

    def reset(self) -> None:
        """Postpone the next reminder by one full interval."""
        with self._lock:
            self._deadline = time.monotonic() + self.interval_seconds
        self._reset_event.set()

    def seconds_remaining(self) -> float:
        with self._lock:
            return max(0.0, self._deadline - time.monotonic())

    def wait_until_due(self) -> None:
        """Wait for the deadline, waking immediately when Reset is clicked."""
        while True:
            remaining = self.seconds_remaining()
            if remaining <= 0:
                return
            self._reset_event.wait(timeout=min(remaining, 0.25))
            self._reset_event.clear()

    def _run_window(self) -> None:
        root = tk.Tk()
        root.title("EyeRest timer")
        root.attributes("-topmost", True)
        root.resizable(False, False)
        panel_x, panel_y = primary_monitor_origin()
        root.geometry(f"+{panel_x}+{panel_y}")
        root.configure(bg="#25211d")
        root.protocol("WM_DELETE_WINDOW", root.iconify)

        frame = tk.Frame(root, bg="#25211d", padx=12, pady=10)
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
        countdown.pack(anchor="w", pady=(0, 4))
        drag_hint = tk.Label(
            frame,
            text="Drag to move",
            bg="#25211d",
            fg="#7a6f64",
            font=("Segoe UI", 8),
            cursor="fleur",
        )
        drag_hint.pack(anchor="w", pady=(0, 7))
        reset_minutes = max(1, int(round(self.interval_seconds / 60)))
        tk.Button(
            frame,
            text=f"Reset {reset_minutes} min",
            command=self.reset,
            bg="#c77d4f",
            fg="#ffffff",
            activebackground="#dc9568",
            activeforeground="#ffffff",
            relief="flat",
            padx=10,
            pady=4,
            font=("Segoe UI Semibold", 9),
            cursor="hand2",
        ).pack(fill="x")

        drag_offset = {"x": 0, "y": 0}

        def start_drag(event: tk.Event) -> None:
            drag_offset["x"] = event.x_root - root.winfo_x()
            drag_offset["y"] = event.y_root - root.winfo_y()

        def on_drag(event: tk.Event) -> None:
            root.geometry(
                f"+{event.x_root - drag_offset['x']}+{event.y_root - drag_offset['y']}"
            )

        for widget in (root, frame, title, countdown, drag_hint):
            widget.bind("<ButtonPress-1>", start_drag)
            widget.bind("<B1-Motion>", on_drag)

        def update_countdown() -> None:
            seconds = int(math.ceil(self.seconds_remaining()))
            countdown.config(text=f"{seconds // 60:02d}:{seconds % 60:02d}")
            root.after(250, update_countdown)

        update_countdown()
        root.mainloop()


def run_loop(
    interval_seconds: float,
    fade_seconds: float,
    long_break_seconds: float,
    long_break_every: int,
    demo_first: bool,
) -> None:
    print("EyeRest is running.")
    print(f"  Interval    : every {interval_seconds / 60:.0f} minutes")
    print(f"  Blink       : {fade_seconds:.1f}s fade to black and back")
    print(
        f"  Long break  : every {long_break_every}th blink, cat rests "
        f"{long_break_seconds / 60:.0f} minutes"
    )
    print("  Press Escape during a blink to skip it.")
    print("  Press Escape during a long break to send the cat away.")
    print("  The top-left timer panel can reset the next reminder.")
    print("  Press Ctrl+C in this window to quit.\n")

    blink_count = 0
    timer_panel = TimerPanel(interval_seconds)

    try:
        if demo_first:
            print("Demo blink in 2 seconds...")
            time.sleep(2)
            blink(fade_seconds)
            blink_count = 1
            timer_panel.reset()
            print(
                "Next event in "
                f"{interval_seconds / 60:.0f} minutes "
                f"({time.strftime('%H:%M:%S', time.localtime(time.time() + interval_seconds))})."
            )

        while True:
            timer_panel.wait_until_due()
            blink_count += 1
            stamp = time.strftime("%H:%M:%S")

            if blink_count % long_break_every == 0:
                print(
                    f"[{stamp}] Long break #{blink_count // long_break_every} "
                    f"- cat is coming to rest..."
                )
                long_break(long_break_seconds)
            else:
                print(
                    f"[{stamp}] Rest your eyes - blinking "
                    f"({blink_count % long_break_every}/{long_break_every} until long break)..."
                )
                blink(fade_seconds)

            timer_panel.reset()
            next_at = time.time() + interval_seconds
            print(
                "Next event at "
                f"{time.strftime('%H:%M:%S', time.localtime(next_at))}."
            )
    except KeyboardInterrupt:
        print("\nEyeRest stopped.")


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
    args = parser.parse_args()

    if args.demo_long:
        long_break(max(5.0, args.long_break * 60))
        return 0

    if args.once:
        blink(args.fade)
        return 0

    run_loop(
        interval_seconds=max(0.1, args.interval) * 60,
        fade_seconds=max(0.5, args.fade),
        long_break_seconds=max(5.0, args.long_break * 60),
        long_break_every=max(1, args.every),
        demo_first=not args.no_demo,
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
