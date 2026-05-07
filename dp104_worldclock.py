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

# ── Color palette ────────────────────────────────────────────────────────────
# Named colors available for city assignment.
# HSV: hue 0-255, sat 220, val 210 for consistency across the display.
CITY_COLORS = {
    'red':    (0,   220, 210),
    'orange': (15,  220, 210),
    'amber':  (28,  220, 210),
    'yellow': (42,  220, 210),
    'green':  (90,  220, 210),
    'cyan':   (128, 220, 210),
    'blue':   (155, 220, 210),
    'purple': (195, 220, 210),
    'pink':   (220, 200, 210),
    'white':  (0,   0,   210),
}
CITY_COLOR_HEX = {
    'red':    '#ff4444',
    'orange': '#ff8c00',
    'amber':  '#ffa500',
    'yellow': '#ffe066',
    'green':  '#00e5a0',
    'cyan':   '#00cfcf',
    'blue':   '#4488ff',
    'purple': '#aa66ff',
    'pink':   '#ff88cc',
    'white':  '#cccccc',
}
DEFAULT_COLOR_CYCLE = ['cyan','blue','red','green','amber','purple',
                        'orange','pink','yellow','white']

def _city_color(color_name):
    """Return HSV tuple for a named color."""
    return CITY_COLORS.get(color_name, CITY_COLORS['cyan'])

def _time_color(hour):
    """Fallback time-of-day color when city has no assigned color."""
    if 6 <= hour < 20:   return (28, 220, 210)
    elif 20 <= hour < 22 or 4 <= hour < 6:
                         return (18, 200, 140)
    else:                return (155, 180, 160)

# ── Pixel helper ──────────────────────────────────────────────────────────────
def _px(canvas, r, c, color):
    if 0 <= r < ROWS and 0 <= c < COLS:
        canvas[r][c] = color

def _draw_tall7(canvas, digit, col0, color, row0=0):
    """Draw a TALL7 digit. row0 = top row to start drawing (default 0)."""
    for r, row_str in enumerate(TALL7.get(digit, TALL7[' '])):
        for c, ch in enumerate(row_str):
            if ch == '#': _px(canvas, row0+r, col0+c, color)



def _draw_digits(canvas, h, m, color, colon=True):
    """Draw HH:MM using TALL7 in rows 0-6 (7 rows, fits exactly).
    Layout: [col1-3 H-tens] [col4 gap] [col5-7 H-units] [col8-9 colon]
            [col10-12 M-tens] [col13 gap] [col14-16 M-units]
    Rows 0-6 = digits. Row 7 = label + seconds (handled separately).
    """
    _draw_tall7(canvas, str(h//10),  1,  color, row0=0)
    _draw_tall7(canvas, str(h%10),   5,  color, row0=0)
    _draw_tall7(canvas, str(m//10),  10, color, row0=0)
    _draw_tall7(canvas, str(m%10),   14, color, row0=0)
    if colon:
        _px(canvas, 1, 8, color); _px(canvas, 1, 9, color)
        _px(canvas, 4, 8, color); _px(canvas, 4, 9, color)

def _draw_sec_bar(canvas, second, color):
    """Row 7 cols 0-17: seconds progress bar (18 pixels = ~3.3s per pixel).
    Cols 18-23 reserved for label dots."""
    total_cols = 18
    filled_f   = (second / 60) * total_cols
    filled     = int(filled_f)
    partial    = filled_f - filled
    h, s, v    = color
    for i in range(total_cols):
        if i < filled:
            _px(canvas, 7, i, color)
        elif i == filled:
            _px(canvas, 7, i, (h, s, max(20, int(v * partial))))

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
def build_frame_still(label, tz_name, color_name=None):
    """Single still frame — no seconds bar, colon always on.
    color_name: named color from CITY_COLORS (overrides time-of-day color)."""
    now    = _get_now(tz_name)
    color  = _city_color(color_name) if color_name else _time_color(now.hour)
    canvas = [[(0,0,0)]*COLS for _ in range(ROWS)]
    _draw_digits(canvas, now.hour, now.minute, color, colon=True)
    return _flat(canvas)

def build_frame_seconds(label, tz_name, color_name=None):
    """Still frame with seconds progress bar in cols 0-17 of row 7."""
    now    = _get_now(tz_name)
    color  = _city_color(color_name) if color_name else _time_color(now.hour)
    canvas = [[(0,0,0)]*COLS for _ in range(ROWS)]
    _draw_digits(canvas, now.hour, now.minute, color, colon=True)
    _draw_sec_bar(canvas, now.second, color)
    return _flat(canvas)

def build_frames_blink(label, tz_name, fps=10, color_name=None):
    """10-frame animation: 5 frames colon-on, 5 frames colon-off."""
    now    = _get_now(tz_name)
    color  = _city_color(color_name) if color_name else _time_color(now.hour)
    frames = []
    for colon_on in [True]*5 + [False]*5:
        canvas = [[(0,0,0)]*COLS for _ in range(ROWS)]
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
        self.cities    = cities or [
            {"label":"NYC","tz":"America/New_York","color":"cyan"},
            {"label":"LON","tz":"Europe/London",   "color":"blue"},
            {"label":"TYO","tz":"Asia/Tokyo",      "color":"red"},
            {"label":"UTC","tz":"UTC",             "color":"white"},
        ]
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
        c   = self.current
        col = c.get('color')   # named color or None → time-of-day fallback
        if self.mode == MODE_BLINK:
            return build_frames_blink(c['label'], c['tz'], color_name=col)
        elif self.mode == MODE_SECONDS:
            return build_frame_seconds(c['label'], c['tz'], color_name=col)
        else:
            return build_frame_still(c['label'], c['tz'], color_name=col)

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
