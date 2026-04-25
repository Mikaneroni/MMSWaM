"""
dp104_weather_v2.py - Animated weather display for DP-104 24x8 LED matrix

Layout:
  Cols 0-9:   Animated weather icon (10x8), black background
  Col  10:    Dim separator
  Cols 11-23: Text zone
    Rows 0-4: Temperature bold
    Rows 5-7: H/L compact

Weather types:
  0=sunny  1=partly_cloudy  2=cloudy  3=rainy  4=snowy  5=thunderstorm

Usage:
  python dp104_weather_v2.py [weather] [temp_f] [high_f] [low_f] [wind_mph]
  python dp104_weather_v2.py 0 72 85 58
"""
import hid, time, sys, colorsys, math

PATH = b'\\\\?\\HID#VID_E560&PID_E104&MI_01#7&180b41ba&0&0000#{4d1e55b2-f16f-11cf-88cb-001111000030}'
VENDOR_ID, PRODUCT_ID = 0xe560, 0xe104
COLS, ROWS = 24, 8
FRAME_BYTES = COLS * ROWS * 3
FPS = 10

OFF = (0, 0, 0)

# ── Color helpers ─────────────────────────────────────────────────────────────
def rgb(r, g, b):
    """Convert RGB 0-255 to HSV 0-255 tuple as keyboard expects."""
    h, s, v = colorsys.rgb_to_hsv(r/255, g/255, b/255)
    return (int(h*255), int(s*255), int(v*255))

def dim(color, amount):
    """Dim ONLY the brightness channel. Preserves hue and saturation."""
    return (color[0], color[1], int(color[2] * amount))

def bright(color, v_255):
    """Set brightness to absolute value 0-255. Preserves hue/sat."""
    return (color[0], color[1], min(255, int(v_255)))

def glow(color, amount):
    """Additive brightness — for glows over black. Clamps to 255."""
    return (color[0], color[1], min(255, int(color[2] + amount * 255)))

# Cloud palette — true grey-white (very low saturation)
CLOUD_BRIGHT = (0, 0, 235)           # pure white cloud top (sat=0 avoids cast)
CLOUD_MID    = rgb(180, 188, 200)   # mid grey
CLOUD_DIM    = rgb(120, 128, 145)   # darker underside
CLOUD_STORM  = rgb(70,  75,  92)    # storm dark

# Sun
SUN_WHITE = (0, 0, 255)              # pure white sun core (sat=0)
SUN_YEL   = rgb(255, 230, 50)
SUN_ORG   = rgb(255, 160, 0)
SUN_RED   = rgb(255, 80,  0)

# Rain
RAIN      = rgb(100, 160, 255)
RAIN_DIM  = rgb(60,  120, 220)

# Snow
SNOW_WH   = (0, 0, 240)         # pure white (sat=0 avoids color cast)
SNOW_BLUE = rgb(160, 195, 255)   # blue edge on star flakes — intentional color

# Lightning
BOLT      = rgb(255, 255, 200)
BOLT_PUR  = rgb(210, 180, 255)

# Night sky
NIGHT_SKY  = rgb(5,   10,  40)
# Moon colors: sat=255 ensures keyboard firmware renders unambiguously as yellow
# Lower value (170) gives the soft pale-gold moon look rather than a blazing sun
MOON_YEL   = (48, 127, 240)    # HSV: hue=68°, sat=50%, val=94% = lemon yellow (distinct from orange-red)
MOON_WHITE = (48,  20, 255)    # HSV: same hue, near-zero sat = warm white shimmer
MOON_GLOW  = (48, 153, 200)    # HSV: hue=68°, sat=60%, val=78% = yellow-green glow
# Stars: sat=0 = pure white — low-sat colors get color-cast by keyboard firmware
# hue is irrelevant when sat=0, so dim() always produces clean white/grey
STAR_W     = (0, 0, 240)   # pure bright white
STAR_DIM   = (0, 0, 160)   # pure dim white

# Nature
BIRD      = rgb(30,  35,  55)
FLOWER_PK = rgb(255, 80,  160)
FLOWER_YL = rgb(255, 210, 40)
FLOWER_PU = rgb(200, 60,  255)
STEM      = rgb(60,  200, 80)

# House
HOUSE_ROOF = rgb(200, 60,  40)
HOUSE_WALL = rgb(220, 180, 100)
HOUSE_DOOR = rgb(140, 80,  40)
HOUSE_WIN  = rgb(150, 220, 255)
HOUSE_CHIM = rgb(180, 50,  30)

