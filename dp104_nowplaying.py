"""
dp104_nowplaying.py  —  Now Playing pixel display for DP-104
24x8 LED matrix: floating source icon + play/pause indicator on brand-color background.

Layout:
  Cols 0-7:   Icon block — left/right black border, 6x6 icon bobs ±1 row
  Col  8:     Dim separator
  Cols 10-15: Play/pause symbol, bobs at half-phase offset from icon
  Cols 16-23: Reserved / open

Usage (standalone test):
  python dp104_nowplaying.py spotify playing
  python dp104_nowplaying.py youtube paused
"""

import math, colorsys, sys, random

# ── Color helpers (same as weather module) ─────────────────────────────────────
def rgb(r, g, b):
    h, s, v = colorsys.rgb_to_hsv(r/255, g/255, b/255)
    return (int(h*255), int(s*255), int(v*255))

def dim(color, amount):
    v = int(color[2] * amount)
    if 0 < v < 20 and color[1] > 30:
        v = 20
    h = color[0]
    s = color[1]
    if (h < 5 or h > 250) and s < 40:
        s = 0   # strip near-zero hue — renders red on firmware
    return (h, s, v)

OFF = (0, 0, 0)

ROWS, COLS = 8, 24
FRAME_BYTES = ROWS * COLS * 3

# ── Source brand colors ────────────────────────────────────────────────────────
SOURCES = {
    # key: (bg_color, icon_color, display_name)
    'spotify':      (rgb( 30, 215,  96), rgb( 30, 215,  96), "Spotify"),
    'youtube':      (rgb(255,   0,   0), rgb(255,   0,   0), "YouTube"),
    'youtubemusic': (rgb(255,   0, 100), rgb(255,   0, 100), "YT Music"),
    'twitch':       (rgb(145,  70, 255), rgb(145,  70, 255), "Twitch"),
    'winamp':       (rgb(  0, 180, 255), rgb(  0, 180, 255), "WinAMP"),
    'foobar':       (rgb(170, 220, 255), rgb(170, 220, 255), "foobar2000"),
    'foobar2000':   (rgb(170, 220, 255), rgb(170, 220, 255), "foobar2000"),
    'tidal':        (rgb(  0, 200, 200), rgb(  0, 200, 200), "TIDAL"),
    'applemusic':   (rgb(255,  45,  85), rgb(255,  45,  85), "Apple Music"),
    'amazonmusic':  (rgb(  0, 168, 232), rgb(  0, 168, 232), "Amazon Music"),
    'vlc':          (rgb(255, 165,   0), rgb(255, 165,   0), "VLC"),
    'soundcloud':   (rgb(255,  85,   0), rgb(255,  85,   0), "SoundCloud"),
    'pandora':      (rgb( 32, 183, 230), rgb( 32, 183, 230), "Pandora"),
    'deezer':       (rgb(161, 207,  75), rgb(161, 207,  75), "Deezer"),
    'browser':      (rgb(100, 149, 237), rgb(100, 149, 237), "Browser"),
    'default':      (rgb(160, 160, 255), rgb(160, 160, 255), "Music"),
}

def get_source(app_id):
    """Map Windows app ID to a source key."""
    a = (app_id or '').lower()
    if 'spotify'      in a: return 'spotify'
    if 'youtube'      in a and 'music' in a: return 'youtubemusic'
    if 'youtube'      in a: return 'youtube'
    if 'twitch'       in a: return 'twitch'
    if 'winamp'       in a: return 'winamp'
    if 'foobar'       in a: return 'foobar2000'
    if 'tidal'        in a: return 'tidal'
    if 'applemusic'   in a or 'itunes' in a: return 'applemusic'
    if 'amazon'       in a: return 'amazonmusic'
    if 'vlc'          in a: return 'vlc'
    if 'soundcloud'   in a: return 'soundcloud'
    if 'pandora'      in a: return 'pandora'
    if 'deezer'       in a: return 'deezer'
    if any(b in a for b in ['chrome','firefox','edge','opera','brave','msedge']):
        return 'browser'
    return 'default'

# ── Source icons (6×6 pixel sets) ─────────────────────────────────────────────
# Each is a set of (row, col) tuples in the 6x6 inner space (0-indexed)

def _px(*rows):
    """Build pixel set from row strings like '░██░░░'."""
    s = set()
    for r, row in enumerate(rows):
        for c, ch in enumerate(row):
            if ch == '█': s.add((r, c))
    return s

