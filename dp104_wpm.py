"""
dp104_wpm.py — WPM/APM tracker for DP-104 keyboard display.

Three display screens (cycled by F-key):
  Screen 0 — Live:    10-bar rolling minute graph + current value
  Screen 1 — History: 24-bar session history (one bar per session)
  Screen 2 — Stats:   All-time PB (left) + all-time average (right)

WPM = keyboard keystrokes ÷ 5 (standard: 5 keys = 1 word)
APM = keyboard keystrokes + mouse clicks (actions per minute)
"""

import time
import threading
import collections
import json
import ctypes
from pathlib import Path

try:
    from pynput import keyboard as _kb
    _PYNPUT = True
except ImportError:
    _PYNPUT = False

try:
    _user32 = ctypes.windll.user32
    _WIN32  = True
except Exception:
    _WIN32  = False

# ── Constants ─────────────────────────────────────────────────────────────────
ROWS, COLS  = 8, 24
FRAME_BYTES = ROWS * COLS * 3
HISTORY_BARS = 10
PB_CACHE_FILE = Path(__file__).parent / 'dp104_wpm_pb.json'
MAX_SESSION_HISTORY = 24   # one bar per column on history screen

# F-key VK codes
VK_F = {13:0x7C,14:0x7D,15:0x7E,16:0x7F,17:0x80,18:0x81,
        19:0x82,20:0x83,21:0x84,22:0x85,23:0x86,24:0x87}

# Mouse button VK codes (APM only)
MOUSE_VKS = [0x01, 0x02, 0x04, 0x05, 0x06]

# ── 3×5 pixel font ────────────────────────────────────────────────────────────
TINY5 = {
    '0':[[1,1,1],[1,0,1],[1,0,1],[1,0,1],[1,1,1]],
    '1':[[0,1,0],[1,1,0],[0,1,0],[0,1,0],[1,1,1]],
    '2':[[1,1,1],[0,0,1],[1,1,1],[1,0,0],[1,1,1]],
    '3':[[1,1,1],[0,0,1],[1,1,1],[0,0,1],[1,1,1]],
    '4':[[1,0,1],[1,0,1],[1,1,1],[0,0,1],[0,0,1]],
    '5':[[1,1,1],[1,0,0],[1,1,1],[0,0,1],[1,1,1]],
    '6':[[1,1,1],[1,0,0],[1,1,1],[1,0,1],[1,1,1]],
    '7':[[1,1,1],[0,0,1],[0,0,1],[0,1,0],[0,1,0]],
    '8':[[1,1,1],[1,0,1],[1,1,1],[1,0,1],[1,1,1]],
    '9':[[1,1,1],[1,0,1],[1,1,1],[0,0,1],[1,1,1]],
}

# TALL7: 3-wide × 7-tall digits for Stats screen
TALL7 = {
    '0':["###","#.#","#.#","#.#","#.#","#.#","###"],
    '1':[".#.",".#.",".#.",".#.",".#.",".#.",".#."],
    '2':["###","..#","..#","###","#..","#..","###"],
    '3':["###","..#","..#","###","..#","..#","###"],
    '4':["#.#","#.#","#.#","###","..#","..#","..#"],
    '5':["###","#..","#..","###","..#","..#","###"],
    '6':["###","#..","#..","###","#.#","#.#","###"],
    '7':["###","..#","..#","..#","..#","..#","..#"],
    '8':["###","#.#","#.#","###","#.#","#.#","###"],
    '9':["###","#.#","#.#","###","..#","..#","###"],
    ' ':["...","...","...","...","...","...","..."],
}

# ── Helpers ───────────────────────────────────────────────────────────────────
def _px(canvas, r, c, color):
    if 0 <= r < ROWS and 0 <= c < COLS:
        canvas[r][c] = color

def _wpm_color(value, pb):
    if pb <= 0: ratio = 0.5
    else: ratio = min(1.0, value / pb)
    if ratio >= 0.8:   hue = 0
    elif ratio >= 0.4: hue = int(32 * (1 - (ratio - 0.4) / 0.4))
    else:              hue = int(90 - 58 * ratio / 0.4)
    return (hue, 230, 200)