# Text
# temp colors now computed dynamically by temp_color()
C_HIGH = rgb(255, 90,  50)
C_LOW  = rgb(80,  170, 255)
C_SEP  = rgb(50,  55,  85)

# ── Canvas ────────────────────────────────────────────────────────────────────
def new_canvas():
    return [[OFF]*COLS for _ in range(ROWS)]

def px(canvas, r, c, color):
    if 0 <= r < ROWS and 0 <= c < COLS:
        canvas[r][c] = color

def px_add(canvas, r, c, color, alpha=1.0):
    """Additive blend on brightness only — safe over black."""
    if not (0 <= r < ROWS and 0 <= c < COLS):
        return
    base = canvas[r][c]
    new_v = min(255, base[2] + int(color[2] * alpha))
    # Use the incoming hue/sat if adding to black, else keep existing
    if base[2] < 10:
        canvas[r][c] = (color[0], color[1], new_v)
    else:
        canvas[r][c] = (base[0], base[1], new_v)

# ── Fonts ─────────────────────────────────────────────────────────────────────
BOLD5 = {
    '0': ["###","#.#","#.#","#.#","###"],
    '1': [".#.",".#.",".#.",".#.",".#."],
    '2': ["###","..#","###","#..","###"],
    '3': ["###","..#","###","..#","###"],
    '4': ["#.#","#.#","###","..#","..#"],
    '5': ["###","#..","###","..#","###"],
    '6': ["###","#..","###","#.#","###"],
    '7': ["###","..#","..#","..#","..#"],
    '8': ["###","#.#","###","#.#","###"],
    '9': ["###","#.#","###","..#","###"],
    'F': ["###","#..","##.","#..","#.."],
    'C': [".##","#..","#..","#..",".##"],
    '-': ["...","...","###","...","..."],
    ' ': ["...","...","...","...","..."],
}

# 4-wide x 4-tall chunky font for large temp display
CHUNKY4 = {
    '0': ["####","#..#","#..#","####"],
    '1': [".##.",".##.",".##.",".##."],
    '2': ["####","..##","##..","####"],
    '3': ["####","..##","..##","####"],
    '4': ["#..#","#..#","####","..#."],
    '5': ["####","##..","..##","####"],
    '6': ["####","##..","####","####"],
    '7': ["####","...#","..#.","..#."],
    '8': ["####","####","####","####"],
    '9': ["####","####","..##","####"],
    '-': ["....","####","####","...."],
    ' ': ["....","....","....","...."],
}

TINY3 = {
    '0': ["###","#.#","###"],
    '1': [".#.",".#.",".#."],
    '2': ["##.",".##","##."],
    '3': ["###","###","###"],
    '4': ["#.#","###","..#"],
    '5': ["###","##.","###"],
    '6': ["#..","###","###"],
    '7': ["###","..#","..#"],
    '8': ["###","###","###"],
    '9': ["###","###","..#"],
    'H': ["#.#","###","#.#"],
    'L': ["#..","#..","###"],
    ' ': ["...","...","..."],
    '-': ["...","###","..."],
}

def draw_bold(canvas, text, col0, row0, color):
    x = col0
    for ch in str(text):
        pat = BOLD5.get(ch, BOLD5[' '])
        for r, bits in enumerate(pat):
            for c, b in enumerate(bits):
                if b == '#':
                    px(canvas, row0+r, x+c, color)
        x += 4

def draw_tiny(canvas, text, col0, row0, color):
    x = col0
    for ch in str(text):
        pat = TINY3.get(ch, TINY3[' '])
        for r, bits in enumerate(pat):
            for c, b in enumerate(bits):
                if b == '#':
                    px(canvas, row0+r, x+c, color)
        x += 4

# 3-wide x 7-tall font for current temp (nearly full column height)
TALL7 = {
    '0': ["###","#.#","#.#","#.#","#.#","#.#","###"],
    '1': [".#.",".#.",".#.",".#.",".#.",".#.",".#."],
    '2': ["###","..#","..#","###","#..","#..","###"],
    '3': ["###","..#","..#","###","..#","..#","###"],
    '4': ["#.#","#.#","#.#","###","..#","..#","..#"],
    '5': ["###","#..","#..","###","..#","..#","###"],
    '6': ["###","#..","#..","###","#.#","#.#","###"],
    '7': ["###","..#","..#","..#","..#","..#","..#"],
    '8': ["###","#.#","#.#","###","#.#","#.#","###"],
    '9': ["###","#.#","#.#","###","..#","..#","###"],
    '-': ["...","...","...","###","...","...","..."],
    ' ': ["...","...","...","...","...","...","..."],
}

