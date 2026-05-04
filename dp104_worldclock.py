"""
dp104_worldclock.py — World clock display for DP-104 24×8 LED matrix.

Three display modes:
  MODE_STILL   — still frame, sent once per minute at the top of the minute.
                 Automatic key remap: Red button→F13, Pause→LcdChangeScr.
                 F13 cycles cities.
  MODE_SECONDS — live seconds display, re-sent every 15 seconds.
                 No automatic remap — user handles via debug menu.
  MODE_BLINK   — blinking colon: 5 frames colon-on / 5 frames colon-off.
                 Sent as a 10-frame animation.

Layout (24×8):
  Row 0       : 3-letter city label — TINY3 font, dim amber
  Rows 1-7    : HH:MM in TALL7 font (3-wide × 7-tall)
                Cols 1-3   hour tens
                Cols 5-7   hour units
                Cols 8-9   colon (rows 2 and 5)
                Cols 10-12 minute tens
                Cols 14-16 minute units
                Cols 18-23 seconds bar (MODE_SECONDS only)
  Color: warm amber (day 06-20), dim orange (dusk/dawn), cool blue (night)
"""

import time
import threading
import datetime
import colorsys
import ctypes

try:
    from zoneinfo import ZoneInfo
    _ZONEINFO = True
except ImportError:
    try:
        from backports.zoneinfo import ZoneInfo
        _ZONEINFO = True
    except ImportError:
        _ZONEINFO = False

try:
    _user32 = ctypes.windll.user32
    _WIN32  = True
except Exception:
    _WIN32  = False

# ── Constants ─────────────────────────────────────────────────────────────────
ROWS, COLS  = 8, 24
FRAME_BYTES = ROWS * COLS * 3

MODE_STILL   = 'still'    # once per minute, top of minute
MODE_SECONDS = 'seconds'  # every 15s with seconds progress bar
MODE_BLINK   = 'blink'    # animated blinking colon (10 frames)

VK_F = {13:0x7C,14:0x7D,15:0x7E,16:0x7F,17:0x80,18:0x81,
        19:0x82,20:0x83,21:0x84,22:0x85,23:0x86,24:0x87}

# ── Fonts ─────────────────────────────────────────────────────────────────────
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
TINY3 = {
    'A':[".#.","#.#","###","#.#","#.#"],
    'B':["##.","#.#","##.","#.#","##."],
    'C':["###","#..","#..","#..","###"],
    'D':["##.","#.#","#.#","#.#","##."],
    'E':["###","#..","###","#..","###"],
    'F':["###","#..","###","#..","#.."],
    'G':["###","#..","#.#","#.#","###"],
    'H':["#.#","#.#","###","#.#","#.#"],
    'I':["###",".#.",".#.",".#.","###"],
    'J':["..#","..#","..#","#.#","###"],
    'K':["#.#","#.#","##.","#.#","#.#"],
    'L':["#..","#..","#..","#..","###"],
    'M':["#.#","###","###","#.#","#.#"],
    'N':["#.#","##.","###","#.#","#.#"],
    'O':["###","#.#","#.#","#.#","###"],
    'P':["###","#.#","###","#..","#.."],
    'Q':["###","#.#","#.#","#.#","###"],
    'R':["###","#.#","###","#.#","#.#"],
    'S':["###","#..","###","..#","###"],
    'T':["###",".#.",".#.",".#.",".#."],
    'U':["#.#","#.#","#.#","#.#","###"],
    'V':["#.#","#.#","#.#","#.#",".#."],
    'W':["#.#","#.#","###","###","#.#"],
    'X':["#.#","#.#",".#.","#.#","#.#"],
    'Y':["#.#","#.#",".#.",".#.",".#."],
    'Z':["###","..#",".#.","#..","###"],
    ' ':["...","...","...","...","..."],
    '-':["...","...","###","...","..."],
    '/':["..#","..#",".#.","#..","#.."],
}

# ── Color helpers ─────────────────────────────────────────────────────────────
def _time_color(hour):
    if 6 <= hour < 20:   return (28, 220, 210)   # day: warm amber
    elif 20 <= hour < 22 or 4 <= hour < 6:
                         return (18, 200, 140)   # dusk/dawn
    else:                return (155, 180, 160)  # night: cool blue

def _label_color(): return (28, 100, 90)         # always dim amber

# ── Pixel helper ──────────────────────────────────────────────────────────────
def _px(canvas, r, c, color):
    if 0 <= r < ROWS and 0 <= c < COLS:
        canvas[r][c] = color

def _draw_tall7(canvas, digit, col0, color):
    for r, row_str in enumerate(TALL7.get(digit, TALL7[' '])):
        for c, ch in enumerate(row_str):
            if ch == '#': _px(canvas, 1+r, col0+c, color)

def _draw_label(canvas, text, color):
    text = (text.upper() + "   ")[:5]
    col = 0
    for ch in text:
        for r, row_str in enumerate(TINY3.get(ch, TINY3[' '])):
            for c2, ch2 in enumerate(row_str):
                if ch2 == '#': _px(canvas, 0, col+c2, color)
        col += 4