def _draw_tiny5(canvas, text, col0, row0, color):
    """Draw right-aligned number using 3×5 font."""
    digits = str(min(999, int(text)))
    total_w = len(digits) * 4 - 1
    cx = col0 + (11 - total_w)
    for ch in digits:
        bits = TINY5.get(ch, [[0]*3]*5)
        for r, row in enumerate(bits):
            for c, b in enumerate(row):
                if b: _px(canvas, row0 + r, cx + c, color)
        cx += 4

def _draw_tall7(canvas, digit, col0, row0, color):
    for r, row_str in enumerate(TALL7.get(str(digit), TALL7[' '])):
        for c, ch in enumerate(row_str):
            if ch == '#': _px(canvas, row0 + r, col0 + c, color)

def _draw_big_number(canvas, value, col0, color):
    """Draw up to 3-digit number right-aligned in a 12-col zone using TALL7."""
    text = str(min(999, int(round(value))))
    total_w = len(text) * 4 - 1
    cx = col0 + (11 - total_w)
    for ch in text:
        _draw_tall7(canvas, ch, cx, 0, color)
        cx += 4

def _flat(canvas):
    return [v for row in canvas for (h,s,val) in row for v in (h,s,val)]

# ── Frame builders ────────────────────────────────────────────────────────────
def build_frame(current, history, pb, apm_mode=False):
    """Screen 0 — Live: 10-bar rolling graph + current value number."""
    canvas = [[(0,0,0)]*COLS for _ in range(ROWS)]
    max_v  = max(pb, max(history) if history else 1, 1)

    for i in range(HISTORY_BARS):
        col      = 2 + i
        hist_idx = i - (HISTORY_BARS - len(history))
        if hist_idx < 0: continue
        val     = history[hist_idx]
        height  = max(1, min(ROWS, round(val / max_v * ROWS))) if val > 0 else 0
        color   = _wpm_color(val, pb)
        for row in range(ROWS - 1, ROWS - 1 - height, -1):
            _px(canvas, row, col, color)

    _px(canvas, ROWS-1, 12, (0, 0, 30))   # separator dot
    color = _wpm_color(current, pb)
    _draw_tiny5(canvas, str(int(round(current))), 13, 1, color)
    canvas[7][13] = (0, 0, 20)
    return _flat(canvas)


def build_frame_history(session_history, pb, apm_mode=False):
    """Screen 1 — History: 24 session bars, newest rightmost."""
    canvas = [[(0,0,0)]*COLS for _ in range(ROWS)]
    if not session_history:
        return _flat(canvas)
    max_v = max(pb, max(session_history), 1)
    for i in range(COLS):
        hist_idx = i - (COLS - len(session_history))
        if hist_idx < 0: continue
        val    = session_history[hist_idx]
        height = max(1, min(ROWS, round(val / max_v * ROWS))) if val > 0 else 0
        color  = _wpm_color(val, pb)
        for row in range(ROWS - 1, ROWS - 1 - height, -1):
            _px(canvas, row, i, color)
    # Mark all-time PB bar with bright top pixel
    if session_history:
        peak_idx = session_history.index(max(session_history))
        col = peak_idx - (len(session_history) - COLS) if len(session_history) <= COLS \
              else peak_idx + (COLS - len(session_history))
        if 0 <= col < COLS:
            _px(canvas, 0, col, (0, 0, 255))
    return _flat(canvas)


def build_frame_stats(pb_wpm, avg_wpm, pb_apm, avg_apm, apm_mode=False):
    """Screen 2 — Stats: PB on left (cols 0-11), Average on right (cols 12-23)."""
    canvas = [[(0,0,0)]*COLS for _ in range(ROWS)]
    pb_val  = pb_apm  if apm_mode else pb_wpm
    avg_val = avg_apm if apm_mode else avg_wpm
    pb_col  = _wpm_color(pb_val, pb_val)      # always red at PB
    avg_col = _wpm_color(avg_val, pb_val)

    # Draw PB in left zone (cols 0-11)
    _draw_big_number(canvas, pb_val,  0,  pb_col)
    # Draw AVG in right zone (cols 12-23)
    _draw_big_number(canvas, avg_val, 12, avg_col)

    # Dim indicator dots in row 7: left=PB, right=AVG
    _px(canvas, 7, 0,  (28, 120, 80))
    _px(canvas, 7, 1,  (28, 120, 80))
    _px(canvas, 7, 12, (28, 120, 80))
    _px(canvas, 7, 13, (28, 120, 80))
    return _flat(canvas)