# 3-wide x 4-tall font for Hi/Lo display — fills 6-col zone exactly (no gaps)
HL4 = {
    '0': ["###","#.#","#.#","###"],
    '1': [".#.",".#.",".#.",".#."],
    '2': ["###","..#","##.","###"],
    '3': ["###","..#","..#","###"],
    '4': ["#.#","#.#","###","..#"],
    '5': ["###","##.","..#","###"],
    '6': ["###","##.","#.#","###"],
    '7': ["###","..#","..#","..#"],
    '8': ["#.#","###","#.#","###"],
    '9': ["###","#.#","###","..#"],
    '-': ["...","###","###","..."],
    ' ': ["...","...","...","..."],
}

# ── Text zone ─────────────────────────────────────────────────────────────────
def temp_color(temp_f):
    """Piecewise temperature gradient:
       <= 10F: deep blue  |  10-45F: blue  |  45-70F: green
       70-90F: yellow→orange  |  >= 90F: deep red
    """
    # Breakpoints: (temp_f, hue_byte)
    # hue 200=purple-blue, 170=blue, 135=cyan-blue, 68=yellow-green, 8=orange-red, 0=red
    BP = [(-10, 200), (10, 170), (45, 135), (70, 68), (90, 8), (105, 0)]
    t = max(-10, min(105, temp_f))
    for i in range(len(BP) - 1):
        t0, h0 = BP[i]
        t1, h1 = BP[i + 1]
        if t0 <= t <= t1:
            frac = (t - t0) / (t1 - t0)
            hue = int(h0 + frac * (h1 - h0))
            return (hue, 255, 220)
    return (0, 255, 220)

# ── Flowers ───────────────────────────────────────────────────────────────────
def draw_flowers(canvas, t):
    cols  = [0, 2, 4, 6, 8]
    petals = [FLOWER_PK, FLOWER_YL, FLOWER_PU, FLOWER_YL, FLOWER_PK]
    for i, fc in enumerate(cols):
        bob = int(math.sin(t*2*math.pi + i*1.3) * 0.6)
        px(canvas, 7, fc, STEM)
        br = max(5, 6 + bob)
        px(canvas, br, fc, petals[i])
        if fc > 0:  px(canvas, br, fc-1, dim(petals[i], 0.55))
        if fc < 9:  px(canvas, br, fc+1, dim(petals[i], 0.55))

# ── Cloud helper ──────────────────────────────────────────────────────────────
def draw_cloud(canvas, cx, cy, w, h, top_col, bot_col, max_col=10):
    for dr in range(h):
        for dc in range(w):
            c = cx + dc
            if c < 0 or c >= max_col:
                continue
            if (dr == 0 or dr == h-1) and (dc == 0 or dc == w-1):
                continue
            col = top_col if dr < h//2 else bot_col
            px(canvas, cy+dr, c, col)

def draw_cloud_tiled(canvas, cx, cy, w, h, top_col, bot_col, zone_w):
    """Two tiled instances so cloud wraps: exits right, re-enters left with no pause."""
    draw_cloud(canvas, cx,          cy, w, h, top_col, bot_col, max_col=zone_w)
    draw_cloud(canvas, cx - zone_w, cy, w, h, top_col, bot_col, max_col=zone_w)

# ── House helper ──────────────────────────────────────────────────────────────
def draw_house(canvas):
    px(canvas, 1, 7, HOUSE_CHIM); px(canvas, 2, 7, HOUSE_CHIM)
    px(canvas, 2, 4, HOUSE_ROOF)
    for c in [3,4,5]:     px(canvas, 3, c, HOUSE_ROOF)
    for c in [2,3,4,5,6]: px(canvas, 4, c, HOUSE_ROOF)
    for r in [5,6]:
        for c in [2,4,6]: px(canvas, r, c, HOUSE_WALL)
        for c in [3,5]:   px(canvas, r, c, HOUSE_WIN)
    px(canvas, 7, 2, HOUSE_WALL); px(canvas, 7, 3, HOUSE_DOOR)
    px(canvas, 7, 4, HOUSE_DOOR); px(canvas, 7, 5, HOUSE_WALL)
    px(canvas, 7, 6, HOUSE_WALL)

# ── Crescent moon ─────────────────────────────────────────────────────────────
_CRESCENT = [
    (0,1),(0,2),(0,3),
    (1,0),(1,1),(1,2),
    (2,0),(2,1),
    (3,0),(3,1),
    (4,0),(4,1),
    (5,0),(5,1),(5,2),
    (6,1),(6,2),(6,3),
]

