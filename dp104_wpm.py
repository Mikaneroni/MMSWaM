"""
dp104_wpm.py — Typing WPM tracker for DP-104 keyboard display.

Monitors keystrokes via pynput, computes a rolling 60-second average WPM,
and renders a 24×8 HSV pixel frame:

Layout (24 × 8):
  Cols  0–1 : margin
  Cols  2–11: 10-bar history graph (one bar per minute, left=oldest right=newest)
  Col  12   : separator
  Cols 13–23: current WPM value (3×5 pixel font, right-aligned)

Color mapping (relative to personal best):
  ≥ 80% of PB → RED   (HSV hue ≈ 0)   — pushing hard
  40–80% of PB → YELLOW (HSV hue ≈ 32) — average pace
  < 40% of PB → GREEN  (HSV hue ≈ 90)  — casual typing

Personal best is tracked in memory and optionally cached to a JSON file.
"""

import time
import threading
import collections
import json
import os
from pathlib import Path

try:
    from pynput import keyboard as _kb
    _PYNPUT = True
except ImportError:
    _PYNPUT = False

# Also try Windows ctypes fallback (works even inside tkinter message loop)
try:
    import ctypes as _ct
    _user32 = _ct.windll.user32
    _WIN32 = True
except Exception:
    _WIN32 = False

# ── Config ──────────────────────────────────────────────────────────────────
ROWS        = 8
COLS        = 24
FRAME_BYTES = ROWS * COLS * 3

HISTORY_BARS   = 10    # bars in the left graph (one per minute)
HISTORY_MINS   = 10    # minutes of history to keep

BAR_START_COL  = 2     # first bar column
BAR_END_COL    = 11    # last bar column (10 bars, cols 2-11)
SEP_COL        = 12    # separator column
NUM_START_COL  = 13    # number zone starts here

PB_CACHE_FILE  = Path(__file__).parent / "dp104_wpm_pb.json"

# ── Color helpers ────────────────────────────────────────────────────────────
def _wpm_to_hsv(wpm, personal_best):
    """Map WPM to HSV color relative to personal best."""
    if personal_best <= 0:
        ratio = 0.5
    else:
        ratio = min(1.0, wpm / personal_best)

    # ratio 0.0 → GREEN (h=90), 0.5 → YELLOW (h=32), 1.0 → RED (h=0)
    if ratio >= 0.8:
        hue = 0      # red
    elif ratio >= 0.4:
        # interpolate yellow (32) to red (0)
        t = (ratio - 0.4) / 0.4
        hue = int(32 * (1 - t))
    else:
        # interpolate green (90) to yellow (32)
        t = ratio / 0.4
        hue = int(90 - 58 * t)

    sat = 230
    val = 200
    return hue, sat, val

def _bar_height(wpm, max_wpm):
    """How many pixels tall (1-8) for this WPM bar."""
    if max_wpm <= 0 or wpm <= 0:
        return 0
    return max(1, min(ROWS, round((wpm / max_wpm) * ROWS)))

# ── 3×5 pixel font (same as weather module) ──────────────────────────────────
_DIGITS = {
    '0': [0b111,0b101,0b101,0b101,0b111],
    '1': [0b010,0b110,0b010,0b010,0b111],
    '2': [0b111,0b001,0b111,0b100,0b111],
    '3': [0b111,0b001,0b111,0b001,0b111],
    '4': [0b101,0b101,0b111,0b001,0b001],
    '5': [0b111,0b100,0b111,0b001,0b111],
    '6': [0b111,0b100,0b111,0b101,0b111],
    '7': [0b111,0b001,0b001,0b010,0b010],
    '8': [0b111,0b101,0b111,0b101,0b111],
    '9': [0b111,0b101,0b111,0b001,0b111],
}