ICONS = {
    'spotify': _px(
        '░████░',
        '░░████',
        '░█████',
        '░█████',
        '░░░██░',
        '░░░░░░',
    ),
    # Spotify circle background — drawn black, arcs drawn on top in brand color
    'spotify_circle': {(r,c) for r in range(6) for c in range(6)
                       if (r-2.5)**2 + (c-2.5)**2 <= 3.0**2},

    'youtube': _px(
        '██████',
        '█░░░░█',
        '█░███░',   # play triangle inside
        '█░███░',
        '█░░░░█',
        '██████',
    ),
    'youtubemusic': _px(
        '░░██░░',
        '░█░░█░',
        '█░██░█',
        '█░██░█',
        '░██░█░',
        '░░██░░',
    ),
    'twitch': _px(
        '░████░',   # rounded top
        '██████',
        '█░██░█',   # two speech bars inside
        '█░██░█',
        '░░░░██',   # notch/chin on BOTTOM-RIGHT (matches actual Twitch logo)
        '░░░░░█',   # chin tip (right side)
    ),
    'winamp': _px(
        '██████',
        '█░█░░█',
        '░████░',
        '░█░░█░',
        '░░██░░',
        '░░██░░',
    ),
    'foobar2000': _px(
        '█████░',
        '█░░░░░',
        '████░░',
        '█░░███',
        '█░░███',
        '█░░██░',
    ),
    'tidal': _px(
        '██████',
        '░░██░░',
        '░░██░░',
        '░░██░░',
        '████░░',
        '░░████',
    ),
    'applemusic': _px(
        '░░████',
        '░░░░██',
        '░░░░██',
        '░░█░█░',
        '░░███░',
        '░░███░',
    ),
    'amazonmusic': _px(
        '░░██░░',
        '░████░',
        '░█░░█░',
        '██████',
        '█░░░░█',
        '██████',
    ),
    'vlc': _px(
        '░░██░░',
        '░████░',
        '░████░',
        '██████',
        '██████',
        '░████░',
    ),
    'soundcloud': _px(
        '░░░██░',
        '░██░░█',
        '██████',
        '██████',
        '░█░█░█',
        '░█████',
    ),
    'pandora': _px(
        '████░░',
        '█░░░█░',
        '█░░░█░',
        '████░░',
        '█░░░░░',
        '█░░░░░',
    ),
    'deezer': _px(
        '████░░',
        '█░░░█░',
        '█░░░█░',
        '████░░',
        '░█░█░█',
        '█░█░█░',
    ),
    'browser': _px(
        '░░██░░',
        '░████░',
        '██████',
        '██████',
        '░████░',
        '░░██░░',
    ),
    'default': _px(
        '░████░',
        '░█░░█░',
        '░█░░█░',
        '░█░░█░',
        '██████',
        '██████',
    ),
}

# ── Source-specific white overlay pixels (e.g. YouTube center dot) ──────────────
# These are drawn in pure white (sat=0) on top of the icon for detail
WHITE_OVERLAYS = {
    'youtube': {(2, 2), (3, 2)},   # white dot on play triangle = "play button" feel
}

# ── Play / Pause symbols (6×6) ─────────────────────────────────────────────────
PLAY_PIXELS = _px(
    '█░░░░░',
    '██░░░░',
    '███░░░',
    '███░░░',
    '██░░░░',
    '█░░░░░',
)

PAUSE_PIXELS = _px(
    '██░██░',
    '██░██░',
    '██░██░',
    '██░██░',
    '██░██░',
    '██░██░',
)

# ── Canvas ─────────────────────────────────────────────────────────────────────
def new_canvas():
    return [[OFF]*COLS for _ in range(ROWS)]

def px(canvas, r, c, color):
    if 0 <= r < ROWS and 0 <= c < COLS:
        canvas[r][c] = color

# ── Frame builder ──────────────────────────────────────────────────────────────
# Visualizer bar phase offsets and speed multipliers (9 bars, cols 15-23)
_VIZ_PHASES = [i * (2 * math.pi / 9) for i in range(9)]
_VIZ_SPEEDS = [1.00, 1.30, 0.85, 1.50, 0.90, 1.20, 0.75, 1.10, 0.95]