def _draw_crescent(canvas, bright):
    shimmer_sat = int(127 - (bright - 0.70) / 0.30 * 117)
    shimmer_sat = max(10, min(127, shimmer_sat))
    for r, c in _CRESCENT:
        if r == 0 or r == 6:
            px(canvas, r, c, (MOON_YEL[0], MOON_YEL[1], int(MOON_YEL[2] * 0.65)))
        elif c == 0:
            px(canvas, r, c, (MOON_YEL[0], shimmer_sat, MOON_WHITE[2]))
        else:
            px(canvas, r, c, MOON_YEL)

def _draw_stars(canvas, t, positions, seed_offset=0):
    for i, (sr, sc) in enumerate(positions):
        twinkle = 0.35 + 0.65 * abs(math.sin(t*2*math.pi + (i+seed_offset)*0.73))
        col = STAR_W if (i+seed_offset) % 3 != 0 else STAR_DIM
        px(canvas, sr, sc, dim(col, twinkle))

# Shooting star purple — hue=194 (274°), sat=175, distinct from stars and sky
SHOOT_PURPLE = (194, 175, 255)

def _draw_shooting_star(canvas, t):
    phase = (t * 0.40) % 1.0
    if phase > 0.30:
        return
    progress = phase / 0.30
    brightness = math.sin(progress * math.pi)
    if brightness < 0.12:
        return
    # Travel full width: col 9 → col -3, rows 0-7 (full height of icon zone)
    head_c = int(9 - progress * 13)   # col 9 → -4, crosses full width
    head_r = int(progress * 7)         # row 0 → 7, full height
    for i in range(4):                 # 4-pixel trail
        tc, tr = head_c + i, head_r - i
        fade = brightness * (1.0 - i * 0.30)
        if fade > 0.10 and 0 <= tr < 8 and 0 <= tc < 10:
            px(canvas, tr, tc, dim(SHOOT_PURPLE, fade))  # overwrites everything

# ── Icons ─────────────────────────────────────────────────────────────────────
def icon_sunny(canvas, t, **_):
    pulse = 0.75 + 0.25*math.sin(t*2*math.pi)
    sr, sc = 2, 2
    angle = t * 2 * math.pi
    for i in range(8):
        a = angle + i*math.pi/4
        for dist, col, alpha in [(2, SUN_ORG, 0.9), (3, SUN_RED, 0.45)]:
            rr = sr + int(round(dist*math.sin(a)))
            rc = sc + int(round(dist*math.cos(a)))
            px_add(canvas, rr, rc, dim(col, pulse*alpha))
    for dr in range(-1, 2):
        for dc in range(-1, 2):
            col = SUN_WHITE if abs(dr)+abs(dc) <= 1 else SUN_YEL
            px(canvas, sr+dr, sc+dc, dim(col, pulse))
    for speed, brow, offset in [(0.65, 3, 0.05), (0.40, 4, 0.45), (0.55, 5, 0.75)]:
        bx = (t*speed + offset) % 1.0
        bc = int(bx * 12) - 1
        for dc, dr in [(0,0),(-1,1),(1,1)]:
            px(canvas, brow+dr, bc+dc, BIRD)
    draw_flowers(canvas, t)

def icon_partly_cloudy(canvas, t, **_):
    # Clean seamless loop: span = cloud_w + 10 + cloud_w = 2*cloud_w + 10
    # At t=0 cloud fully off left; at t=1 fully off right → t=0 again → seamless.
    pulse = 0.7 + 0.3*math.sin(t*2*math.pi)
    sr, sc = 1, 1
    for i in range(6):
        a = i*math.pi/3
        for dist in [2, 3]:
            rr = sr + int(round(dist*math.sin(a)))
            rc = sc + int(round(dist*math.cos(a)))
            px_add(canvas, rr, rc, dim(SUN_ORG, pulse*0.65))
    for dr in range(-1, 2):
        for dc in range(-1, 2):
            col = SUN_WHITE if abs(dr)+abs(dc) <= 1 else SUN_YEL
            px(canvas, sr+dr, sc+dc, dim(col, pulse))
    # Both clouds: zone=10, w=7. cx=int(t*10) → 0..9. Phases staggered for parallax.
    c1x = int((t % 1.0) * 10)
    draw_cloud_tiled(canvas, c1x, 3, 7, 3, CLOUD_BRIGHT, CLOUD_MID, zone_w=10)
    c2x = int(((t * 0.60 + 0.50) % 1.0) * 10)
    draw_cloud_tiled(canvas, c2x, 1, 7, 2, CLOUD_MID, CLOUD_DIM, zone_w=10)
    bx = (t*0.45 + 0.2) % 1.0
    bc = int(bx * 12) - 1
    for dc, dr in [(0,0),(-1,1),(1,1)]:
        px(canvas, 6+dr, bc+dc, BIRD)

