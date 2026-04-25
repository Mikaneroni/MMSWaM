#!/usr/bin/env python3
"""
dp104_gui.pyw — DP104 Display Controller
Now Playing + Weather Pixel Display for TickType DP-104
Requires: pip install hidapi pystray pillow
Place dp104_weather_v2.py in the same folder.
"""

import sys, os, time, threading, importlib, re, subprocess, json, urllib.request, socket, colorsys
_NO_WINDOW = 0x08000000
from pathlib import Path

try:
    import tkinter as tk
    from tkinter import ttk
except ImportError:
    sys.exit("tkinter not available")

try:
    import pystray
    from pystray import MenuItem, Menu
except ImportError:
    sys.exit("pip install pystray")

try:
    from PIL import Image, ImageDraw
except ImportError:
    sys.exit("pip install pillow")

# ── HID ───────────────────────────────────────────────────────────────────────
_hid = None
for _name in ('hid', 'hidapi'):
    try:
        m = importlib.import_module(_name)
        if hasattr(m, 'enumerate'):
            _hid = m; break
    except ImportError:
        continue

RAW_USAGE_PAGE   = 0xFF60
DP104_VID        = 0xe560
DP104_PID        = 0xe104
DP104_PIXEL_PATH = b'\\\\?\\HID#VID_E560&PID_E104&MI_01#7&180b41ba&0&0000#{4d1e55b2-f16f-11cf-88cb-001111000030}'
MAX_TEXT_LEN     = 30
PIXEL_W, PIXEL_H = 24, 8
FRAME_BYTES      = PIXEL_W * PIXEL_H * 3

# ── Theme ─────────────────────────────────────────────────────────────────────
BG   = '#0b0c14'   # near-black background
BG2  = '#13141f'   # card/panel background
BG3  = '#1c1e2e'   # input / selected
ACC  = '#00e5a0'   # teal-green accent
ACC2 = '#4fc3f7'   # light blue (secondary)
FG   = '#dde1f0'   # primary text
DIM  = '#4a4e6a'   # dimmed text / labels
RED  = '#ff4d5e'   # stop / error
ORG  = '#ffb347'   # warning / orange
SEP  = '#1e2032'   # separator line color

# ── Text protocol ─────────────────────────────────────────────────────────────
def make_text_packet(block, offset, text_bytes):
    payload = bytearray(32)
    payload[0]=0x07; payload[1]=0x1a; payload[2]=0x05
    payload[3]=block; payload[4]=offset; payload[5]=len(text_bytes)
    payload[6:6+len(text_bytes)] = text_bytes
    return bytes(payload)

def sanitize(text):
    replacements = {
        '\u2014':'-','\u2013':'-','\u2018':"'",'\u2019':"'",
        '\u201c':'"','\u201d':'"','\u2026':'',
        '\u00e9':'e','\u00e8':'e','\u00ea':'e','\u00e0':'a',
        '\u00e2':'a','\u00e1':'a','\u00f4':'o','\u00f3':'o',
        '\u00fc':'u','\u00fa':'u','\u00f6':'o','\u00e4':'a',
        '\u00f1':'n','\u00e7':'c','\u00df':'ss',
    }
    for u, a in replacements.items():
        text = text.replace(u, a)
    text = text.encode('ascii', errors='ignore').decode('ascii')
    parts = re.split(r' +- +', text)
    if len(parts) > 1:
        text = parts[0].strip()
        for p in parts[1:]:
            p = p.strip()
            if p: text += ' (' + p + ')'
    return re.sub(r' {2,}', ' ', text).strip().upper()

# ── Pixel protocol ────────────────────────────────────────────────────────────
def _rgb_to_hsv256(r, g, b):
    h, s, v = colorsys.rgb_to_hsv(r/255, g/255, b/255)
    return int(h*255), int(s*255), int(v*255)

def _num_into_bytes(n):
    return [(n>>24)&0xFF, (n>>16)&0xFF, (n>>8)&0xFF, n&0xFF]

def send_pixel_frames(dev, frames, fps=10):
    """Send pre-built HSV frames using the proven protocol."""
    n = len(frames)
    hsv_frames = []
    for frame in frames:
        if isinstance(frame[0], (list, tuple)):
            flat = []
            for (r, g, b) in frame:
                flat.extend(_rgb_to_hsv256(r, g, b))
            hsv_frames.append(flat)
        else:
            hsv_frames.append(list(frame))

    hdr = [0xd1, 0x30, n, fps, PIXEL_H, PIXEL_W] + [0]*26
    dev.write([0x00] + hdr)
    resp = dev.read(32, timeout_ms=2000)
    if not resp or resp[0] != 0xd1:
        raise RuntimeError(f"Bad header ACK: {list(resp[:4]) if resp else 'timeout'}")
    time.sleep(1.0)

    chunk_size = 25
    for fi, frame_data in enumerate(hsv_frames):
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
            time.sleep(0.320)

# ── HID helpers ───────────────────────────────────────────────────────────────
def find_dp104():
    if not _hid: return None
    for info in _hid.enumerate():
        if info['usage_page'] == RAW_USAGE_PAGE and info.get('vendor_id') == DP104_VID:
            return info
    for info in _hid.enumerate():
        if info['usage_page'] == RAW_USAGE_PAGE:
            return info
    return None

def send_to_keyboard(title, artist):
    """Send Now Playing text via scroll protocol."""
    if not _hid: return False
    info = find_dp104()
    if not info: return False
    try:
        dev = _hid.device()
        dev.open_path(info['path'])
        def send_block(block, text):
            enc = sanitize(text).encode('ascii', errors='replace')[:MAX_TEXT_LEN]
            pad = enc + b'\x00' * (MAX_TEXT_LEN - len(enc))
            dev.write([0x00] + list(make_text_packet(block, 0,    pad[0:26])))
            time.sleep(0.03)
            dev.write([0x00] + list(make_text_packet(block, 0x1a, pad[26:30])))
            time.sleep(0.03)
        send_block(0, title); send_block(1, artist)
        for b in (2, 3, 4): send_block(b, '')
        dev.close()
        return True
    except Exception:
        return False

def send_pixel_animation(frames, fps=10, retries=3, retry_delay=2.0):
    """Open keyboard on MI_01 interface and send pixel frames.
    Retries up to `retries` times on disconnect. Call from background thread."""
    if not _hid: return False, "HID library not available"

    last_err = "Unknown error"
    for attempt in range(1, retries + 1):
        dev = _hid.device()
        opened = False
        try:
            dev.open_path(DP104_PIXEL_PATH)
            dev.set_nonblocking(False)
            opened = True
        except Exception:
            pass
        if not opened:
            info = find_dp104()
            if not info:
                last_err = "Keyboard not found"
                time.sleep(retry_delay)
                continue
            try:
                dev.open_path(info['path'])
                dev.set_nonblocking(False)
                opened = True
            except Exception as e:
                last_err = str(e)
                time.sleep(retry_delay)
                continue
        try:
            send_pixel_frames(dev, frames, fps)
            dev.close()
            return True, "OK"
        except Exception as e:
            last_err = str(e)
            try: dev.close()
            except: pass
            if attempt < retries:
                time.sleep(retry_delay)  # wait before retry

    return False, f"Failed after {retries} attempts: {last_err}"

# ── Weather module loader ─────────────────────────────────────────────────────
def _load_weather_mod():
    try:
        import importlib.util
        p = Path(__file__).parent / "dp104_weather_v2.py"
        if not p.exists():
            return None
        spec = importlib.util.spec_from_file_location("dp104_weather_v2", str(p))
        mod  = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mod)
        return mod
    except Exception:
        return None

_WX_MOD = _load_weather_mod()

TEMP_C = (255, 180,  30)
HIGH_C = (255,  70,  50)
LOW_C  = ( 60, 150, 255)
WIND_C = ( 80, 220, 255)
SEP_C  = ( 20,  20,  35)