def _draw_digits(canvas, wpm_int, col0, color):
    """Draw up to 3 digits right-aligned starting at col0, rows 1-5."""
    text = str(min(999, wpm_int))
    # right-align: each digit is 3 wide + 1 gap
    total_w = len(text) * 4 - 1
    start_c = col0 + (11 - total_w)   # right-align in remaining space
    for char_i, ch in enumerate(text):
        bitmap = _DIGITS.get(ch, _DIGITS['0'])
        dc = start_c + char_i * 4
        for row_i, bits in enumerate(bitmap):
            for bit_i in range(3):
                if bits & (0b100 >> bit_i):
                    c = dc + bit_i
                    r = 1 + row_i
                    if 0 <= c < COLS and 0 <= r < ROWS:
                        canvas[r][c] = color

# ── Frame builder ─────────────────────────────────────────────────────────────
def build_frame(current_wpm, history_wpm, personal_best):
    """
    Build a single 24×8 HSV frame.

    current_wpm  : float — rolling 60-second average right now
    history_wpm  : list of floats, len ≤ 10, oldest first — one per minute
    personal_best: float — highest ever 60s average

    Returns flat list of ROWS*COLS*3 ints (HSV bytes).
    """
    canvas = [[(0, 0, 0)] * COLS for _ in range(ROWS)]

    # ── Left graph: 10 bars (cols 2-11), one per minute of history ──────────
    max_wpm = max(personal_best, max(history_wpm) if history_wpm else 1, 1)

    for bar_i in range(HISTORY_BARS):
        col = BAR_START_COL + bar_i
        # Fill bars from history, oldest leftmost
        hist_idx = bar_i - (HISTORY_BARS - len(history_wpm))
        if hist_idx < 0:
            continue  # no data yet for this bar
        wpm_val = history_wpm[hist_idx]
        height  = _bar_height(wpm_val, max_wpm)
        color   = _wpm_to_hsv(wpm_val, personal_best)
        for row in range(ROWS - 1, ROWS - 1 - height, -1):
            canvas[row][col] = color

    # ── Separator (col 12) — dim single pixel at bottom ─────────────────────
    canvas[ROWS - 1][SEP_COL] = (0, 0, 30)

    # ── Right zone: current WPM number (cols 13-23) ──────────────────────────
    num_color = _wpm_to_hsv(current_wpm, personal_best)
    _draw_digits(canvas, int(round(current_wpm)), NUM_START_COL, num_color)

    # ── "WPM" label at row 6-7, right zone (tiny) ────────────────────────────
    # Just a dim "w" indicator at col 13, row 7
    canvas[7][13] = (0, 0, 20)  # dim white dot

    return [v for row in canvas for (h, s, val) in row for v in (h, s, val)]