def _draw_digits(canvas, h, m, color, colon=True):
    """Draw HH:MM at fixed positions."""
    _draw_tall7(canvas, str(h//10),  1,  color)
    _draw_tall7(canvas, str(h%10),   5,  color)
    _draw_tall7(canvas, str(m//10),  10, color)
    _draw_tall7(canvas, str(m%10),   14, color)
    if colon:
        _px(canvas, 2, 8, color); _px(canvas, 2, 9, color)
        _px(canvas, 5, 8, color); _px(canvas, 5, 9, color)

def _draw_sec_bar(canvas, second, color):
    """Row 7 cols 18-23: seconds progress bar."""
    filled  = second // 10
    partial = second %  10
    for i in range(6):
        col = 18 + i
        if i < filled:
            _px(canvas, 7, col, color)
        elif i == filled:
            h, s, v = color
            _px(canvas, 7, col, (h, s, max(20, int(v * partial / 10))))

def _get_now(tz_name):
    """Return datetime in the given timezone."""
    try:
        if _ZONEINFO:
            return datetime.datetime.now(ZoneInfo(tz_name))
    except Exception:
        pass
    return datetime.datetime.now()

def _flat(canvas):
    return [v for row in canvas for (h,s,val) in row for v in (h,s,val)]

# ── Public frame builders ─────────────────────────────────────────────────────
def build_frame_still(label, tz_name):
    """Single still frame — no seconds bar, colon always on."""
    now    = _get_now(tz_name)
    color  = _time_color(now.hour)
    canvas = [[(0,0,0)]*COLS for _ in range(ROWS)]
    _draw_label(canvas, label, _label_color())
    _draw_digits(canvas, now.hour, now.minute, color, colon=True)
    return _flat(canvas)

def build_frame_seconds(label, tz_name):
    """Still frame with seconds progress bar."""
    now    = _get_now(tz_name)
    color  = _time_color(now.hour)
    canvas = [[(0,0,0)]*COLS for _ in range(ROWS)]
    _draw_label(canvas, label, _label_color())
    _draw_digits(canvas, now.hour, now.minute, color, colon=True)
    _draw_sec_bar(canvas, now.second, color)
    return _flat(canvas)

def build_frames_blink(label, tz_name, fps=10):
    """
    10-frame animation: 5 frames colon-on, 5 frames colon-off.
    Returns list of flat HSV frame lists.
    """
    now    = _get_now(tz_name)
    color  = _time_color(now.hour)
    frames = []
    for colon_on in [True]*5 + [False]*5:
        canvas = [[(0,0,0)]*COLS for _ in range(ROWS)]
        _draw_label(canvas, label, _label_color())
        _draw_digits(canvas, now.hour, now.minute, color, colon=colon_on)
        frames.append(_flat(canvas))
    return frames

# ── WorldClock controller ─────────────────────────────────────────────────────
class WorldClock:
    """
    Manages city list, F-key cycling, and frame generation.

    cities   : list of {"label": str, "tz": str}
    fkey     : int 13-24 — F-key number to listen on
    mode     : MODE_STILL | MODE_SECONDS | MODE_BLINK
    on_change: callable(idx, label, tz) — called when city changes
    """

    def __init__(self, cities=None, fkey=13, mode=MODE_STILL, on_change=None):
        self.cities    = cities or [{"label":"NYC","tz":"America/New_York"},
                                    {"label":"UTC","tz":"UTC"}]
        self.fkey      = fkey
        self.mode      = mode
        self.on_change = on_change
        self._idx      = 0
        self._running  = False
        self._lock     = threading.Lock()
        self._prev_key = False

    @property
    def current(self):
        with self._lock:
            return self.cities[self._idx]

    def next_city(self):
        with self._lock:
            self._idx = (self._idx + 1) % max(1, len(self.cities))
            city = self.cities[self._idx]
        if self.on_change:
            try: self.on_change(self._idx, city['label'], city['tz'])
            except Exception: pass
        return city

    def get_frame(self):
        """Return frame(s) for current city based on mode."""
        c = self.current
        if self.mode == MODE_BLINK:
            return build_frames_blink(c['label'], c['tz'])
        elif self.mode == MODE_SECONDS:
            return build_frame_seconds(c['label'], c['tz'])
        else:
            return build_frame_still(c['label'], c['tz'])

    def seconds_until_next_minute(self):
        now = datetime.datetime.now()
        return 60 - now.second

    def start(self):
        self._running = True
        if _WIN32:
            t = threading.Thread(target=self._key_loop, daemon=True, name="ClockKeys")
            t.start()
        else:
            print("[WorldClock] Win32 not available — F-key cycling disabled")

    def stop(self):
        self._running = False

    def _key_loop(self):
        vk = VK_F.get(self.fkey)
        if not vk:
            return
        print(f"[WorldClock] Listening for F{self.fkey} (VK=0x{vk:02x})")
        while self._running:
            time.sleep(0.05)
            pressed = bool(_user32.GetAsyncKeyState(vk) & 0x8000)
            if pressed and not self._prev_key:
                city = self.next_city()
                print(f"[WorldClock] → {city['label']} ({city['tz']})")
            self._prev_key = pressed


if __name__ == '__main__':
    print("dp104_worldclock standalone test")
    clock = WorldClock(mode=MODE_BLINK)
    clock.start()
    try:
        while True:
            time.sleep(1)
            c = clock.current
            now = _get_now(c['tz'])
            print(f"  {c['label']:5s} {now.strftime('%H:%M:%S')}  mode={clock.mode}")
    except KeyboardInterrupt:
        clock.stop()