FONT = {
    ' ':[0b000]*5,'A':[0b010,0b101,0b111,0b101,0b101],'B':[0b110,0b101,0b110,0b101,0b110],
    'C':[0b011,0b100,0b100,0b100,0b011],'D':[0b110,0b101,0b101,0b101,0b110],
    'E':[0b111,0b100,0b110,0b100,0b111],'F':[0b111,0b100,0b110,0b100,0b100],
    'G':[0b011,0b100,0b101,0b101,0b011],'H':[0b101,0b101,0b111,0b101,0b101],
    'I':[0b111,0b010,0b010,0b010,0b111],'J':[0b001,0b001,0b001,0b101,0b010],
    'K':[0b101,0b101,0b110,0b101,0b101],'L':[0b100,0b100,0b100,0b100,0b111],
    'M':[0b101,0b111,0b111,0b101,0b101],'N':[0b101,0b111,0b111,0b111,0b101],
    'O':[0b010,0b101,0b101,0b101,0b010],'P':[0b110,0b101,0b110,0b100,0b100],
    'Q':[0b010,0b101,0b101,0b111,0b011],'R':[0b110,0b101,0b110,0b101,0b101],
    'S':[0b011,0b100,0b010,0b001,0b110],'T':[0b111,0b010,0b010,0b010,0b010],
    'U':[0b101,0b101,0b101,0b101,0b010],'V':[0b101,0b101,0b101,0b010,0b010],
    'W':[0b101,0b101,0b111,0b111,0b101],'X':[0b101,0b010,0b010,0b010,0b101],
    'Y':[0b101,0b101,0b010,0b010,0b010],'Z':[0b111,0b001,0b010,0b100,0b111],
    '0':[0b010,0b101,0b101,0b101,0b010],'1':[0b010,0b110,0b010,0b010,0b111],
    '2':[0b110,0b001,0b010,0b100,0b111],'3':[0b111,0b001,0b011,0b001,0b111],
    '4':[0b101,0b101,0b111,0b001,0b001],'5':[0b111,0b100,0b110,0b001,0b110],
    '6':[0b011,0b100,0b110,0b101,0b010],'7':[0b111,0b001,0b001,0b010,0b010],
    '8':[0b010,0b101,0b010,0b101,0b010],'9':[0b010,0b101,0b011,0b001,0b110],
    '-':[0b000,0b000,0b111,0b000,0b000],'/': [0b001,0b001,0b010,0b100,0b100],
    '.':[0b000,0b000,0b000,0b000,0b010],'%':[0b101,0b001,0b010,0b100,0b101],
    '+':[0b000,0b010,0b111,0b010,0b000],
}

def draw_text(p, text, row, col, color, max_w=15, rows=5):
    x = col
    for ch in text.upper():
        glyph = FONT.get(ch, FONT[' '])
        for r in range(min(rows, 5)):
            for b in range(3):
                if glyph[r] & (1 << (2-b)):
                    c = x + b
                    if c < col + max_w and 0 <= row+r < PIXEL_H and 0 <= c < PIXEL_W:
                        p[(row+r, c)] = color
        x += 4
        if x >= col + max_w: break
    return x

def make_frame(pixel_dict):
    frame = [(0,0,0)] * (PIXEL_W * PIXEL_H)
    for (row, col), color in pixel_dict.items():
        if 0 <= row < PIXEL_H and 0 <= col < PIXEL_W:
            frame[row * PIXEL_W + col] = color
    return frame

def _get_weather_code(condition):
    c = condition.lower()
    if any(w in c for w in ['thunder','storm','tornado']): return 5
    if any(w in c for w in ['snow','sleet','blizzard','ice','flurr']): return 4
    if any(w in c for w in ['rain','drizzle','shower','pour']): return 3
    if any(w in c for w in ['partly','cloud','fog','mist','haze']): return 1
    if any(w in c for w in ['overcast','cloudy']): return 2
    return 0

def build_weather_frames(weather):
    if _WX_MOD is not None:
        try:
            code     = _get_weather_code(weather.get('cond', ''))
            temp_f   = int(weather.get('temp',  72))
            high_f   = int(weather.get('high',  85))
            low_f    = int(weather.get('low',   58))
            wind_mph = int(weather.get('wind',   0))
            is_day   = weather.get('is_day', True)
            if not is_day and code == 0: code = 6
            elif not is_day and code == 1: code = 7
            return _WX_MOD.build_frames(code, temp_f, high_f, low_f,
                                        wind_mph)  # num_frames from _FRAME_COUNTS
        except Exception:
            pass
    # Fallback renderer
    icon_fn  = lambda f: {}
    temp     = str(weather.get('temp', '--'))
    wind     = str(weather.get('wind', '--'))
    high     = str(weather.get('high', '--'))
    low      = str(weather.get('low',  '--'))
    is_am    = time.localtime().tm_hour < 14
    hl_str   = f"H{high}" if is_am else f"L{low}"
    hl_color = HIGH_C if is_am else LOW_C
    frames   = []
    for f in range(4):
        p = {}
        for r in range(PIXEL_H): p[(r,8)] = SEP_C
        draw_text(p, f"{temp}F", row=0, col=9, color=TEMP_C, max_w=14)
        for c in range(9, PIXEL_W): p[(3,c)] = SEP_C
        draw_text(p, hl_str, row=4, col=9, color=hl_color, max_w=14)
        flat = [0]*FRAME_BYTES
        for (row,col),(r,g,b) in p.items():
            idx = (row*PIXEL_W+col)*3
            h2,s2,v2 = _rgb_to_hsv256(r,g,b)
            flat[idx],flat[idx+1],flat[idx+2] = h2,s2,v2
        frames.append(flat)
    return frames

# ── Location resolver ─────────────────────────────────────────────────────────
def _resolve_location(location):
    loc = location.strip()
    if re.match(r'^[0-9]{5}$', loc):
        try:
            url = f"https://api.zippopotam.us/us/{loc}"
            req = urllib.request.Request(url, headers={'User-Agent': 'dp104/1.0'})
            with urllib.request.urlopen(req, timeout=8) as resp:
                data = json.loads(resp.read())
            place = data['places'][0]
            city  = place['place name']
            state = place['state abbreviation']
            return f"{city},{state}", f"{city}, {state} ({loc})"
        except Exception:
            pass
    return loc, loc

def fetch_weather(location="03275"):
    try:
        wttr_loc, display_name = _resolve_location(location)
        loc = wttr_loc.replace(' ', '+')
        url = f"https://wttr.in/{loc}?format=j1"
        socket.setdefaulttimeout(10)
        req = urllib.request.Request(url, headers={'User-Agent': 'curl/7.0'})
        with urllib.request.urlopen(req, timeout=12) as resp:
            data = json.loads(resp.read())
        cur = data['current_condition'][0]
        day = data['weather'][0]
        try:
            astronomy  = day.get('astronomy', [{}])[0]
            def parse_t(s):
                import datetime
                return datetime.datetime.strptime(s.strip(), '%I:%M %p').time()
            now_t  = __import__('datetime').datetime.now().time()
            is_day = parse_t(astronomy.get('sunrise','06:00 AM')) <= now_t <= \
                     parse_t(astronomy.get('sunset', '08:00 PM'))
        except Exception:
            is_day = 6 <= __import__('datetime').datetime.now().hour < 20
        return {
            'temp': cur['temp_F'], 'feels': cur['FeelsLikeF'],
            'humid': cur['humidity'], 'wind': cur['windspeedMiles'],
            'wdir': cur.get('winddir16Point',''), 'cond': cur['weatherDesc'][0]['value'],
            'high': day['maxtempF'], 'low': day['mintempF'],
            'is_day': is_day, 'display_name': display_name,
        }
    except Exception:
        return None