def icon_cloudy(canvas, t, **_):
    """House in icon zone 0-9, standard blue separator at col 10, zone=10 clouds."""
    draw_house(canvas)
    # All three cloud layers: zone=10, w=7, gap=3 cols (sky showing through house scene)
    # cx = int(t * 10) → always 0..9, draw_cloud_tiled adds wrap instance at cx-10
    c1x = int((t % 1.0) * 10)
    draw_cloud_tiled(canvas, c1x, 1, 7, 2, CLOUD_BRIGHT, CLOUD_MID, zone_w=10)
    c2x = int(((t * 0.60 + 0.33) % 1.0) * 10)
    draw_cloud_tiled(canvas, c2x, 0, 7, 2, CLOUD_MID, CLOUD_DIM, zone_w=10)
    c3x = int(((t * 0.40 + 0.67) % 1.0) * 10)
    draw_cloud_tiled(canvas, c3x, 2, 7, 2, CLOUD_DIM, CLOUD_STORM, zone_w=10)

def icon_rainy(canvas, t, wind_mph=0, **_):
    # Cloud covers rows 0-2
    for r in range(3):
        for c in range(10):
            shade = CLOUD_STORM if r > 0 else CLOUD_DIM
            px(canvas, r, c, shade)
    for c in range(1, 9):
        px(canvas, 0, c, dim(CLOUD_MID, 0.4))

    # Slant: linear 0-6 pixel drift over the 7-row fall distance
    # 0 mph=straight, 60 mph=~40° diagonal. Capped at 60.
    slant_f = min(60.0, float(wind_mph)) / 60.0 * 6.0

    # Drop columns: include negative-start positions so drops enter from the
    # left edge at high wind, keeping the icon zone visually full of rain
    drop_cols = [(0,0.00),(2,0.20),(4,0.42),(6,0.63),(8,0.82),
                 (1,0.55),(-2,0.15),(-1,0.70),(3,0.35),(5,0.90)]

    for col_base, ph in drop_cols:
        for di in range(2):
            phase = (t + di*0.5 + ph) % 1.0
            dr = int(phase * 7) + 3
            dc = col_base + int(phase * slant_f)
            if not (0 <= dc <= 9):
                continue  # off-screen — skip (natural clipping)
            v_b = int((1.0 - phase * 0.4) * 255)
            px(canvas, dr,     dc, bright(RAIN, v_b))
            px(canvas, dr + 1, dc, bright(RAIN_DIM, int(v_b * 0.55)))

    # Occasional lightning
    fl = (t * 2.8) % 1.0
    if fl < 0.1:
        b = math.sin(fl / 0.1 * math.pi)
        for lr, lc in [(1,4),(2,3),(2,4),(3,4),(4,3)]:
            px(canvas, lr, lc, dim(BOLT, b))