# ── WPM tracker ──────────────────────────────────────────────────────────────
class WPMTracker:
    """
    Tracks keystrokes, computes rolling 60-second WPM average,
    and maintains per-minute history for the graph.

    Usage:
        tracker = WPMTracker()
        tracker.start()
        # ... later ...
        frame = tracker.get_frame()
        tracker.stop()
    """

    WORDS_PER_KEY = 1 / 5.0   # standard: 5 keystrokes = 1 word

    def __init__(self, pb_file=None):
        self._lock          = threading.Lock()
        self._keystroke_ts  = collections.deque()   # timestamps of recent keystrokes
        self._minute_history= collections.deque(maxlen=HISTORY_BARS)  # wpm per minute
        self._personal_best = 0.0
        self._current_wpm   = 0.0
        self._running       = False
        self._listener      = None
        self._pb_file       = Path(pb_file) if pb_file else PB_CACHE_FILE
        self._load_pb()

    # ── Persistence ──────────────────────────────────────────────────────────
    def _load_pb(self):
        try:
            if self._pb_file.exists():
                data = json.loads(self._pb_file.read_text())
                self._personal_best = float(data.get('personal_best', 0))
        except Exception:
            pass

    def _save_pb(self):
        try:
            self._pb_file.write_text(json.dumps({'personal_best': round(self._personal_best, 1)}))
        except Exception:
            pass

    # ── Keystroke listener ────────────────────────────────────────────────────
    def _on_press(self, key):
        """Called on every keypress — thread safe."""
        with self._lock:
            self._keystroke_ts.append(time.monotonic())

    def _prune_old(self, now, window=60.0):
        """Remove keystrokes older than window seconds."""
        cutoff = now - window
        while self._keystroke_ts and self._keystroke_ts[0] < cutoff:
            self._keystroke_ts.popleft()

    # ── Compute WPM ──────────────────────────────────────────────────────────
    def _compute_wpm(self):
        now = time.monotonic()
        with self._lock:
            self._prune_old(now)
            count = len(self._keystroke_ts)
        # keys in last 60s → words → WPM
        return count * self.WORDS_PER_KEY

    # ── Background update loop ────────────────────────────────────────────────
    def _update_loop(self):
        """Runs every 50ms: polls key states + updates WPM every second."""
        last_second = time.monotonic()
        last_minute_snap = time.monotonic()

        # Key codes to poll (A-Z = 0x41-0x5A, space=0x20, common punctuation)
        POLL_KEYS = list(range(0x08, 0x08+1)) + [0x20] + \
                    list(range(0x30, 0x3A)) + list(range(0x41, 0x5B)) + \
                    list(range(0xBA, 0xC1)) + list(range(0xDB, 0xE0))
        prev_states = {}

        while self._running:
            time.sleep(0.05)  # 50ms poll interval
            now = time.monotonic()

            if _WIN32:
                # Poll GetAsyncKeyState — use high bit (0x8000 = currently pressed)
                # with edge detection (was up, now down = new keystroke)
                for vk in POLL_KEYS:
                    cur = bool(_user32.GetAsyncKeyState(vk) & 0x8000)
                    prev = prev_states.get(vk, False)
                    if cur and not prev:   # key just went down
                        with self._lock:
                            self._keystroke_ts.append(now)
                    prev_states[vk] = cur
            elif _PYNPUT and self._listener is None:
                # Fallback: start pynput listener if no Win32
                self._listener = _kb.Listener(on_press=self._on_press)
                self._listener.start()

            # Update WPM every second
            if now - last_second >= 1.0:
                last_second = now
                wpm = self._compute_wpm()
                with self._lock:
                    self._current_wpm = wpm
                    if wpm > self._personal_best:
                        self._personal_best = wpm
                        self._save_pb()
                    if now - last_minute_snap >= 60.0:
                        self._minute_history.append(wpm)
                        last_minute_snap = now

    # ── Public API ────────────────────────────────────────────────────────────
    def start(self):
        """Start tracking keystrokes.
        Uses GetAsyncKeyState polling on Windows (works inside GUI process).
        Falls back to pynput listener if Win32 not available."""
        self._running = True
        threading.Thread(target=self._update_loop, daemon=True, name="WPMTracker").start()
        print(f"[WPM] Tracker started (Win32={_WIN32} pynput={_PYNPUT})")

    def stop(self):
        self._running = False
        if self._listener:
            self._listener.stop()

    def reset_pb(self):
        with self._lock:
            self._personal_best = 0.0
        self._save_pb()

    @property
    def current_wpm(self):
        with self._lock:
            return self._current_wpm

    @property
    def personal_best(self):
        with self._lock:
            return self._personal_best

    @property
    def history(self):
        with self._lock:
            return list(self._minute_history)

    def get_frame(self):
        """Return a ready-to-send flat HSV frame for the DP-104."""
        with self._lock:
            cur = self._current_wpm
            hist = list(self._minute_history)
            pb   = self._personal_best
        return build_frame(cur, hist, pb)


# ── Quick test ───────────────────────────────────────────────────────────────
if __name__ == '__main__':
    print("WPM Tracker — type anything, Ctrl+C to stop")
    tracker = WPMTracker()
    tracker.start()
    try:
        while True:
            time.sleep(2)
            print(f"  Current: {tracker.current_wpm:.1f} WPM  |  PB: {tracker.personal_best:.1f} WPM  |  History: {[round(x,1) for x in tracker.history]}")
    except KeyboardInterrupt:
        tracker.stop()
        print("\nStopped.")