# ── WPMTracker ────────────────────────────────────────────────────────────────
class WPMTracker:
    WORDS_PER_KEY = 1 / 5.0

    def __init__(self, pb_file=None, fkey=13, on_screen_change=None):
        self._lock              = threading.Lock()
        self._keystroke_ts      = collections.deque()
        self._mouseclick_ts     = collections.deque()
        self._minute_history    = collections.deque(maxlen=HISTORY_BARS)
        self._apm_history       = collections.deque(maxlen=HISTORY_BARS)
        self._personal_best     = 0.0
        self._personal_best_apm = 0.0
        self._current_wpm       = 0.0
        self._current_apm       = 0.0
        self._session_peak      = 0.0
        self._session_peak_apm  = 0.0
        self._pb_file           = Path(pb_file) if pb_file else PB_CACHE_FILE
        self._running           = False
        self._listener          = None
        self._screen            = 0          # 0=live, 1=history, 2=stats
        self._fkey              = fkey
        self._on_screen_change  = on_screen_change
        self._prev_fkey         = False
        # Session/average data (loaded from file)
        self._session_history     = []
        self._apm_session_history = []
        self._wpm_average         = 0.0
        self._apm_average         = 0.0
        self._total_sessions      = 0
        self._load_pb()

    # ── Persistence ──────────────────────────────────────────────────────────
    def _load_pb(self):
        try:
            if self._pb_file.exists():
                d = json.loads(self._pb_file.read_text())
                self._personal_best       = float(d.get('personal_best', 0))
                self._personal_best_apm   = float(d.get('personal_best_apm', 0))
                self._wpm_average         = float(d.get('wpm_average', 0))
                self._apm_average         = float(d.get('apm_average', 0))
                self._total_sessions      = int(d.get('total_sessions', 0))
                self._session_history     = list(d.get('session_history', []))
                self._apm_session_history = list(d.get('apm_session_history', []))
        except Exception:
            pass

    def _save_pb(self):
        try:
            self._pb_file.write_text(json.dumps({
                'personal_best':       round(self._personal_best, 1),
                'personal_best_apm':   round(self._personal_best_apm, 1),
                'wpm_average':         round(self._wpm_average, 1),
                'apm_average':         round(self._apm_average, 1),
                'total_sessions':      self._total_sessions,
                'session_history':     [round(x,1) for x in self._session_history],
                'apm_session_history': [round(x,1) for x in self._apm_session_history],
            }, indent=2))
        except Exception:
            pass

    def _save_session(self):
        """Called when tracker stops — records session peak in history."""
        with self._lock:
            peak     = self._session_peak
            peak_apm = self._session_peak_apm
        if peak < 1.0:
            return  # nothing typed this session
        with self._lock:
            self._session_history.append(peak)
            self._apm_session_history.append(peak_apm)
            if len(self._session_history) > MAX_SESSION_HISTORY:
                self._session_history = self._session_history[-MAX_SESSION_HISTORY:]
            if len(self._apm_session_history) > MAX_SESSION_HISTORY:
                self._apm_session_history = self._apm_session_history[-MAX_SESSION_HISTORY:]
            # Update rolling average
            self._total_sessions += 1
            n = self._total_sessions
            self._wpm_average = (self._wpm_average * (n-1) + peak)     / n
            self._apm_average = (self._apm_average * (n-1) + peak_apm) / n
        self._save_pb()

    # ── Update loop ──────────────────────────────────────────────────────────
    def _update_loop(self):
        last_second      = time.monotonic()
        last_minute_snap = time.monotonic()

        POLL_KEYS = (list(range(0x08,0x09)) + [0x20] +
                     list(range(0x30,0x3A)) + list(range(0x41,0x5B)) +
                     list(range(0xBA,0xC1)) + list(range(0xDB,0xE0)))
        prev = {}
        vk_fkey = VK_F.get(self._fkey)

        while self._running:
            time.sleep(0.05)
            now = time.monotonic()

            if _WIN32:
                # Keyboard → WPM + APM
                for vk in POLL_KEYS:
                    cur = bool(_user32.GetAsyncKeyState(vk) & 0x8000)
                    if cur and not prev.get(vk, False):
                        with self._lock:
                            self._keystroke_ts.append(now)
                    prev[vk] = cur
                # Mouse → APM only
                for vk in MOUSE_VKS:
                    cur = bool(_user32.GetAsyncKeyState(vk) & 0x8000)
                    if cur and not prev.get(vk, False):
                        with self._lock:
                            self._mouseclick_ts.append(now)
                    prev[vk] = cur
                # F-key screen cycle
                if vk_fkey:
                    cur = bool(_user32.GetAsyncKeyState(vk_fkey) & 0x8000)
                    if cur and not self._prev_fkey:
                        self._screen = (self._screen + 1) % 3
                        if self._on_screen_change:
                            try: self._on_screen_change(self._screen)
                            except Exception: pass
                    self._prev_fkey = cur

            if now - last_second >= 1.0:
                last_second = now
                wpm, apm = self._compute_wpm()
                with self._lock:
                    self._current_wpm = wpm
                    self._current_apm = apm
                    if wpm > self._personal_best:
                        self._personal_best = wpm
                        self._save_pb()
                    if apm > self._personal_best_apm:
                        self._personal_best_apm = apm
                    if wpm > self._session_peak:
                        self._session_peak = wpm
                    if apm > self._session_peak_apm:
                        self._session_peak_apm = apm
                    if now - last_minute_snap >= 60.0:
                        self._minute_history.append(wpm)
                        self._apm_history.append(apm)
                        last_minute_snap = now

    def _compute_wpm(self):
        now    = time.monotonic()
        cutoff = now - 60.0
        with self._lock:
            while self._keystroke_ts  and self._keystroke_ts[0]  < cutoff: self._keystroke_ts.popleft()
            while self._mouseclick_ts and self._mouseclick_ts[0] < cutoff: self._mouseclick_ts.popleft()
            kc = len(self._keystroke_ts)
            mc = len(self._mouseclick_ts)
        return kc * self.WORDS_PER_KEY, float(kc + mc)

    # ── Public API ────────────────────────────────────────────────────────────
    def start(self):
        self._running = True
        threading.Thread(target=self._update_loop, daemon=True, name="WPMTracker").start()
        print(f"[WPM] Tracker started (Win32={_WIN32} pynput={_PYNPUT})")

    def stop(self):
        self._running = False
        self._save_session()
        if self._listener:
            try: self._listener.stop()
            except: pass

    def reset_pb(self):
        with self._lock:
            self._personal_best = 0.0
            self._personal_best_apm = 0.0
        self._save_pb()

    @property
    def screen(self):       return self._screen
    @property
    def current_wpm(self):
        with self._lock: return self._current_wpm
    @property
    def current_apm(self):
        with self._lock: return self._current_apm
    @property
    def personal_best(self):
        with self._lock: return self._personal_best
    @property
    def personal_best_apm(self):
        with self._lock: return self._personal_best_apm
    @property
    def wpm_average(self):
        with self._lock: return self._wpm_average
    @property
    def apm_average(self):
        with self._lock: return self._apm_average

    def get_frame(self, apm_mode=False):
        with self._lock:
            screen    = self._screen
            cur       = self._current_apm   if apm_mode else self._current_wpm
            hist      = list(self._apm_history) if apm_mode else list(self._minute_history)
            pb        = self._personal_best_apm if apm_mode else self._personal_best
            s_hist    = list(self._apm_session_history) if apm_mode else list(self._session_history)
            pb_wpm    = self._personal_best
            pb_apm    = self._personal_best_apm
            avg_wpm   = self._wpm_average
            avg_apm   = self._apm_average
        if screen == 1:
            return build_frame_history(s_hist, pb)
        elif screen == 2:
            return build_frame_stats(pb_wpm, avg_wpm, pb_apm, avg_apm, apm_mode)
        else:
            return build_frame(cur, hist, pb, apm_mode)


if __name__ == '__main__':
    tracker = WPMTracker()
    tracker.start()
    try:
        while True:
            time.sleep(2)
            c = tracker.current_wpm
            print(f"  {c:.1f} WPM  screen={tracker.screen}  "
                  f"pb={tracker.personal_best:.1f}  avg={tracker.wpm_average:.1f}")
    except KeyboardInterrupt:
        tracker.stop()
        print("\nStopped.")