# ── Media detection ───────────────────────────────────────────────────────────
def get_now_playing():
    try:
        ps = r"""
Add-Type -AssemblyName System.Runtime.WindowsRuntime
function Await { param($AsyncOp, $Type)
    $asTask = [System.WindowsRuntimeSystemExtensions].GetMethods() |
        Where-Object { $_.Name -eq 'AsTask' -and $_.GetParameters().Count -eq 1 -and
            $_.GetParameters()[0].ParameterType.Name -eq 'IAsyncOperation`1' } |
        Select-Object -First 1
    $task = $asTask.MakeGenericMethod($Type).Invoke($null, @($AsyncOp))
    $task.Wait() | Out-Null; return $task.Result }
$null = [Windows.Media.Control.GlobalSystemMediaTransportControlsSessionManager,Windows.Media.Control,ContentType=WindowsRuntime]
$mgrType = [Windows.Media.Control.GlobalSystemMediaTransportControlsSessionManager]
$mgr = Await ($mgrType::RequestAsync()) $mgrType
$propsType = [Windows.Media.Control.GlobalSystemMediaTransportControlsSessionMediaProperties]
$playing = [Windows.Media.Control.GlobalSystemMediaTransportControlsSessionPlaybackStatus]::Playing
$results = @()
foreach ($s in $mgr.GetSessions()) {
    if ($s.GetPlaybackInfo().PlaybackStatus -eq $playing) {
        $p = Await ($s.TryGetMediaPropertiesAsync()) $propsType
        if ($p -and $p.Title) { $results += [PSCustomObject]@{App=$s.SourceAppUserModelId;Title=$p.Title;Artist=$p.Artist} }
    }
}
$r = $results | Where-Object {$_.App -eq 'Spotify.exe' -and $_.Artist} | Select-Object -First 1
if (-not $r) { $r = $results | Where-Object {$_.App -eq 'Spotify.exe'} | Select-Object -First 1 }
if (-not $r) { $r = $results | Where-Object {$_.Artist} | Select-Object -First 1 }
if (-not $r) { $r = $results | Select-Object -First 1 }
if ($r) { Write-Output ($r.Title + "|" + $r.Artist) }
"""
        result = subprocess.run(['powershell','-NoProfile','-NonInteractive','-Command',ps],
                                capture_output=True, text=True, timeout=8,
                                creationflags=_NO_WINDOW)
        lines = [l.strip() for l in result.stdout.strip().splitlines() if l.strip()]
        out = lines[-1] if lines else ''
        if out and '|' in out:
            t, a = out.split('|', 1)
            if t.strip(): return (t.strip(), a.strip())
    except Exception:
        pass
    return None