def build_frames(source_key='default', playing=True, num_frames=20):
    """
    Build animated HSV frames for the Now Playing pixel display.

    Layout:
      Cols  0-7 : Source icon — left/right black border, black rows 0&7,
                  6x6 icon bobs vertically (3 positions, smooth float)
      Col   8   : Dim separator
      Col   9   : Gap (bg color)
      Cols 10-15: Play/pause symbol — bobs at half-phase offset
      Col  16   : Gap (bg color)
      Cols 17-23: 7-bar EQ visualizer — sine-driven, brand color

    source_key: one of the SOURCES keys (use get_source() to map app IDs)
    playing:    True = play triangle, False = pause bars
    num_frames: 40 → smooth 3-position bob, 4s cycle at 10fps
    """
    src = SOURCES.get(source_key, SOURCES['default'])
    bg_col, icon_col, _ = src
    icon_pixels    = ICONS.get(source_key, ICONS['default'])
    white_overlays = WHITE_OVERLAYS.get(source_key, set())
    pp_pixels      = PLAY_PIXELS if playing else PAUSE_PIXELS

    bg_dim     = dim(bg_col, 0.25)   # tinted background
    icon_bright= icon_col             # full brand color for icon
    white_col  = (0, 0, 255)          # pure white for overlays (sat=0)
    sep_col    = dim(bg_col, 0.12)    # very dim separator

    # EQ bars: pure white (sat=0) for maximum contrast against any bg color
    viz_col = (0, 0, 230)

    # Play/Pause: random hue, full sat+val, new color each build_frames call.
    # The color ONLY changes when the keyboard is actually being refreshed
    # (triggered by state changes, not by this color change itself).
    rand_hue = random.randint(0, 255)
    pp_col = (rand_hue, 220, 230)

    # Spotify: draw circle background in black first, then arcs on top
    spotify_circle = ICONS.get('spotify_circle', set())

    # 2-position bob: cosine wave → 0 at start/end, 1 at middle.
    # Both black bars always visible:
    #   pos=0: black@row0, icon rows 1-6, black@row7
    #   pos=1: bg@row0, black@row1, icon rows 2-6, black@row7 (static bottom)
    frames = []
    for i in range(num_frames):
        t = i / num_frames
        canvas = new_canvas()

        # ── Background ───────────────────────────────────────────────────────
        for r in range(ROWS):
            for c in range(COLS):
                canvas[r][c] = bg_dim

        # ── 2-position bob ───────────────────────────────────────────────────
        bob = round((1 - math.cos(t * 2 * math.pi)) / 2)   # 0 or 1
        bob = max(0, min(1, bob))

        # PP bobs at half phase (opposite timing from icon)
        pp_bob = round((1 - math.cos((t + 0.5) * 2 * math.pi)) / 2)
        pp_bob = max(0, min(1, pp_bob))

        # ── Icon zone (cols 0-7) ─────────────────────────────────────────────
        icon_top = 1 + bob   # row 1 or 2

        # Spotify: fill circle with black first
        if source_key == 'spotify':
            for (ir, ic) in spotify_circle:
                draw_r = icon_top + ir
                draw_c = 1 + ic
                if 0 <= draw_r < ROWS and 0 <= draw_c < 8:
                    px(canvas, draw_r, draw_c, OFF)

        # Draw icon pixels
        for (ir, ic) in icon_pixels:
            draw_r = icon_top + ir
            draw_c = 1 + ic
            if 0 <= draw_r < ROWS and 0 <= draw_c < 8:
                px(canvas, draw_r, draw_c, icon_bright)

        # White overlays (e.g. YouTube center dot)
        for (ir, ic) in white_overlays:
            draw_r = icon_top + ir
            draw_c = 1 + ic
            if 0 <= draw_r < ROWS and 0 <= draw_c < 8:
                px(canvas, draw_r, draw_c, white_col)

        # Black rows — drawn LAST to cleanly frame the icon
        # Top: moves with bob (row 0 or 1)
        row_top = icon_top - 1
        for c in range(8):
            px(canvas, row_top, c, OFF)
        # Bottom: always row 7 (static — ensures bottom bar always visible)
        for c in range(8):
            px(canvas, 7, c, OFF)

        # ── Separator col 8 ──────────────────────────────────────────────────
        for r in range(ROWS):
            px(canvas, r, 8, sep_col)

        # ── Play/Pause: cols 10-14, bobs with pp_bob ─────────────────────────
        pp_top = 1 + pp_bob
        for (pr, pc) in pp_pixels:
            draw_r = pp_top + pr
            draw_c = 10 + pc
            if 0 <= draw_r < ROWS and 0 <= draw_c < COLS:
                px(canvas, draw_r, draw_c, pp_col)

        # ── EQ Visualizer: cols 15-23 (9 bars), only when PLAYING ────────────
        if playing:
            for bar in range(9):
                col = 15 + bar
                height = int(
                    (math.sin(t * 2 * math.pi * _VIZ_SPEEDS[bar]
                               + _VIZ_PHASES[bar]) + 1) / 2 * 6
                ) + 1   # 1..7
                for row in range(ROWS):
                    if row >= ROWS - height:
                        px(canvas, row, col, viz_col)

        # ── Flatten ───────────────────────────────────────────────────────────
        flat = []
        for r in range(ROWS):
            for c in range(COLS):
                flat.extend(canvas[r][c])
        assert len(flat) == FRAME_BYTES
        frames.append(flat)

    return frames



# ── HID constants ──────────────────────────────────────────────────────────────
import time, importlib

_hid = None
for _name in ('hid', 'hidapi'):
    try:
        m = importlib.import_module(_name)
        if hasattr(m, 'enumerate'):
            _hid = m; break
    except ImportError:
        continue