def icon_snowy(canvas, t, wind_mph=0, **_):
    sky = rgb(20, 40, 90)
    for r in range(8):
        for c in range(10):
            px(canvas, r, c, sky)
    for r in range(2):
        for c in range(10):
            px(canvas, r, c, dim(CLOUD_MID, 0.55))
    drift = min(2, wind_mph // 8)
    lanes = [
        (0,'star'),(2,'dot'),(4,'star'),(6,'dot'),(8,'star'),
        (1,'dot'),(3,'star'),(5,'dot'),(7,'star'),(9,'dot'),
    ]
    for i, (col_base, shape) in enumerate(lanes):
        for stagger in [0.0, 0.5]:
            phase = (t*0.60 + i*0.10 + stagger) % 1.0
            fr = int(phase * 9)
            fc = min(9, col_base + int(phase * drift))
            if phase < 0.15:
                alpha = phase / 0.15
            elif phase > 0.85:
                alpha = (1.0 - phase) / 0.15
            else:
                alpha = 1.0
            bv = int(alpha * 220)
            if bv < 8:
                continue
            center = bright(SNOW_WH, bv)
            edge   = bright(SNOW_BLUE, int(bv * 0.60))
            if 0 <= fr < ROWS:
                px(canvas, fr, fc, center)
                if shape == 'star' and bv > 20:
                    for ddr, ddc in [(-1,0),(1,0),(0,-1),(0,1)]:
                        if 0 <= fr+ddr < ROWS and 0 <= fc+ddc < 10:
                            px(canvas, fr+ddr, fc+ddc, edge)

def icon_thunderstorm(canvas, t, **_):
    """Storm cloud enters from left, crosses full width, two staggered lightning bolts."""
    # Slow cloud travel for a longer, dramatic loop
    cx1 = int((t * 0.25 % 1.0) * 25) - 5   # enters at -5, exits at 20
    cx2 = cx1 - 4
    draw_cloud(canvas, cx1, 0, 11, 3, CLOUD_STORM, CLOUD_DIM, max_col=10)
    draw_cloud(canvas, cx2, 1,  9, 2, CLOUD_DIM,  CLOUD_STORM, max_col=10)

    # Heavy diagonal rain below cloud
    rain_center = max(0, min(8, cx1 + 5))
    for offset, ph in [(-3,0.0),(-1,0.18),(1,0.36),(3,0.54),(-2,0.72),(2,0.88)]:
        col_base = rain_center + offset
        for di in range(2):
            phase = (t * 1.3 + di * 0.5 + ph) % 1.0
            dr = int(phase * 7) + 3
            dc = col_base + int(phase * 2)
            v_b = int((0.85 - phase * 0.3) * 255)
            if 0 <= dc <= 9:
                px(canvas, dr,   dc, bright(RAIN, v_b))
                px(canvas, dr+1, dc, bright(RAIN_DIM, int(v_b * 0.6)))

    FLASH_WHITE = (0, 0, 255)   # pure white for cloud flash — sat=0, no colour cast

    # Lightning bolt 1 — fires at t≈0.15 cycle
    bl1 = (t * 2.0) % 1.0
    if bl1 < 0.15:
        b1 = math.sin(bl1 / 0.15 * math.pi)
        lx1 = max(1, min(7, cx1 + 3))
        for i, (lr, lc) in enumerate([(2,lx1),(3,lx1-1),(4,lx1),(5,lx1+1),(6,lx1),(7,lx1-1)]):
            px(canvas, lr, lc, dim(BOLT if i%2==0 else BOLT_PUR, b1))
        # Entire cloud flashes white — intensity scales with bolt brightness
        for r in range(3):
            for c in range(10):
                if canvas[r][c] != OFF:
                    flash_v = int(b1 * 255)
                    px(canvas, r, c, bright(FLASH_WHITE, flash_v))

    # Lightning bolt 2 — offset by 0.5 cycle, different shape
    bl2 = (t * 2.0 + 0.5) % 1.0
    if bl2 < 0.12:
        b2 = math.sin(bl2 / 0.12 * math.pi)
        lx2 = max(2, min(8, cx1 + 6))
        for i, (lr, lc) in enumerate([(2,lx2+1),(3,lx2),(4,lx2+1),(5,lx2),(6,lx2+1),(7,lx2)]):
            px(canvas, lr, lc, dim(BOLT_PUR if i%2==0 else BOLT, b2))
        for r in range(3):
            for c in range(10):
                if canvas[r][c] != OFF:
                    flash_v = int(b2 * 255)
                    px(canvas, r, c, bright(FLASH_WHITE, flash_v))

def icon_night_clear(canvas, t, **_):
    for r in range(8):
        for c in range(10):
            px(canvas, r, c, NIGHT_SKY)
    stars = [
        (0,1),(0,4),(0,7),(0,9),
        (1,3),(1,5),(1,8),
        (2,0),(2,4),(2,7),(2,9),
        (3,3),(3,6),(3,9),
        (4,1),(4,4),(4,7),
        (5,0),(5,3),(5,6),(5,9),
        (6,1),(6,4),(6,7),
        (7,0),(7,3),(7,6),(7,9),
    ]
    _draw_stars(canvas, t, stars)
    _draw_shooting_star(canvas, t)
    pulse = 0.85 + 0.15 * math.sin(t * 2 * math.pi)
    _draw_crescent(canvas, pulse)

def icon_night_partly_cloudy(canvas, t, **_):
    """Night sky: crescent, stars, shooting star, white cloud with blue star peeks."""
    for r in range(8):
        for c in range(10):
            px(canvas, r, c, NIGHT_SKY)

    # Stars drawn first (cloud will overwrite them, then we poke some back through)
    stars = [
        (0,1),(0,4),(0,7),(0,9),
        (1,3),(1,6),(1,8),
        (2,0),(2,4),(2,8),
        (3,2),(3,5),(3,9),
        (4,1),(4,4),(4,7),(4,9),
        (5,0),(5,3),(5,6),
        (6,1),(6,4),(6,8),
        (7,0),(7,3),(7,7),
    ]
    _draw_stars(canvas, t, stars, seed_offset=5)
    _draw_shooting_star(canvas, t)
    pulse = 0.70 + 0.20 * math.sin(t * 2 * math.pi)
    _draw_crescent(canvas, pulse)

    # Cloud: pure white, zone=10, w=7, cx=int(t*10)
    NIGHT_CLOUD_TOP = (0, 0, 200)
    NIGHT_CLOUD_BOT = (0, 0, 130)
    cx = int((t % 1.0) * 10)
    draw_cloud_tiled(canvas, cx, 4, 7, 3, NIGHT_CLOUD_TOP, NIGHT_CLOUD_BOT, zone_w=10)

    # Blue star peeks: after drawing cloud, restore a few star positions
    # inside the cloud area so it looks like night sky twinkling through.
    # Use a deterministic but t-varying set so they shimmer.
    peek_stars = [(4,2),(4,5),(5,1),(5,4),(5,7),(6,2),(6,6),(7,1),(7,5)]
    for sr, sc_p in peek_stars:
        # Only show if this position is currently inside the cloud (has cloud pixel)
        if not (0 <= sc_p < 10): continue
        h, s, v = canvas[sr][sc_p]
        if s == 0 and v > 80:   # cloud pixel (sat=0, bright) — poke a star through
            # Shimmer: use a hash of position + slow time to get stable random flicker
            seed_val = (sr * 37 + sc_p * 13 + int(t * 8)) % 7
            if seed_val < 3:    # ~43% chance each frame = nice shimmer
                twinkle = 0.3 + 0.4 * abs(math.sin(t * 2 * math.pi + sr * 0.7))
                peek_col = dim(STAR_DIM, twinkle)
                px(canvas, sr, sc_p, peek_col)

ICONS = {0:icon_sunny, 1:icon_partly_cloudy, 2:icon_cloudy,
         3:icon_rainy,  4:icon_snowy,         5:icon_thunderstorm,
         6:icon_night_clear, 7:icon_night_partly_cloudy}


def draw_text_zone(canvas, temp_f, high_f, low_f, sep=True, is_night=False, text_start=11):
    # Col 10 separator (omitted for cloudy which draws its own at col 12)
    if sep:
        for r in range(ROWS):
            px(canvas, r, 10, C_SEP)

    # ── Current temp: tall, 7 wide, rows 0-6 ────────────────────────────
    # text_start=11 normally, =13 for cloudy (which has sep at col 12)
    s = f"-{abs(temp_f)}" if temp_f < 0 else str(temp_f)
    tcol = temp_color(temp_f)
    w = len(s)*4 - 1
    c0 = text_start + max(0, (7-w)//2)
    x  = c0
    for ch in s:
        pat = TALL7.get(ch, TALL7[' '])
        for r, bits in enumerate(pat):
            for c, b in enumerate(bits):
                if b == '#':
                    px(canvas, r, x+c, tcol)
        x += 4

    # ── Right zone: High (day) or Low (night), cols 18-23 ────────────────
    # Day = show High in red, Night = show Low in blue
    show_val  = low_f  if is_night else high_f
    show_col  = C_LOW  if is_night else C_HIGH
    show_lbl  = 'L'    if is_night else 'H'

    # Cap at 99, use overflow dot for 100+
    overflow = abs(show_val) >= 100
    if show_val < 0:
        s2 = f"-{min(9, abs(show_val))}"   # "-9" max for negative
    else:
        s2 = str(min(99, show_val))

    # TALL7 at 3px spacing fits 2 digits exactly in 6 cols
    # Fallback to HL4 for 3-char strings (shouldn't happen after capping)
    use_tall = len(s2) <= 2
    spacing = 3
    w2 = len(s2) * spacing
    x2 = (text_start + 7) + max(0, (6 - w2) // 2)
    x = x2
    for ch in s2:
        if use_tall:
            pat = TALL7.get(ch, TALL7[' '])
            for r, bits in enumerate(pat):
                for c, b in enumerate(bits):
                    if b == '#':
                        px(canvas, r, x+c, show_col)
        else:
            pat = HL4.get(ch, HL4[' '])
            for r, bits in enumerate(pat):
                for c, b in enumerate(bits):
                    if b == '#':
                        px(canvas, r+2, x+c, show_col)
        x += spacing

    hl_right_start = text_start + 7   # right zone starts 7 cols after text_start
    # Overflow indicator
    if overflow:
        px(canvas, 6, hl_right_start + 4, show_col)
        px(canvas, 6, hl_right_start + 5, show_col)

    # Small H or L label at row 7
    lbl_pat = TINY3.get(show_lbl, TINY3[' '])
    lbl_col = dim(show_col, 0.55)
    lx = hl_right_start + 2
    for r, bits in enumerate(lbl_pat):
        if r == 0:
            for c, b in enumerate(bits):
                if b == '#':
                    px(canvas, 7, lx+c, lbl_col)


# ── Frame builder ─────────────────────────────────────────────────────────────
# Codes that need more frames for a clean long loop
_FRAME_COUNTS = {1: 20, 2: 20, 5: 20, 7: 30}  # partly=20, cloudy=20, thunder=20, night_pc=30

def build_frames(weather=0, temp_f=72, high_f=85, low_f=58,
                 wind_mph=0, num_frames=None):
    if num_frames is None:
        num_frames = _FRAME_COUNTS.get(weather, 10)
    icon_fn = ICONS.get(weather, icon_cloudy)
    frames = []
    for i in range(num_frames):
        t = i / num_frames
        canvas = new_canvas()
        icon_fn(canvas, t, wind_mph=wind_mph)
        draw_text_zone(canvas, temp_f, high_f, low_f, sep=True, is_night=(weather >= 6))
        flat = []
        for r in range(ROWS):
            for c in range(COLS):
                flat.extend(canvas[r][c])
        assert len(flat) == FRAME_BYTES
        frames.append(flat)
    return frames

# ── ASCII preview ─────────────────────────────────────────────────────────────
def preview(frames, weather, temp_f, high_f, low_f):
    names = {0:'Sunny',1:'Partly Cloudy',2:'Cloudy',
             3:'Rainy',4:'Snowy',5:'Thunderstorm'}
    print(f"\n{names.get(weather,'?')} | {temp_f}F | H:{high_f} L:{low_f}")
    # Show 3 frames to see animation
    for fi in [0, len(frames)//2, len(frames)-1]:
        flat = frames[fi]
        print(f"  [Frame {fi}]")
        for r in range(ROWS):
            row = "".join("#" if flat[(r*24+c)*3+2]>20 else "." for c in range(24))
            print(f"  R{r} |{row}|")

# ── HID send ──────────────────────────────────────────────────────────────────
def numIntoBytes(n):
    return [(n>>24)&0xFF, (n>>16)&0xFF, (n>>8)&0xFF, n&0xFF]

def send_animation(dev, frames, fps=FPS):
    n = len(frames)
    print(f"Sending {n} frames @ {fps}fps...")
    hdr = [0xd1, 0x30, n, fps, ROWS, COLS] + [0]*26
    dev.write([0x00] + hdr)
    resp = dev.read(32, timeout_ms=2000)
    print(f"  ACK: {list(resp[:8]) if resp else 'TIMEOUT'}")
    time.sleep(1.0)
    chunk_size = 25
    for fi, fdata in enumerate(frames):
        offset = 0
        while offset < FRAME_BYTES:
            chunk = fdata[offset:offset+chunk_size]
            goff = fi*FRAME_BYTES + offset
            ob = numIntoBytes(goff)
            pkt = [0xd1,0x31,ob[0],ob[1],ob[2],ob[3],len(chunk)] + list(chunk)
            pkt += [0]*(32-len(pkt))
            dev.write([0x00] + pkt[:32])
            offset += len(chunk)
            time.sleep(0.002)
        print(f"  Frame {fi+1}/{n}")
        if fi < n-1:
            time.sleep(0.320)
    print("Done!")

# ── Main ──────────────────────────────────────────────────────────────────────
def main():
    weather  = int(sys.argv[1]) if len(sys.argv) > 1 else 0
    temp_f   = int(sys.argv[2]) if len(sys.argv) > 2 else 72
    high_f   = int(sys.argv[3]) if len(sys.argv) > 3 else 85
    low_f    = int(sys.argv[4]) if len(sys.argv) > 4 else 58
    wind_mph = int(sys.argv[5]) if len(sys.argv) > 5 else 0

    frames = build_frames(weather, temp_f, high_f, low_f, wind_mph)
    preview(frames, weather, temp_f, high_f, low_f)

    dev = hid.device()
    try:
        dev.open_path(PATH)
    except:
        dev.open(VENDOR_ID, PRODUCT_ID)
    dev.set_nonblocking(False)
    print(f"Opened: {dev.get_product_string()}")
    send_animation(dev, frames)
    dev.close()

if __name__ == '__main__':
    main()