# ── Windows toast notification ────────────────────────────────────────────────
def _toast_weather(data):
    """Show a Windows toast notification for a successful weather update.
    Only fires when called — never on NP updates, never if nothing changed."""
    try:
        cond  = data.get('cond',  'Unknown')
        temp  = data.get('temp',  '--')
        high  = data.get('high',  '--')
        low   = data.get('low',   '--')
        name  = data.get('display_name', 'Weather')
        msg   = f"{cond}  {temp}°F   H:{high}  L:{low}"
        title = f"DP-104 Weather — {name}"
        # Use PowerShell BurntToast-style toast via Windows Script Host
        ps_script = f"""
[Windows.UI.Notifications.ToastNotificationManager, Windows.UI.Notifications, ContentType=WindowsRuntime] | Out-Null
[Windows.UI.Notifications.ToastNotification, Windows.UI.Notifications, ContentType=WindowsRuntime] | Out-Null
[Windows.Data.Xml.Dom.XmlDocument, Windows.Data.Xml.Dom, ContentType=WindowsRuntime] | Out-Null
$template = @"
<toast>
  <visual><binding template="ToastGeneric">
    <text>{title}</text>
    <text>{msg}</text>
  </binding></visual>
</toast>
"@
$xml = New-Object Windows.Data.Xml.Dom.XmlDocument
$xml.LoadXml($template)
$toast = New-Object Windows.UI.Notifications.ToastNotification $xml
[Windows.UI.Notifications.ToastNotificationManager]::CreateToastNotifier("DP104Controller").Show($toast)
"""
        subprocess.Popen(
            ['powershell', '-NoProfile', '-NonInteractive', '-WindowStyle', 'Hidden',
             '-Command', ps_script],
            creationflags=_NO_WINDOW, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    except Exception:
        pass  # toast is best-effort — never crash the app over it

# ── Tray icon ─────────────────────────────────────────────────────────────────
def make_tray_icon(connected=True):
    img = Image.new('RGBA', (64,64), (0,0,0,0))
    d = ImageDraw.Draw(img)
    color = '#00e5a0' if connected else '#555555'
    d.rounded_rectangle([4,18,60,46], radius=6, fill='#0b0c14', outline=color, width=2)
    for _, y in enumerate([26,34]):
        for col in range(7):
            x = 10 + col*8
            d.rectangle([x,y,x+4,y+4], fill=color)
    d.ellipse([44,38,52,46], fill=color)
    d.line([52,30,52,42], fill=color, width=2)
    d.line([52,30,58,27], fill=color, width=2)
    return img

# ── Pixel preview widget ──────────────────────────────────────────────────────
class PixelPreview(tk.Canvas):
    CELL = 11
    GAP  = 2

    def __init__(self, parent, **kw):
        cw = PIXEL_W * (self.CELL + self.GAP) + self.GAP
        ch = PIXEL_H * (self.CELL + self.GAP) + self.GAP
        super().__init__(parent, width=cw, height=ch,
                         bg='#04040d', highlightthickness=1,
                         highlightbackground=DIM, **kw)
        self._rects = []
        for row in range(PIXEL_H):
            for col in range(PIXEL_W):
                x1 = self.GAP + col*(self.CELL+self.GAP)
                y1 = self.GAP + row*(self.CELL+self.GAP)
                x2 = x1 + self.CELL - 1
                y2 = y1 + self.CELL - 1
                self._rects.append(
                    self.create_rectangle(x1,y1,x2,y2, fill='#07071a', outline=''))

    def set_frame(self, pixels):
        # Single flat frame: list of FRAME_BYTES ints (HSV triplets)
        if isinstance(pixels, (bytes, bytearray)) or (
                isinstance(pixels, list) and len(pixels) == FRAME_BYTES
                and len(pixels) > 0 and isinstance(pixels[0], int)):
            for i in range(PIXEL_W * PIXEL_H):
                h = pixels[i*3]   / 255
                s = pixels[i*3+1] / 255
                v = pixels[i*3+2] / 255
                if v < 0.05:
                    self.itemconfig(self._rects[i], fill='#07071a')
                else:
                    r2,g2,b2 = colorsys.hsv_to_rgb(h, s, v)
                    self.itemconfig(self._rects[i],
                        fill=f'#{int(r2*255):02x}{int(g2*255):02x}{int(b2*255):02x}')
        else:
            for i, (r,g,b) in enumerate(pixels):
                self.itemconfig(self._rects[i],
                    fill='#07071a' if (r+g+b)<12 else f'#{r:02x}{g:02x}{b:02x}')

# ── Helpers ───────────────────────────────────────────────────────────────────
def _divider(parent, padx=0, pady=(8,0)):
    tk.Frame(parent, bg=SEP, height=1).pack(fill='x', padx=padx, pady=pady)

def _label(parent, text, **kw):
    defaults = dict(font=('Consolas',8), bg=BG2, fg=DIM, anchor='w')
    defaults.update(kw)
    return tk.Label(parent, text=text, **defaults)

def _card(parent, **kw):
    defaults = dict(bg=BG2, padx=14, pady=10)
    defaults.update(kw)
    return tk.Frame(parent, **defaults)

def _btn(parent, text, cmd, fg=FG, bg=BG3, **kw):
    defaults = dict(font=('Consolas',9), bg=bg, fg=fg, relief='flat',
                    cursor='hand2', activebackground=BG3, activeforeground=fg,
                    padx=10, pady=5)
    defaults.update(kw)
    b = tk.Button(parent, text=text, command=cmd, **defaults)
    b.bind('<Enter>', lambda e: b.config(bg='#252640'))
    b.bind('<Leave>', lambda e: b.config(bg=bg))
    return b

# ── Main App ──────────────────────────────────────────────────────────────────
class DP104App:
    def __init__(self):
        self.running    = False
        self.interval   = 10
        self.last_np    = (None, None)
        self.connected  = False
        self.thread     = None
        self.tray       = None
        self.mode       = 'nowplaying'
        self._wx_frames = []
        self._prev_idx  = 0
        self._wx_sending   = False
        self._wx_send_start = 0
        self._last_wx_data  = None
        self._fps = 10
        self._settings_path = Path(__file__).parent / "dp104_settings.json"

        self.root = tk.Tk()
        self.root.title("DP-104 Controller")
        self.root.resizable(True, True)
        self.root.minsize(480, 540)
        self.root.configure(bg=BG)
        self.root.protocol("WM_DELETE_WINDOW", self._quit)
        self.root.bind("<Unmap>", self._on_minimize)
        self.root.bind("<grave>", lambda e: self._open_debug())   # tilde/backtick key
        self.root.bind("<F1>",    lambda e: self._open_credits())  # F1 = credits
        self._debug_win = None
        # Temp override vars (initialised here, used by debug menu + _do_fetch_weather)
        self.temp_ovr_var     = tk.StringVar(value='')
        self.temp_ovr_enabled = tk.BooleanVar(value=False)
        self.debug_wx_var     = tk.IntVar(value=-1)   # -1 = use live weather code
        self.debug_wind_var   = tk.IntVar(value=0)    # wind override for debug
        # These are created in _build_ui; pre-declare so _load_settings can set them
        self.np_enabled       = None
        self.wx_enabled       = None

        self._build_ui()
        self._load_settings()   # load after vars exist
        self.root.after(10, self._style_tabs)   # set initial tab colours
        self._build_tray()
        self.toggle_running()
        self.root.after(2000, self._check_connection)
        self.root.after(400,  self._tick_preview)

    # ── UI ────────────────────────────────────────────────────────────────────
    def _build_ui(self):
        r = self.root

        # ── Header ────────────────────────────────────────────────────────────
        hdr = tk.Frame(r, bg=BG, padx=14, pady=10)
        hdr.pack(fill='x')

        tk.Label(hdr, text="DP-104", font=('Consolas',14,'bold'),
                 bg=BG, fg=ACC).pack(side='left')
        tk.Label(hdr, text="  DISPLAY CONTROLLER", font=('Consolas',10),
                 bg=BG, fg=DIM).pack(side='left')

        # Right side: stop button + connection dot
        self.dot = tk.Label(hdr, text="●", font=('Consolas',13), bg=BG, fg=DIM)
        self.dot.pack(side='right', padx=(6,0))
        self.btn_stop = tk.Button(
            hdr, text="■  STOP", font=('Consolas',8,'bold'),
            bg='#1a0a0e', fg=RED, relief='flat', cursor='hand2',
            padx=8, pady=3, activebackground='#2a1218', activeforeground='#ff6e7d',
            command=self._stop_all)
        self.btn_stop.pack(side='right')
        tk.Label(hdr, text="v1.1.3", font=('Consolas',7),
                 bg=BG, fg=DIM).pack(side='right', padx=(0,10))

        _divider(r, padx=14)

        # ── Mode tabs ─────────────────────────────────────────────────────────
        # Left-click  = switch to that tab
        # Right-click = toggle enable/disable (green=on, red=off)
        tabs = tk.Frame(r, bg=BG, padx=14, pady=6)
        tabs.pack(fill='x')
        self.mode_var   = tk.StringVar(value='nowplaying')
        self.np_enabled = tk.BooleanVar(value=True)
        self.wx_enabled = tk.BooleanVar(value=True)
        self._tab_btns  = {}   # val -> button widget for colour updates

        def _make_tab(val, label, en_var):
            btn = tk.Button(
                tabs, text=label,
                font=('Consolas',9,'bold'),
                bg='#0d2b1a', fg=ACC,          # starts green (enabled)
                relief='flat', padx=6, pady=6, cursor='hand2',
                activebackground=BG3, activeforeground=ACC,
                bd=0)
            btn.pack(side='left', padx=(0,4))
            self._tab_btns[val] = btn

            # Left-click → select this tab
            btn.bind('<Button-1>', lambda e, v=val: self._select_tab(v))
            # Right-click → toggle enabled
            btn.bind('<Button-3>', lambda e, v=val, ev=en_var: self._toggle_tab(v, ev))
            # Also bind on macOS two-finger click / Ctrl+click
            btn.bind('<Control-Button-1>', lambda e, v=val, ev=en_var: self._toggle_tab(v, ev))

        _make_tab('nowplaying', '  ♪  NOW PLAYING  ', self.np_enabled)
        _make_tab('weather',    '  ⛅  WEATHER  ',    self.wx_enabled)

        self.mode_var.trace_add('write', self._style_tabs)

        _divider(r, padx=14, pady=(0,0))

        # ── Now Playing panel ──────────────────────────────────────────────────
        self.np_panel = _card(r)

        _label(self.np_panel, "NOW PLAYING").pack(anchor='w', pady=(0,6))

        self.lbl_title = tk.Label(self.np_panel, text="—",
                                   font=('Consolas',13,'bold'), bg=BG2, fg=ACC,
                                   wraplength=420, justify='left', anchor='w')
        self.lbl_title.pack(fill='x')
        self.lbl_artist = tk.Label(self.np_panel, text="",
                                    font=('Consolas',10), bg=BG2, fg=FG,
                                    wraplength=420, justify='left', anchor='w')
        self.lbl_artist.pack(fill='x')

        _divider(self.np_panel, pady=(8,6))
        _label(self.np_panel, "ON KEYBOARD").pack(anchor='w', pady=(0,4))

        self.lbl_kb0 = tk.Label(self.np_panel, text="", font=('Courier New',10),
                                  bg='#06060f', fg=ACC, anchor='w', padx=8, pady=4)
        self.lbl_kb0.pack(fill='x', pady=(0,2))
        self.lbl_kb1 = tk.Label(self.np_panel, text="", font=('Courier New',10),
                                  bg='#06060f', fg='#80ffcc', anchor='w', padx=8, pady=4)
        self.lbl_kb1.pack(fill='x')

        # ── Weather panel ──────────────────────────────────────────────────────
        self.wx_panel = _card(r)

        # Location row
        loc_row = tk.Frame(self.wx_panel, bg=BG2)
        loc_row.pack(fill='x', pady=(0,6))
        _label(self.wx_panel, "WEATHER").pack(anchor='w', pady=(0,8))

        _label(loc_row, "Location", font=('Consolas',9), fg=FG).pack(side='left')
        self.loc_var = tk.StringVar(value="03275")
        loc_entry = tk.Entry(loc_row, textvariable=self.loc_var,
                             font=('Consolas',10), bg=BG3, fg=FG,
                             insertbackground=FG, relief='flat',
                             width=12, highlightthickness=1,
                             highlightbackground=DIM, highlightcolor=ACC)
        loc_entry.pack(side='left', padx=(8,4))
        _label(loc_row, "zip or City,ST", font=('Consolas',7)).pack(side='left', padx=(0,8))
        fetch_btn = _btn(loc_row, "⟳  FETCH", self._fetch_weather, fg=ACC, bg='#0d1a14')
        fetch_btn.pack(side='left')

        # Weather summary
        self.lbl_weather = tk.Label(self.wx_panel, text="Press FETCH to load weather.",
                                     font=('Consolas',9), bg=BG2, fg=DIM,
                                     justify='left', wraplength=420, anchor='w')
        self.lbl_weather.pack(fill='x')

        # Last updated + auto-refresh row
        meta_row = tk.Frame(self.wx_panel, bg=BG2)
        meta_row.pack(fill='x', pady=(4,0))
        self.lbl_wx_updated = tk.Label(meta_row, text="Last updated: never",
                                        font=('Consolas',8), bg=BG2, fg=DIM)
        self.lbl_wx_updated.pack(side='left')
        tk.Label(meta_row, text="   Auto-refresh:", font=('Consolas',8),
                 bg=BG2, fg=DIM).pack(side='left')
        self.wx_int_var = tk.StringVar(value='240')
        tk.Spinbox(meta_row, values=(15,30,60,120,240,480), width=4,
                   textvariable=self.wx_int_var, font=('Consolas',8),
                   bg=BG3, fg=FG, buttonbackground=BG3, insertbackground=FG,
                   relief='flat').pack(side='left', padx=4)
        tk.Label(meta_row, text="min", font=('Consolas',8),
                 bg=BG2, fg=DIM).pack(side='left')

        # Preview
        _divider(self.wx_panel, pady=(10,6))
        preview_hdr = tk.Frame(self.wx_panel, bg=BG2)
        preview_hdr.pack(fill='x', pady=(0,4))
        _label(preview_hdr, "PREVIEW").pack(side='left')
        self.lbl_frame = tk.Label(preview_hdr, text="",
                                   font=('Consolas',8), bg=BG2, fg=DIM)
        self.lbl_frame.pack(side='right')
        # FPS selector
        self.fps_var = tk.StringVar(value='10')
        fps_menu = tk.OptionMenu(preview_hdr, self.fps_var, '5', '10', '15', '20',
                                  command=lambda v: setattr(self, '_fps', int(v)))
        fps_menu.config(font=('Consolas',8), bg=BG3, fg=FG, relief='flat',
                        activebackground='#252640', activeforeground=FG,
                        highlightthickness=0, bd=0, width=2)
        fps_menu['menu'].config(font=('Consolas',8), bg=BG3, fg=FG,
                                activebackground=ACC, activeforeground=BG)
        fps_menu.pack(side='right', padx=(0,4))
        _label(preview_hdr, "fps:", font=('Consolas',8), fg=DIM).pack(side='right')

        self.preview = PixelPreview(self.wx_panel)
        self.preview.pack(anchor='w')

        # Now Playing ticker on weather tab
        _divider(self.wx_panel, pady=(10,6))
        _label(self.wx_panel, "NOW PLAYING").pack(anchor='w', pady=(0,4))

        self.lbl_wx_np_title = tk.Label(self.wx_panel, text="—",
                                         font=('Consolas',10,'bold'),
                                         bg='#06060f', fg=ACC,
                                         anchor='w', padx=8, pady=4,
                                         wraplength=440, justify='left')
        self.lbl_wx_np_title.pack(fill='x', pady=(0,2))
        self.lbl_wx_np_artist = tk.Label(self.wx_panel, text="",
                                          font=('Consolas',9),
                                          bg='#06060f', fg='#80ffcc',
                                          anchor='w', padx=8, pady=4,
                                          wraplength=440, justify='left')
        self.lbl_wx_np_artist.pack(fill='x')

        # ── Status bar ────────────────────────────────────────────────────────
        _divider(r, padx=14, pady=(6,0))
        status_bar = tk.Frame(r, bg=BG, padx=14, pady=6)
        status_bar.pack(fill='x')

        self.lbl_status = tk.Label(status_bar, text="Starting...",
                                    font=('Consolas',8), bg=BG, fg=DIM, anchor='w')
        self.lbl_status.pack(side='left', fill='x', expand=True)

        # Poll controls — right side
        self.btn_pause = tk.Button(
            status_bar, text="⏸  PAUSE", font=('Consolas',8,'bold'),
            bg=BG3, fg=ACC, relief='flat', cursor='hand2',
            padx=8, pady=3, activebackground='#252640', activeforeground=ACC,
            command=self.toggle_running)
        self.btn_pause.pack(side='right')

        tk.Label(status_bar, text="  |  poll:",
                 font=('Consolas',8), bg=BG, fg=DIM).pack(side='right')
        self.interval_var = tk.StringVar(value='10')
        tk.Label(status_bar, text="s",
                 font=('Consolas',8), bg=BG, fg=DIM).pack(side='right')
        tk.Spinbox(status_bar, from_=5, to=60, width=3,
                   textvariable=self.interval_var, font=('Consolas',8),
                   bg=BG3, fg=FG, buttonbackground=BG3,
                   insertbackground=FG, relief='flat',
                   command=self._on_interval).pack(side='right', padx=(0,2))
        self.interval_var.trace_add('write', lambda *_: self._on_interval())

        # ── Action buttons ────────────────────────────────────────────────────
        _divider(r, padx=14, pady=(0,0))
        btns = tk.Frame(r, bg=BG, padx=14, pady=10)
        btns.pack(fill='x')

        _btn(btns, "⟳  SEND NOW",    self._force_send).pack(side='left', padx=(0,6))
        _btn(btns, "↺  RELOAD ALL",  self._reload_all, fg=ACC, bg='#0d1a14').pack(side='left', padx=(0,6))
        _btn(btns, "🗑  CLEAR",       self._clear).pack(side='left', padx=(0,6))
        _btn(btns, "⊟  TRAY",        self.minimize_to_tray, fg=DIM).pack(side='right')

        # ── Init ──────────────────────────────────────────────────────────────
        self._on_mode_change()
        r.update_idletasks()
        w, h = 500, 605
        sw, sh = r.winfo_screenwidth(), r.winfo_screenheight()
        r.geometry(f"{w}x{h}+{(sw-w)//2}+{(sh-h)//2}")

    def _style_tabs(self, *_):
        """Colour the tab buttons: active=brighter, inactive=dimmer."""
        active = self.mode_var.get()
        for val, btn in self._tab_btns.items():
            enabled = (self.np_enabled if val=='nowplaying' else self.wx_enabled).get()
            if not enabled:
                btn.config(bg='#2b0a0e', fg=RED)   # red = disabled
            elif val == active:
                btn.config(bg='#0d3b22', fg=ACC)   # bright green = active+enabled
            else:
                btn.config(bg='#0a1f13', fg='#00a060')  # dim green = inactive+enabled

    def _select_tab(self, val):
        """Left-click: switch active tab view."""
        self.mode_var.set(val)
        self._on_mode_change()
        self._style_tabs()

    def _toggle_tab(self, tab, enabled_var):
        """Right-click: flip enabled state for that tab."""
        enabled_var.set(not enabled_var.get())
        enabled = enabled_var.get()

        if not enabled:
            # If we just disabled the active tab, switch to the other
            if self.mode_var.get() == tab:
                other    = 'weather' if tab == 'nowplaying' else 'nowplaying'
                other_en = self.wx_enabled if other=='weather' else self.np_enabled
                if other_en and other_en.get():
                    self.mode_var.set(other)
                    self._on_mode_change()
            # Clear keyboard page for that service
            def _clear():
                if tab == 'nowplaying':
                    send_to_keyboard('', '')
                    self.last_np = (None, None)
                    self.root.after(0, self._update_np_display, '', '')
            threading.Thread(target=_clear, daemon=True).start()

        self._style_tabs()
        lbl = "Now Playing" if tab == 'nowplaying' else "Weather"
        self._set_status(f"{lbl} {'enabled' if enabled else 'disabled'}")

    def _on_mode_change(self):
        self.mode = self.mode_var.get()
        self.np_panel.pack_forget()
        self.wx_panel.pack_forget()
        if self.mode == 'nowplaying':
            self.np_panel.pack(fill='x', padx=14, pady=(0,0))
        else:
            self.wx_panel.pack(fill='x', padx=14, pady=(0,0))
        self.root.update_idletasks()

    def _on_interval(self):
        try: self.interval = max(5, min(60, int(self.interval_var.get())))
        except ValueError: pass

    # ── Tray ──────────────────────────────────────────────────────────────────
    def _build_tray(self):
        self.tray = pystray.Icon(
            "dp104", make_tray_icon(False), "DP-104 Controller",
            menu=Menu(
                MenuItem("Show",     self._show_window, default=True),
                MenuItem("Send Now", lambda: self._force_send()),
                MenuItem("Clear",    lambda: self._clear()),
                Menu.SEPARATOR,
                MenuItem("Quit",     self._quit),
            ))

    def minimize_to_tray(self):
        self.root.withdraw()
        if not self.tray._running:
            threading.Thread(target=self.tray.run, daemon=True).start()

    def _show_window(self, icon=None, item=None):
        self.root.after(0, self.root.deiconify)
        self.root.after(0, self.root.lift)

    # ── Preview ticker ─────────────────────────────────────────────────────────
    def _tick_preview(self):
        if self._wx_frames:
            idx = self._prev_idx % len(self._wx_frames)
            self.preview.set_frame(self._wx_frames[idx])
            self.lbl_frame.config(text=f"frame {idx+1} / {len(self._wx_frames)}")
            self._prev_idx += 1
        # Match keyboard FPS. Keyboard has ~1s buffer load offset on first play,
        # but the preview loops continuously so we just match the frame interval.
        interval_ms = max(50, int(1000 / self._fps))
        self.root.after(interval_ms, self._tick_preview)

    # ── Poll loop ─────────────────────────────────────────────────────────────
    def toggle_running(self):
        self.running = not self.running
        if self.running:
            self.btn_pause.config(text="⏸  PAUSE")
            self._set_status("Polling...")
            if not self.thread or not self.thread.is_alive():
                self.thread = threading.Thread(target=self._poll_loop, daemon=True)
                self.thread.start()
        else:
            self.btn_pause.config(text="▶  RESUME")
            self._set_status("Paused")

    def _poll_loop(self):
        """
        Both NP and Weather run concurrently regardless of active tab.
        NP text protocol and weather pixel protocol use separate keyboard pages.
        """
        wx_countdown = 0
        wx_fetching  = False
        np_fetching  = False

        while True:
            if self.running:

                # ── Now Playing — runs when enabled, regardless of active tab ──
                np_on = not self.np_enabled or self.np_enabled.get()
                if np_on and not np_fetching:
                    np_fetching = True
                    def _np_done():
                        nonlocal np_fetching
                        try:
                            result = get_now_playing()
                            if result:
                                title, artist = result
                                if (title, artist) != self.last_np:
                                    ok = send_to_keyboard(title, artist)
                                    if ok:
                                        self.last_np = (title, artist)
                                        self.root.after(0, self._update_np_display,
                                                        title, artist)
                                        if self.mode == 'nowplaying':
                                            self._set_status("Now Playing updated")
                            else:
                                if self.last_np != (None, None):
                                    send_to_keyboard('', '')
                                    self.last_np = (None, None)
                                    self.root.after(0, self._update_np_display, '', '')
                        finally:
                            np_fetching = False
                    threading.Thread(target=_np_done, daemon=True).start()

                # ── Weather — runs when enabled, regardless of active tab ───
                wx_on = not self.wx_enabled or self.wx_enabled.get()
                if wx_on and wx_countdown <= 0 and not wx_fetching:
                    wx_fetching = True
                    def _wx_done():
                        nonlocal wx_fetching, wx_countdown
                        try:
                            self._do_fetch_weather()
                        finally:
                            try: wx_countdown = int(self.wx_int_var.get()) * 60
                            except: wx_countdown = 14400
                            wx_fetching = False
                    threading.Thread(target=_wx_done, daemon=True).start()
                elif wx_on and not wx_fetching:
                    wx_countdown -= self.interval
                    mins = max(0, wx_countdown) // 60
                    secs = max(0, wx_countdown) % 60
                    if self.mode == 'weather':
                        self._set_status(
                            f"Next weather refresh in {mins}m {secs:02d}s")
                    else:
                        self._set_status(
                            f"Weather refreshes in {mins}m {secs:02d}s  |  NP active")
                elif not wx_on and not np_on:
                    self._set_status("All services disabled")
                elif not wx_on:
                    self._set_status("Weather disabled  |  NP active")
                elif not np_on:
                    self._set_status("NP disabled  |  Weather active")

            time.sleep(self.interval)

    def _check_connection(self):
        info = find_dp104()
        self.connected = info is not None
        self.dot.config(fg=ACC if self.connected else RED)
        if self.tray:
            self.tray.icon = make_tray_icon(self.connected)
        self.root.after(5000, self._check_connection)

    # ── Actions ───────────────────────────────────────────────────────────────
    def _stop_all(self):
        self.running = False
        self.btn_pause.config(text="▶  RESUME")
        self._set_status("Stopped — display cleared")
        def _do():
            send_to_keyboard('', '')
        threading.Thread(target=_do, daemon=True).start()

    def _reload_all(self):
        def _do():
            result = get_now_playing()
            if result:
                title, artist = result
                send_to_keyboard(title, artist)
                self.last_np = (title, artist)
                self.root.after(0, self._update_np_display, title, artist)
            self._do_fetch_weather()
        threading.Thread(target=_do, daemon=True).start()

    def _force_send(self):
        def _do():
            if self.mode == 'nowplaying':
                result = get_now_playing()
                if result:
                    title, artist = result
                    ok = send_to_keyboard(title, artist)
                    if ok:
                        self.last_np = (title, artist)
                        self.root.after(0, self._update_np_display, title, artist)
                        self._set_status("Sent")
                    else:
                        self._set_status("Send failed")
                else:
                    self._set_status("Nothing playing")
            else:
                self._do_fetch_weather()
        threading.Thread(target=_do, daemon=True).start()

    def _clear(self):
        def _do():
            send_to_keyboard('', '')
            self.last_np = (None, None)
            self._wx_frames = []
            self._wx_sending = False
            self.root.after(0, self._update_np_display, '', '')
            self.root.after(0, self.lbl_frame.config, {'text': ''})
            self._set_status("Cleared")
        threading.Thread(target=_do, daemon=True).start()

    def _fetch_weather(self):
        threading.Thread(target=self._do_fetch_weather, daemon=True).start()

    def _do_fetch_weather(self):
        # Guard with a timeout — auto-release after 120s to prevent permanent lock
        if self._wx_sending:
            if time.time() - getattr(self, '_wx_send_start', 0) < 120:
                self._set_status("Already sending, please wait...")
                return
            else:
                self._wx_sending = False  # timed out — release the lock

        self._wx_sending = True
        self._wx_send_start = time.time()

        loc = self.loc_var.get().strip() or "03275"
        self._set_status(f"Fetching weather for {loc}...")
        self.root.after(0, self.lbl_weather.config,
                        {'text': f"Fetching {loc}...", 'fg': DIM})
        try:
            data = fetch_weather(loc)
        except Exception as e:
            self.root.after(0, self.lbl_weather.config,
                            {'text': f"Fetch error: {e}", 'fg': RED})
            self._set_status("Weather fetch failed")
            self._wx_sending = False
            return

        if not data:
            self.root.after(0, self.lbl_weather.config,
                            {'text': "Failed to fetch weather.", 'fg': RED})
            self._set_status("Weather fetch failed")
            self._wx_sending = False
            return

        # Apply temperature override if enabled
        if getattr(self, 'temp_ovr_enabled', None) and self.temp_ovr_enabled.get():
            try:
                data['temp'] = str(int(self.temp_ovr_var.get()))
            except (ValueError, AttributeError):
                pass  # invalid entry — use real temp

        display_name = data.get('display_name', loc)
        day_night = "Day" if data.get('is_day', True) else "Night"
        summary = (f"{display_name}  —  {data['cond']}  {data['temp']}°F  "
                   f"H:{data['high']}  L:{data['low']}  "
                   f"Wind {data['wind']}mph {data['wdir']}  [{day_night}]")
        self.root.after(0, self.lbl_weather.config, {'text': summary, 'fg': ACC})

        self._last_wx_data = data   # cache for debug menu
        # Apply debug animation override if set
        dbg_code = getattr(self, 'debug_wx_var', None)
        if dbg_code and dbg_code.get() >= 0 and _WX_MOD:
            try:
                t  = int(data.get('temp', 72))
                h2 = int(data.get('high', 85))
                l2 = int(data.get('low',  58))
                w2 = int(data.get('wind',  0))
                frames = _WX_MOD.build_frames(dbg_code.get(), t, h2, l2, w2)
            except Exception:
                frames = build_weather_frames(data)
        else:
            frames = build_weather_frames(data)
        self._wx_frames = frames
        self._set_status("Sending to keyboard...")
        time.sleep(0.1)
        ok, msg = send_pixel_animation(frames, fps=self._fps)
        self._wx_sending = False

        if ok:
            ts = time.strftime('%m/%d/%y %H:%M:%S')
            self.root.after(0, self.lbl_wx_updated.config,
                            {'text': f"Last updated: {ts}", 'fg': ACC2})
            self._set_status(
                f"Weather sent  ·  {data['temp']}°F  {data['cond'][:28]}")
            # Windows toast — only on successful weather send
            _toast_weather(data)
        else:
            self._set_status(f"Send failed: {msg}")

    def _update_np_display(self, title, artist):
        self.lbl_title.config(text=title or '—')
        self.lbl_artist.config(text=artist or '')
        self.lbl_kb0.config(text=sanitize(title)[:MAX_TEXT_LEN] if title else '')
        self.lbl_kb1.config(text=sanitize(artist)[:MAX_TEXT_LEN] if artist else '')
        self.lbl_wx_np_title.config(text=title or '—')
        self.lbl_wx_np_artist.config(text=artist or '')
        if self.tray and title:
            self.tray.title = title[:30]

    def _on_temp_override_toggle(self):
        """Enable/disable the temp override entry field."""
        if self.temp_ovr_enabled.get():
            self.temp_ovr_entry.config(state='normal')
            self.temp_ovr_entry.focus_set()
        else:
            self.temp_ovr_entry.config(state='disabled')
            self.temp_ovr_var.set('')

    def _set_status(self, msg):
        ts = time.strftime('%H:%M:%S')
        self.root.after(0, self.lbl_status.config, {'text': f"{ts}  {msg}"})

    # ── Quit ──────────────────────────────────────────────────────────────────
    def _on_minimize(self, event):
        """Minimize button in title bar -> go to tray."""
        if event.widget is self.root and self.root.winfo_viewable():
            self.minimize_to_tray()

    def _load_settings(self):
        """Load saved settings from JSON on startup."""
        try:
            if self._settings_path.exists():
                s = json.loads(self._settings_path.read_text())
                if 'location'    in s: self.loc_var.set(s['location'])
                if 'poll_sec'    in s:
                    self.interval_var.set(str(s['poll_sec']))
                    try: self.interval = int(s['poll_sec'])
                    except: pass
                if 'wx_refresh'  in s: self.wx_int_var.set(str(s['wx_refresh']))
                if 'fps'         in s:
                    self.fps_var.set(str(s['fps']))
                    self._fps = int(s['fps'])
                if 'mode'        in s:
                    self.mode_var.set(s['mode'])
                    self.root.after(50, self._on_mode_change)
                    self.root.after(60, self._style_tabs)
                if 'np_enabled'  in s and self.np_enabled:
                    self.np_enabled.set(bool(s['np_enabled']))
                if 'wx_enabled'  in s and self.wx_enabled:
                    self.wx_enabled.set(bool(s['wx_enabled']))
        except Exception:
            pass  # silently ignore corrupt settings

    def _save_settings(self):
        """Persist settings to JSON on exit."""
        try:
            s = {
                'location':   self.loc_var.get(),
                'poll_sec':   self.interval_var.get(),
                'wx_refresh': self.wx_int_var.get(),
                'fps':        self.fps_var.get(),
                'mode':       self.mode_var.get(),
                'np_enabled': self.np_enabled.get() if self.np_enabled else True,
                'wx_enabled': self.wx_enabled.get() if self.wx_enabled else True,
            }
            self._settings_path.write_text(json.dumps(s, indent=2))
        except Exception:
            pass

    def _open_debug(self):
        """Open the debug panel (tilde key). Attached to main window, not always-on-top."""
        if self._debug_win and tk.Toplevel.winfo_exists(self._debug_win):
            self._debug_win.lift()
            self._debug_win.focus_force()
            return

        win = tk.Toplevel(self.root)
        win.title("Debug  —  DP-104  v1.1.3")
        win.configure(bg=BG)
        win.resizable(False, False)
        win.transient(self.root)          # attached to parent: hides with it, no always-on-top
        win.bind("<grave>", lambda e: win.destroy())
        win.bind("<F1>",    lambda e: self._open_credits())
        self._debug_win = win

        # Position snapped to right edge of main window
        def _reposition(*_):
            if not tk.Toplevel.winfo_exists(win): return
            self.root.update_idletasks()
            rx = self.root.winfo_x() + self.root.winfo_width() + 8
            ry = self.root.winfo_y()
            win.geometry(f"+{rx}+{ry}")

        _reposition()
        # Track main window moves and snap debug alongside
        self.root.bind("<Configure>", _reposition, add='+')
        win.protocol("WM_DELETE_WINDOW",
                     lambda: [self.root.unbind("<Configure>"), win.destroy()])

        # ── Header ─────────────────────────────────────────────────────────────
        tk.Label(win, text="  ⚙  DEBUG MENU",
                 font=('Consolas',11,'bold'), bg=BG, fg=ORG,
                 pady=10, padx=14).pack(anchor='w')
        tk.Frame(win, bg=SEP, height=1).pack(fill='x', padx=14)

        # ── Weather animation override ─────────────────────────────────────────
        wx_card = _card(win)
        wx_card.pack(fill='x', padx=14, pady=(10,0))
        _label(wx_card, "FORCE WEATHER ANIMATION", fg=ORG).pack(anchor='w', pady=(0,8))

        WEATHER_NAMES = [
            (0, "0 — Sunny"),
            (1, "1 — Partly Cloudy"),
            (2, "2 — Cloudy (House)"),
            (3, "3 — Rainy"),
            (4, "4 — Snowy"),
            (5, "5 — Thunderstorm"),
            (6, "6 — Night Clear"),
            (7, "7 — Night Partly Cloudy"),
        ]
        self.debug_wx_var.set(-1)
        tk.Radiobutton(wx_card, text="—  Live (from weather API)",
                       variable=self.debug_wx_var, value=-1,
                       font=('Consolas',9), fg=FG, bg=BG2,
                       selectcolor=BG3, activebackground=BG2,
                       activeforeground=ACC, indicatoron=0,
                       width=26, pady=4, relief='flat', cursor='hand2',
                       command=self._apply_debug_preview).pack(fill='x', pady=1)
        for code, label in WEATHER_NAMES:
            tk.Radiobutton(wx_card, text=label,
                           variable=self.debug_wx_var, value=code,
                           font=('Consolas',9), fg=FG, bg=BG2,
                           selectcolor=BG3, activebackground=BG2,
                           activeforeground=ACC, indicatoron=0,
                           width=26, pady=4, relief='flat', cursor='hand2',
                           command=self._apply_debug_preview).pack(fill='x', pady=1)

        send_btn = _btn(wx_card, "▶  SEND TO KEYBOARD",
                        self._send_debug_animation, fg=BG, bg=ORG)
        send_btn.pack(anchor='w', pady=(10,0))

        # ── Wind speed override ────────────────────────────────────────────────
        tk.Frame(win, bg=SEP, height=1).pack(fill='x', padx=14, pady=(12,0))
        wind_card = _card(win)
        wind_card.pack(fill='x', padx=14, pady=(6,0))
        _label(wind_card, "WIND SPEED OVERRIDE (mph)", fg=ORG).pack(anchor='w', pady=(0,6))

        wind_row = tk.Frame(wind_card, bg=BG2)
        wind_row.pack(anchor='w')
        self.debug_wind_lbl = tk.Label(wind_row, text="0 mph",
                                        font=('Consolas',10,'bold'),
                                        bg=BG2, fg=FG, width=7, anchor='w')
        self.debug_wind_lbl.pack(side='left')
        self.debug_wind_var = tk.IntVar(value=0)
        def _wind_changed(v):
            mph = int(float(v))
            self.debug_wind_lbl.config(text=f"{mph} mph")
            self._apply_debug_preview()
        tk.Scale(wind_row, from_=0, to=60, orient='horizontal',
                 variable=self.debug_wind_var, command=_wind_changed,
                 length=180, bg=BG2, fg=DIM, troughcolor=BG3,
                 highlightthickness=0, sliderrelief='flat', bd=0,
                 activebackground=ORG).pack(side='left', padx=(6,0))
        _label(wind_card, "Affects rain slant and snow drift. 0 = straight down.",
               font=('Consolas',7)).pack(anchor='w', pady=(4,0))

        # ── Temperature override ───────────────────────────────────────────────
        tk.Frame(win, bg=SEP, height=1).pack(fill='x', padx=14, pady=(12,0))
        tmp_card = _card(win)
        tmp_card.pack(fill='x', padx=14, pady=(6,0))
        _label(tmp_card, "TEMPERATURE OVERRIDE", fg=ORG).pack(anchor='w', pady=(0,8))

        tmp_row = tk.Frame(tmp_card, bg=BG2)
        tmp_row.pack(anchor='w')
        self.temp_ovr_enabled.set(False)
        ovr_cb = tk.Checkbutton(tmp_row, text="Enable override:",
                                 variable=self.temp_ovr_enabled,
                                 font=('Consolas',9), fg=FG, bg=BG2,
                                 selectcolor=BG3, activebackground=BG2,
                                 activeforeground=FG, cursor='hand2',
                                 command=self._on_temp_override_toggle)
        ovr_cb.pack(side='left')
        self.temp_ovr_entry = tk.Entry(tmp_row, textvariable=self.temp_ovr_var,
                                        width=6, font=('Consolas',10),
                                        bg=BG3, fg=FG, insertbackground=FG,
                                        relief='flat', state='disabled',
                                        highlightthickness=1,
                                        highlightbackground=DIM,
                                        highlightcolor=ACC)
        self.temp_ovr_entry.pack(side='left', padx=(8,4))
        _label(tmp_row, "°F", fg=FG, font=('Consolas',9)).pack(side='left')
        _label(tmp_card, "Resets to live temp on next weather poll.",
               font=('Consolas',7)).pack(anchor='w', pady=(4,0))

        # ── Footer ─────────────────────────────────────────────────────────────
        tk.Frame(win, bg=SEP, height=1).pack(fill='x', padx=14, pady=(12,0))
        footer = tk.Frame(win, bg=BG, padx=14, pady=6)
        footer.pack(fill='x')
        tk.Label(footer, text="~  close   F1  credits",
                 font=('Consolas',7), bg=BG, fg=DIM).pack(side='left')

    def _apply_debug_preview(self):
        """Rebuild preview frames using debug overrides (animation, temp, wind)."""
        code = self.debug_wx_var.get()
        if code == -1:
            return  # live mode — let next real fetch populate
        try:
            temp_f = 72; high_f = 85; low_f = 58; wind = 0
            if hasattr(self, '_last_wx_data') and self._last_wx_data:
                d = self._last_wx_data
                try: temp_f = int(d.get('temp', 72))
                except: pass
                try: high_f = int(d.get('high', 85))
                except: pass
                try: low_f  = int(d.get('low',  58))
                except: pass
                try: wind   = int(d.get('wind',  0))
                except: pass
            # Apply overrides
            if self.temp_ovr_enabled.get():
                try: temp_f = int(self.temp_ovr_var.get())
                except: pass
            if hasattr(self, 'debug_wind_var'):
                wind = self.debug_wind_var.get()
            if _WX_MOD:
                frames = _WX_MOD.build_frames(code, temp_f, high_f, low_f,
                                              wind)  # num_frames from _FRAME_COUNTS
                self._wx_frames = frames
                self._prev_idx  = 0
        except Exception:
            pass

    def _send_debug_animation(self):
        """Send the currently previewed debug animation to the keyboard."""
        if not self._wx_frames:
            return
        frames = list(self._wx_frames)
        fps    = self._fps
        threading.Thread(
            target=lambda: send_pixel_animation(frames, fps=fps),
            daemon=True).start()

    def _open_credits(self):
        """F1 — Credits screen."""
        win = tk.Toplevel(self.root)
        win.title("Credits")
        win.configure(bg=BG)
        win.resizable(False, False)
        win.transient(self.root)
        win.bind("<F1>",    lambda e: win.destroy())
        win.bind("<grave>", lambda e: win.destroy())

        # Centre on main window
        self.root.update_idletasks()
        cx = self.root.winfo_x() + self.root.winfo_width()  // 2
        cy = self.root.winfo_y() + self.root.winfo_height() // 2
        win.update_idletasks()
        win.geometry(f"+{cx - 180}+{cy - 120}")

        outer = tk.Frame(win, bg=BG, padx=32, pady=28)
        outer.pack()

        tk.Label(outer, text="DP-104 DISPLAY CONTROLLER",
                 font=('Consolas',11,'bold'), bg=BG, fg=ACC).pack()
        tk.Label(outer, text="v1.1.3", font=('Consolas',9),
                 bg=BG, fg=DIM).pack(pady=(0,18))

        tk.Frame(outer, bg=SEP, height=1).pack(fill='x', pady=(0,18))

        rows = [
            ("Claude",  "Big Guy",   ACC2),
            ("Mikan",   "Human Guy", ORG),
        ]
        for name, role, color in rows:
            row = tk.Frame(outer, bg=BG)
            row.pack(fill='x', pady=4)
            tk.Label(row, text=name, font=('Consolas',13,'bold'),
                     bg=BG, fg=color, width=10, anchor='e').pack(side='left')
            tk.Label(row, text=f"  —  {role}", font=('Consolas',10),
                     bg=BG, fg=FG).pack(side='left')

        tk.Frame(outer, bg=SEP, height=1).pack(fill='x', pady=(18,10))

        tk.Label(outer, text="2026", font=('Consolas',10),
                 bg=BG, fg=DIM).pack()

        tk.Label(outer, text="F1 or ~  to close",
                 font=('Consolas',7), bg=BG, fg=DIM, pady=(10)).pack()

    def _quit(self, icon=None, item=None):
        self._save_settings()
        self.running = False
        if self.tray:
            try: self.tray.stop()
            except: pass
        self.root.after(0, self.root.destroy)

    def run(self):
        self.root.mainloop()


if __name__ == '__main__':
    if not _hid:
        import tkinter.messagebox as mb
        tk.Tk().withdraw()
        mb.showerror("Missing dependency", "pip install hidapi")
        sys.exit(1)
    app = DP104App()
    app.run()