DP104_VID        = 0xe560
DP104_PID        = 0xe104
DP104_PIXEL_PATH = b'\\\\?\\HID#VID_E560&PID_E104&MI_01#7&180b41ba&0&0000#{4d1e55b2-f16f-11cf-88cb-001111000030}'
RAW_USAGE_PAGE   = 0xFF60


def _num_into_bytes(n):
    return [(n>>24)&0xFF, (n>>16)&0xFF, (n>>8)&0xFF, n&0xFF]


def _find_dp104():
    if not _hid: return None
    for info in _hid.enumerate():
        if info['usage_page'] == RAW_USAGE_PAGE and info.get('vendor_id') == DP104_VID:
            return info
    for info in _hid.enumerate():
        if info['usage_page'] == RAW_USAGE_PAGE:
            return info
    return None


def _open_device():
    dev = _hid.device()
    try:
        dev.open_path(DP104_PIXEL_PATH)
        dev.set_nonblocking(False)
        return dev
    except Exception:
        pass
    info = _find_dp104()
    if not info:
        raise RuntimeError("DP-104 not found")
    dev.open_path(info['path'])
    dev.set_nonblocking(False)
    return dev


def send_nowplaying(source_key='default', playing=True, fps=10, retries=3):
    """
    Build and send the Now Playing pixel animation to the keyboard.
    Uses the same proven protocol as dp104_weather_v2.py.

    source_key: use get_source(app_id) to map a Windows app ID
    playing:    True = play icon, False = pause icon
    fps:        animation speed (5 / 10 / 15 / 20)
    retries:    number of reconnect attempts on failure
    """
    if not _hid:
        raise RuntimeError("HID library not available — pip install hidapi")

    frames = build_frames(source_key, playing, num_frames=20)
    n      = len(frames)

    last_err = "Unknown error"
    for attempt in range(1, retries + 1):
        try:
            dev = _open_device()

            # Header — tell keyboard frame count and fps
            hdr = [0xd1, 0x30, n, fps, ROWS, COLS] + [0]*26
            dev.write([0x00] + hdr)
            resp = dev.read(32, timeout_ms=2000)
            if not resp or resp[0] != 0xd1:
                raise RuntimeError(f"Bad ACK: {list(resp[:4]) if resp else 'timeout'}")

            # Keyboard allocates animation buffer — must wait
            time.sleep(1.0)

            chunk_size = 25
            for fi, frame_data in enumerate(frames):
                offset = 0
                while offset < FRAME_BYTES:
                    chunk = frame_data[offset:offset+chunk_size]
                    goff  = fi * FRAME_BYTES + offset
                    ob    = _num_into_bytes(goff)
                    pkt   = [0xd1, 0x31, ob[0], ob[1], ob[2], ob[3], len(chunk)] + chunk
                    pkt  += [0] * (32 - len(pkt))
                    dev.write([0x00] + pkt[:32])
                    offset += len(chunk)
                    time.sleep(0.002)
                if fi < n - 1:
                    time.sleep(0.320)   # inter-frame gap

            dev.close()
            return True

        except Exception as e:
            last_err = str(e)
            try: dev.close()
            except: pass
            if attempt < retries:
                time.sleep(2.0)

    raise RuntimeError(f"Failed after {retries} attempts: {last_err}")


# ── ASCII preview ──────────────────────────────────────────────────────────────
def preview(source_key, playing):
    import colorsys as cs
    frames = build_frames(source_key, playing, num_frames=20)
    src = SOURCES.get(source_key, SOURCES['default'])
    print(f"\n{src[2]} — {'Playing' if playing else 'Paused'}")
    for fi in [0, 5, 10, 15]:
        flat = frames[fi]
        print(f"  [Frame {fi:2d}]")
        for r in range(ROWS):
            row = ""
            for c in range(COLS):
                h,s,v = flat[(r*COLS+c)*3],flat[(r*COLS+c)*3+1],flat[(r*COLS+c)*3+2]
                if v < 10:          row += " "
                elif s == 0:        row += "□"   # white pp symbol
                elif v < 50:        row += "·"   # dim bg
                else:               row += "█"   # icon
            print(f"    R{r} |{row}|")


# ── Main ──────────────────────────────────────────────────────────────────────
if __name__ == '__main__':
    src   = sys.argv[1] if len(sys.argv) > 1 else 'spotify'
    state = sys.argv[2] if len(sys.argv) > 2 else 'playing'
    playing = state.lower() != 'paused'

    preview(src, playing)

    if '--preview-only' in sys.argv:
        print("\n(preview only — not sending to keyboard)")
        sys.exit(0)

    print(f"\nSending {src} ({'playing' if playing else 'paused'}) to keyboard...")
    try:
        send_nowplaying(src, playing)
        print("Sent successfully.")
    except RuntimeError as e:
        print(f"Send failed: {e}")
        sys.exit(1)
