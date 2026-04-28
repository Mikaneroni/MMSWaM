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

# Optional: load dp104_nowplaying and dp104_discord if present alongside gui
_NP_MOD = None
_DISC_MOD = None
try:
    import importlib.util as _ilu
    for _mod_name, _mod_var in [('dp104_nowplaying', '_NP_MOD'),
                                  ('dp104_discord',   '_DISC_MOD')]:
        _p = Path(__file__).parent / f"{_mod_name}.py"
        if _p.exists():
            _spec = _ilu.spec_from_file_location(_mod_name, str(_p))
            _m    = _ilu.module_from_spec(_spec)
            _spec.loader.exec_module(_m)
            globals()[_mod_var] = _m
except Exception:
    pass

try:
    import tkinter as tk
    from tkinter import ttk
except ImportError:
    import ctypes
    ctypes.windll.user32.MessageBoxW(0, "tkinter not available. Reinstall Python with tkinter.", "DP-104 Error", 0x10)
    sys.exit(1)

def _report_callback_error(exc, val, tb):
    """Show full traceback in a message box instead of swallowing it."""
    import traceback as _tb
    msg = "".join(_tb.format_exception(exc, val, tb))
    print(f"[DP-104 ERROR]\n{msg}", file=sys.stderr)
    try:
        import tkinter.messagebox as _mb
        _mb.showerror("DP-104 Error", msg[:1000])
    except Exception:
        pass

try:
    import pystray
    from pystray import MenuItem, Menu
except ImportError:
    # pystray optional — disable tray if missing
    pystray = None
    MenuItem = None
    Menu = None

try:
    from PIL import Image, ImageDraw
except ImportError:
    # PIL optional — disable tray icon if missing
    Image = None
    ImageDraw = None

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

# ── Pixel send priority queue ─────────────────────────────────────────────────
# Priorities: lower number = higher priority
PRIO_DISCORD = 1   # Discord VC skin — highest priority
PRIO_NP      = 2   # Now Playing custom pixel page
PRIO_WEATHER = 3   # Weather animation — lowest priority

def _send_direct(frames, fps=10, retries=3, retry_delay=2.0):
    """Execute a pixel send synchronously. Called only from _PixelQueue worker."""
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


class _PixelQueue:
    """Single-worker priority queue for keyboard pixel sends.
    Highest-priority pending send wins. 4-second cooldown between sends
    gives the keyboard time to reload its animation buffer."""
    COOLDOWN = 4.0   # seconds between sends

    def __init__(self):
        self._lock    = threading.Lock()
        self._event   = threading.Event()
        self._pending = {}   # priority -> (frames, fps)
        self._running = True
        t = threading.Thread(target=self._worker, daemon=True)
        t.start()

    def submit(self, priority, frames, fps):
        """Queue a send. Drops any pending send of same or lower priority."""
        with self._lock:
            # Discard lower-priority queued sends (higher prio_number = lower prio)
            stale = [p for p in self._pending if p >= priority]
            for p in stale:
                del self._pending[p]
            self._pending[priority] = (list(frames), fps)
        self._event.set()

    def _worker(self):
        while self._running:
            self._event.wait()
            self._event.clear()
            with self._lock:
                if not self._pending:
                    continue
                best = min(self._pending)
                frames, fps = self._pending.pop(best)
            # Execute — direct call bypasses the queue (we're already serialized)
            _send_direct(frames, fps)
            # Cooldown gives keyboard time to reload animation buffer
            time.sleep(self.COOLDOWN)
            # If more work arrived during cooldown, wake up
            if self._pending:
                self._event.set()

_PIXEL_QUEUE = _PixelQueue()
DP104_VID        = 0xe560
DP104_PID        = 0xe104
DP104_PIXEL_PATH = b'\\\\?\\HID#VID_E560&PID_E104&MI_01#7&180b41ba&0&0000#{4d1e55b2-f16f-11cf-88cb-001111000030}'
MAX_TEXT_LEN     = 30
PIXEL_W, PIXEL_H = 24, 8
FRAME_BYTES      = PIXEL_W * PIXEL_H * 3

APP_VERSION = "1.2.5"

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

# ── Public pixel send API (routes through priority queue) ─────────────────────
def send_pixel_animation(frames, fps=10, priority=PRIO_WEATHER):
    """Submit a pixel animation to the priority queue.
    priority: PRIO_DISCORD(1), PRIO_NP(2), or PRIO_WEATHER(3).
    Returns immediately — actual send happens in queue worker with 4s cooldown."""
    _PIXEL_QUEUE.submit(priority, frames, fps)
    return True, "queued"

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
if ($r) { Write-Output ($r.Title + "|" + $r.Artist + "|" + $r.App) }
"""
        result = subprocess.run(['powershell','-NoProfile','-NonInteractive','-Command',ps],
                                capture_output=True, text=True, timeout=8,
                                creationflags=_NO_WINDOW)
        lines = [l.strip() for l in result.stdout.strip().splitlines() if l.strip()]
        out = lines[-1] if lines else ''
        if out and '|' in out:
            parts = out.split('|')
            t   = parts[0].strip()
            a   = parts[1].strip() if len(parts) > 1 else ''
            app = parts[2].strip() if len(parts) > 2 else ''
            if t: return (t, a, app)
    except Exception:
        pass
    return None

# ── Page switch command ───────────────────────────────────────────────────────
PAGE_OFF    = 0   # blank display
PAGE_CUSTOM = 2   # custom pixel animation (weather / discord / NP)
PAGE_SCROLL = 6   # scrolling text (NP)

def switch_page(dev, page):
    """Switch keyboard display page. Send two HID packets.
    page: PAGE_OFF(0), PAGE_CUSTOM(2), PAGE_SCROLL(6)"""
    # Packet 1: [0x07, 0x1a, 0x02, page, 0x00 ...]
    pkt1 = [0x00, 0x07, 0x1a, 0x02, page] + [0x00]*27
    dev.write(pkt1[:33])
    import time; time.sleep(0.05)
    # Packet 2: [0x09, 0x1a, 0x00 ...]
    pkt2 = [0x00, 0x09, 0x1a, 0x00] + [0x00]*29
    dev.write(pkt2[:33])

def switch_page_safe(page):
    """Open keyboard, switch page, close. Safe to call from any thread."""
    if not _hid: return
    dev = _hid.device()
    try:
        try:
            dev.open_path(DP104_PIXEL_PATH)
        except Exception:
            info = find_dp104()
            if not info: return
            dev.open_path(info['path'])
        dev.set_nonblocking(False)
        switch_page(dev, page)
        dev.close()
    except Exception as e:
        try: dev.close()
        except: pass

# ── Windows toast notification ─────────────────────────────────────────────────
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
    if not Image or not ImageDraw:
        return None
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
        self.root.report_callback_exception = _report_callback_error
        self.root.resizable(True, True)
        self.root.minsize(480, 540)
        self.root.configure(bg=BG)
        self.root.protocol("WM_DELETE_WINDOW", self._quit)
        # <Unmap> fires during initial draw on Windows — guard with a flag
        self._window_ready = False
        def _on_unmap(e):
            if self._window_ready and e.widget is self.root:
                self._on_minimize()
        self.root.bind("<Unmap>", _on_unmap)
        self.root.bind("<grave>", lambda e: self._open_debug())   # tilde/backtick key
        self.root.bind("<F1>",    lambda e: self._open_credits())  # F1 = credits
        self._debug_win = None
        # Temp override vars (initialised here, used by debug menu + _do_fetch_weather)
        self.temp_ovr_var     = tk.StringVar(value='')
        self.temp_ovr_enabled = tk.BooleanVar(value=False)
        self.debug_wx_var     = tk.IntVar(value=-1)   # -1 = use live weather code
        self._debug_np_src    = tk.StringVar(value='default')
        self._debug_np_playing= tk.BooleanVar(value=True)
        self._debug_np_eq     = tk.BooleanVar(value=True)
        self._debug_disc_key  = tk.StringVar(value='ggg')
        self.debug_wind_var   = tk.IntVar(value=0)    # wind override for debug
        # These are created in _build_ui; pre-declare so _load_settings can set them
        self.np_enabled         = None
        self.wx_enabled         = None
        self.discord_enabled    = None
        # Discord state
        self._discord_display   = None   # DiscordDisplay instance
        self._discord_connected = False
        self._discord_in_vc     = False
        self._discord_thread    = None
        self.last_np_source     = 'default'  # source key for NP pixel display
        # NP pixel display
        self._np_frames         = []
        self._np_prev_idx       = 0
        self._np_custom         = True   # True = pixel page, False = text scroll only

        self._build_ui()
        self._load_settings()   # load after vars exist
        self.root.after(10, self._style_tabs)   # set initial tab colours
        self._build_tray()
        self.toggle_running()
        self.root.after(2000, self._check_connection)
        self.root.after(400,  self._tick_preview)
        self.root.after(1500, self._disc_autoconnect)  # auto-connect if saved creds
        # Allow <Unmap> to work only after first draw completes
        self.root.after(500, lambda: setattr(self, '_window_ready', True))

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
        tk.Label(hdr, text=f"v{APP_VERSION}", font=('Consolas',7),
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

        self.discord_enabled = tk.BooleanVar(value=False)
        _make_tab('nowplaying', '  ♪  NOW PLAYING  ', self.np_enabled)
        _make_tab('weather',    '  ⛅  WEATHER  ',    self.wx_enabled)
        _make_tab('discord',    '  🎮  DISCORD  ',    self.discord_enabled)

        self.mode_var.trace_add('write', self._style_tabs)

        _divider(r, padx=14, pady=(0,0))

        # ── Now Playing panel ──────────────────────────────────────────────────
        self.np_panel = _card(r)

        np_hdr_row = tk.Frame(self.np_panel, bg=BG2)
        np_hdr_row.pack(fill='x', pady=(0,6))
        _label(np_hdr_row, "NOW PLAYING").pack(side='left')
        # Custom pixel display toggle
        self._np_custom_var = tk.BooleanVar(value=True)
        def _on_np_custom_toggle():
            self._np_custom = self._np_custom_var.get()
            if not self._np_custom:
                # Switch to scroll page
                threading.Thread(
                    target=lambda: send_to_keyboard(
                        self.last_np[0] or '', self.last_np[1] or ''),
                    daemon=True).start()
                # Restore weather to custom page if enabled
                if (not self._discord_in_vc and
                        self._wx_frames and
                        self.wx_enabled and self.wx_enabled.get()):
                    threading.Thread(
                        target=lambda: send_pixel_animation(
                            list(self._wx_frames), fps=self._fps, priority=PRIO_WEATHER),
                        daemon=True).start()
            self._set_status(
                "NP: custom pixel display" if self._np_custom else "NP: text scroll only")
        tk.Checkbutton(np_hdr_row, text="Custom display",
                       variable=self._np_custom_var,
                       font=('Consolas',8), bg=BG2, fg=DIM,
                       selectcolor=BG3, activebackground=BG2,
                       activeforeground=FG, cursor='hand2',
                       command=_on_np_custom_toggle).pack(side='right')

        self.lbl_title = tk.Label(self.np_panel, text="—",
                                   font=('Consolas',13,'bold'), bg=BG2, fg=ACC,
                                   wraplength=420, justify='left', anchor='w')
        self.lbl_title.pack(fill='x')
        self.lbl_artist = tk.Label(self.np_panel, text="",
                                    font=('Consolas',10), bg=BG2, fg=FG,
                                    wraplength=420, justify='left', anchor='w')
        self.lbl_artist.pack(fill='x')

        # NP pixel preview
        _divider(self.np_panel, pady=(8,6))
        np_prev_hdr = tk.Frame(self.np_panel, bg=BG2)
        np_prev_hdr.pack(fill='x', pady=(0,4))
        _label(np_prev_hdr, "PREVIEW").pack(side='left')
        self.lbl_np_frame = tk.Label(np_prev_hdr, text="",
                                      font=('Consolas',8), bg=BG2, fg=DIM)
        self.lbl_np_frame.pack(side='right')
        self.np_preview = PixelPreview(self.np_panel)
        self.np_preview.pack(anchor='w')

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

        # ── Discord panel ─────────────────────────────────────────────────────
        self.disc_panel = _card(r)

        disc_title_row = tk.Frame(self.disc_panel, bg=BG2)
        disc_title_row.pack(fill='x', pady=(0,8))
        _label(disc_title_row, "DISCORD VC").pack(side='left')
        self.lbl_disc_status = tk.Label(disc_title_row, text="● Disconnected",
                                         font=('Consolas',8), bg=BG2, fg=RED)
        self.lbl_disc_status.pack(side='right')

        # Client ID row
        cid_row = tk.Frame(self.disc_panel, bg=BG2)
        cid_row.pack(fill='x', pady=(0,4))
        _label(cid_row, "Client ID:", font=('Consolas',9), fg=FG).pack(side='left')
        self.disc_cid_var = tk.StringVar(value='')
        tk.Entry(cid_row, textvariable=self.disc_cid_var,
                 font=('Consolas',9), bg=BG3, fg=FG, insertbackground=FG,
                 relief='flat', width=22, show='',
                 highlightthickness=1, highlightbackground=DIM,
                 highlightcolor=ACC).pack(side='left', padx=(6,4))
        self.btn_disc_connect = _btn(cid_row, "Connect", self._disc_connect,
                                      fg=ACC, bg='#0d1a14')
        self.btn_disc_connect.pack(side='left')

        # Client Secret (first run only)
        csec_row = tk.Frame(self.disc_panel, bg=BG2)
        csec_row.pack(fill='x', pady=(0,6))
        _label(csec_row, "Client Secret:", font=('Consolas',9), fg=FG).pack(side='left')
        self.disc_csec_var = tk.StringVar(value='')
        tk.Entry(csec_row, textvariable=self.disc_csec_var,
                 font=('Consolas',9), bg=BG3, fg=FG, insertbackground=FG,
                 relief='flat', width=22, show='*',
                 highlightthickness=1, highlightbackground=DIM,
                 highlightcolor=ACC).pack(side='left', padx=(6,4))
        _label(csec_row, "(first run only)", font=('Consolas',7)).pack(side='left')

        # Online status selector
        _divider(self.disc_panel, pady=(0,6))
        status_row = tk.Frame(self.disc_panel, bg=BG2)
        status_row.pack(fill='x', pady=(0,6))
        _label(status_row, "Online status:", font=('Consolas',9), fg=FG).pack(side='left')
        self.disc_status_var = tk.StringVar(value='online')
        for s_val, s_label, s_color in [
            ('online',    'Online',    '#43b581'),
            ('away',      'Away',      '#faa61a'),
            ('dnd',       'DnD',       '#f04747'),
            ('invisible', 'Invisible', '#747f8d'),
        ]:
            rb = tk.Radiobutton(status_row, text=s_label,
                                variable=self.disc_status_var, value=s_val,
                                font=('Consolas',8), bg=BG2, fg=s_color,
                                selectcolor=BG3, activebackground=BG2,
                                activeforeground=s_color, cursor='hand2',
                                command=self._disc_set_status)
            rb.pack(side='left', padx=(8,0))

        # When in VC, fallback display
        fallback_row = tk.Frame(self.disc_panel, bg=BG2)
        fallback_row.pack(fill='x', pady=(0,4))
        _label(fallback_row, "When not in VC show:",
               font=('Consolas',9), fg=FG).pack(side='left')
        self.disc_fallback_var = tk.StringVar(value='weather')
        for fb_val, fb_label in [('weather', 'Weather'), ('nowplaying', 'Now Playing')]:
            tk.Radiobutton(fallback_row, text=fb_label,
                           variable=self.disc_fallback_var, value=fb_val,
                           font=('Consolas',8), bg=BG2, fg=DIM,
                           selectcolor=BG3, activebackground=BG2,
                           activeforeground=FG, cursor='hand2').pack(side='left', padx=(8,0))

        # Auto-connect checkbox
        auto_row = tk.Frame(self.disc_panel, bg=BG2)
        auto_row.pack(fill='x', pady=(6,0))
        self.disc_autoconnect_var = tk.BooleanVar(value=False)
        tk.Checkbutton(auto_row, text="Auto-connect on startup",
                       variable=self.disc_autoconnect_var,
                       font=('Consolas',8), bg=BG2, fg=DIM,
                       selectcolor=BG3, activebackground=BG2,
                       activeforeground=FG, cursor='hand2').pack(side='left')

        # Info note
        _label(self.disc_panel,
               "ℹ  Mic + deafen read automatically.  "
               "Status must be set manually — Discord local IPC does not expose "
               "presence without OAuth2.",
               font=('Consolas',7), fg=DIM).pack(anchor='w', pady=(4,0))

        # ── Now Playing on Discord tab ─────────────────────────────────────
        _divider(self.disc_panel, pady=(10,6))
        _label(self.disc_panel, "NOW PLAYING").pack(anchor='w', pady=(0,4))

        self.lbl_disc_np_title = tk.Label(self.disc_panel, text="—",
                                           font=('Consolas',10,'bold'),
                                           bg='#06060f', fg=ACC,
                                           anchor='w', padx=8, pady=4,
                                           wraplength=440, justify='left')
        self.lbl_disc_np_title.pack(fill='x', pady=(0,2))
        self.lbl_disc_np_artist = tk.Label(self.disc_panel, text="",
                                            font=('Consolas',9),
                                            bg='#06060f', fg='#80ffcc',
                                            anchor='w', padx=8, pady=4,
                                            wraplength=440, justify='left')
        self.lbl_disc_np_artist.pack(fill='x')

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
        """Colour tab buttons. Weather has 4 states based on Discord status."""
        active    = self.mode_var.get()
        disc_on   = self.discord_enabled and self.discord_enabled.get()
        in_vc     = self._discord_in_vc
        wx_on     = self.wx_enabled and self.wx_enabled.get()
        np_on     = self.np_enabled and self.np_enabled.get()
        disc_en   = disc_on

        for val, btn in self._tab_btns.items():
            is_active = (val == active)
            dim_mult  = '' if is_active else '0a1f13'

            if val == 'nowplaying':
                enabled = np_on
                if not enabled:
                    btn.config(bg='#2b0a0e', fg=RED)
                elif is_active:
                    btn.config(bg='#0d3b22', fg=ACC)
                else:
                    btn.config(bg='#0a1f13', fg='#00a060')

            elif val == 'weather':
                if not wx_on:
                    btn.config(bg='#2b0a0e', fg=RED)           # red: disabled
                elif disc_en and in_vc:
                    btn.config(bg='#2b1800', fg=ORG)            # orange: discord in VC
                elif disc_en and not in_vc:
                    btn.config(bg='#2b2b00', fg='#e0e000')      # yellow: discord active, no VC
                elif is_active:
                    btn.config(bg='#0d3b22', fg=ACC)            # green: normal active
                else:
                    btn.config(bg='#0a1f13', fg='#00a060')      # dim green: normal inactive

            elif val == 'discord':
                enabled = disc_en
                if not enabled:
                    btn.config(bg='#2b0a0e', fg=RED)
                elif in_vc:
                    btn.config(bg='#0d1a2b', fg=ACC2)           # blue: in VC
                elif is_active:
                    btn.config(bg='#0d3b22', fg=ACC)
                else:
                    btn.config(bg='#0a1f13', fg='#00a060')

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
            # If we just disabled the active tab, switch to another enabled one
            if self.mode_var.get() == tab:
                for other in ['nowplaying', 'weather', 'discord']:
                    if other == tab: continue
                    other_en = {'nowplaying': self.np_enabled,
                                'weather':    self.wx_enabled,
                                'discord':    self.discord_enabled}.get(other)
                    if other_en and other_en.get():
                        self.mode_var.set(other)
                        self._on_mode_change()
                        break
            # Service-specific cleanup
            def _clear():
                if tab == 'nowplaying':
                    send_to_keyboard('', '')
                    self.last_np = (None, None)
                    self.root.after(0, self._update_np_display, '', '')
                elif tab == 'discord':
                    self._disc_disconnect()
            threading.Thread(target=_clear, daemon=True).start()

        self._style_tabs()
        labels = {'nowplaying':'Now Playing','weather':'Weather','discord':'Discord'}
        lbl = labels.get(tab, tab)
        self._set_status(f"{lbl} {'enabled' if enabled else 'disabled'}")

    def _on_mode_change(self):
        self.mode = self.mode_var.get()
        self.np_panel.pack_forget()
        self.wx_panel.pack_forget()
        self.disc_panel.pack_forget()
        if self.mode == 'nowplaying':
            self.np_panel.pack(fill='x', padx=14, pady=(0,0))
        elif self.mode == 'weather':
            self.wx_panel.pack(fill='x', padx=14, pady=(0,0))
        else:
            self.disc_panel.pack(fill='x', padx=14, pady=(0,0))
        self.root.update_idletasks()

    def _on_interval(self):
        try: self.interval = max(5, min(60, int(self.interval_var.get())))
        except ValueError: pass

    # ── Tray ──────────────────────────────────────────────────────────────────
    def _build_tray(self):
        if not pystray or not Image:
            self.tray = None
            return
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
        # Weather preview
        if self._wx_frames:
            idx = self._prev_idx % len(self._wx_frames)
            self.preview.set_frame(self._wx_frames[idx])
            self.lbl_frame.config(text=f"frame {idx+1} / {len(self._wx_frames)}")
            self._prev_idx += 1
        # NP pixel preview
        if self._np_frames and self._np_custom:
            ni = self._np_prev_idx % len(self._np_frames)
            self.np_preview.set_frame(self._np_frames[ni])
            self.lbl_np_frame.config(text=f"frame {ni+1} / {len(self._np_frames)}")
            self._np_prev_idx += 1
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
        # Start countdown at interval so weather doesn't fire on very first tick.
        # Settings may not be fully loaded yet when the thread starts.
        try:    wx_countdown = int(self.wx_int_var.get()) * 60
        except: wx_countdown = 240 * 60
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
                                title, artist, app_id = result
                                if (title, artist) != self.last_np:
                                    # Always send text scroll first
                                    send_to_keyboard(title, artist)
                                    self.last_np = (title, artist)
                                    self.root.after(0, self._update_np_display,
                                                    title, artist)
                                    # If custom NP enabled and not Discord in VC, send pixel page
                                    if (self._np_custom and _NP_MOD and
                                            not self._discord_in_vc):
                                        src    = _NP_MOD.get_source(app_id)
                                        frames = _NP_MOD.build_frames(src, True)
                                        self.last_np_source  = src
                                        self._np_frames      = frames
                                        self._np_prev_idx    = 0
                                        send_pixel_animation(frames, fps=self._fps, priority=PRIO_NP)
                                        if self.mode == 'nowplaying':
                                            self._set_status(
                                                f"Now Playing updated  [{src}]")
                                    else:
                                        if self.mode == 'nowplaying':
                                            self._set_status("Now Playing updated (text only)")
                            else:
                                if self.last_np != (None, None):
                                    send_to_keyboard('', '')
                                    self.last_np = (None, None)
                                    self._np_frames = []
                                    self.root.after(0, self._update_np_display, '', '')
                                    # NP stopped — restore weather to custom page if enabled
                                    if (self._np_custom and
                                            not self._discord_in_vc and
                                            self._wx_frames and
                                            self.wx_enabled and
                                            self.wx_enabled.get()):
                                        threading.Thread(
                                            target=lambda: send_pixel_animation(
                                                list(self._wx_frames), fps=self._fps, priority=PRIO_WEATHER),
                                            daemon=True).start()
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
                title, artist, app_id = result
                send_to_keyboard(title, artist)
                self.last_np = (title, artist)
                self.root.after(0, self._update_np_display, title, artist)
                if self._np_custom and _NP_MOD and not self._discord_in_vc:
                    src    = _NP_MOD.get_source(app_id)
                    frames = _NP_MOD.build_frames(src, True)
                    self._np_frames = frames
                    send_pixel_animation(frames, fps=self._fps, priority=PRIO_NP)
            self._do_fetch_weather()
        threading.Thread(target=_do, daemon=True).start()

    def _force_send(self):
        def _do():
            if self.mode == 'nowplaying':
                result = get_now_playing()
                if result:
                    title, artist, app_id = result
                    ok = send_to_keyboard(title, artist)
                    if ok:
                        self.last_np = (title, artist)
                        self.root.after(0, self._update_np_display, title, artist)
                        if self._np_custom and _NP_MOD and not self._discord_in_vc:
                            src    = _NP_MOD.get_source(app_id)
                            frames = _NP_MOD.build_frames(src, True)
                            self._np_frames = frames
                            send_pixel_animation(frames, fps=self._fps, priority=PRIO_NP)
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
            self._np_frames = []
            self._wx_sending = False
            switch_page_safe(PAGE_OFF)   # actually blank the keyboard display
            self.root.after(0, self._update_np_display, '', '')
            self.root.after(0, self.lbl_frame.config, {'text': ''})
        threading.Thread(target=_do, daemon=True).start()
        self._set_status("Cleared")


    # ── Now Playing display updater ───────────────────────────────────────────
    def _update_np_display(self, title, artist):
        """Update all Now Playing label widgets."""
        self.lbl_title.config(text=title or '—')
        self.lbl_artist.config(text=artist or '')
        self.lbl_kb0.config(text=sanitize(title)[:MAX_TEXT_LEN] if title else '')
        self.lbl_kb1.config(text=sanitize(artist)[:MAX_TEXT_LEN] if artist else '')
        if hasattr(self, 'lbl_wx_np_title'):
            self.lbl_wx_np_title.config(text=title or '—')
        if hasattr(self, 'lbl_wx_np_artist'):
            self.lbl_wx_np_artist.config(text=artist or '')
        if hasattr(self, 'lbl_disc_np_title'):
            self.lbl_disc_np_title.config(text=title or '—')
        if hasattr(self, 'lbl_disc_np_artist'):
            self.lbl_disc_np_artist.config(text=artist or '')
        if self.tray and title:
            self.tray.title = title[:30]

    # ── Weather fetch ─────────────────────────────────────────────────────────
    def _fetch_weather(self):
        """Button handler — launches weather fetch in background thread."""
        threading.Thread(target=self._do_fetch_weather, daemon=True).start()

    def _do_fetch_weather(self):
        """Fetch weather data, build animation frames, submit to pixel queue."""
        # Hard gate: never run if weather disabled
        if self.wx_enabled and not self.wx_enabled.get():
            return
        if self._wx_sending:
            if time.time() - getattr(self, '_wx_send_start', 0) < 120:
                self._set_status("Already sending, please wait...")
                return
            else:
                self._wx_sending = False

        self._wx_sending = True
        self._wx_send_start = time.time()
        loc = self.loc_var.get().strip() or "03275"

        try:
            # Resolve zip → city if needed
            import dp104_weather_v2 as _WX
            data = _WX.fetch_weather(loc)
        except Exception as e:
            self._wx_sending = False
            self._set_status(f"Weather fetch failed: {e}")
            return

        if not data:
            self._wx_sending = False
            self._set_status("Weather: no data returned")
            return

        # Store for debug menu
        self._last_wx_data = data

        # Debug overrides
        try:
            code = self.debug_wx_var.get()
            temp_f = int(data.get('temp', 72))
            high_f = int(data.get('high', 85))
            low_f  = int(data.get('low',  58))
            wind   = int(data.get('wind',  0))
            if self.temp_ovr_enabled.get():
                try: temp_f = int(self.temp_ovr_var.get())
                except: pass
            if hasattr(self, 'debug_wind_var'):
                wind = self.debug_wind_var.get()
            import dp104_weather_v2 as _WX2
            if code == -1:
                frames = _WX2.build_frames(data['code'], temp_f, high_f, low_f, wind)
            else:
                frames = _WX2.build_frames(code, temp_f, high_f, low_f, wind)
        except Exception as e:
            self._wx_sending = False
            self._set_status(f"Weather build failed: {e}")
            return

        self._wx_frames = frames
        self._prev_idx  = 0

        # Update the weather panel display
        self.root.after(0, self.lbl_wx_temp.config,
                        {'text': f"{data.get('temp','?')}°F"})
        self.root.after(0, self.lbl_wx_cond.config,
                        {'text': data.get('cond', '')[:32]})

        # Submit to priority queue — Discord/NP at higher priority will win
        send_pixel_animation(frames, fps=self._fps, priority=PRIO_WEATHER)

        self._wx_sending = False
        ts = time.strftime('%m/%d/%y %H:%M:%S')
        self.root.after(0, self.lbl_wx_updated.config,
                        {'text': f"Last updated: {ts}", 'fg': ACC2})
        self._set_status(f"Weather queued  ·  {data.get('temp','?')}°F  "
                         f"{data.get('cond','')[:28]}")
        _toast_weather(data)

    # ── Settings ──────────────────────────────────────────────────────────────
    def _save_settings(self):
        """Persist settings to JSON on exit."""
        try:
            s = {
                'location':        self.loc_var.get(),
                'poll_sec':        self.interval_var.get(),
                'wx_refresh':      self.wx_int_var.get(),
                'fps':             self.fps_var.get(),
                'mode':            self.mode_var.get(),
                'np_enabled':      self.np_enabled.get()       if self.np_enabled       else True,
                'wx_enabled':      self.wx_enabled.get()       if self.wx_enabled       else True,
                'disc_enabled':    self.discord_enabled.get()  if self.discord_enabled  else False,
                'disc_client_id':  self.disc_cid_var.get()     if hasattr(self,'disc_cid_var')        else '',
                'disc_status':     self.disc_status_var.get()  if hasattr(self,'disc_status_var')     else 'online',
                'disc_fallback':   self.disc_fallback_var.get() if hasattr(self,'disc_fallback_var')  else 'weather',
                'disc_autoconnect': self.disc_autoconnect_var.get() if hasattr(self,'disc_autoconnect_var') else False,
                'np_custom':       self._np_custom_var.get()   if hasattr(self,'_np_custom_var')      else True,
            }
            cfg = Path(__file__).parent / 'dp104_settings.json'
            cfg.write_text(json.dumps(s, indent=2))
        except Exception:
            pass

    def _load_settings(self):
        """Load settings from JSON if it exists."""
        try:
            cfg = Path(__file__).parent / 'dp104_settings.json'
            if not cfg.exists():
                return
            s = json.loads(cfg.read_text())
            if 'location'    in s: self.loc_var.set(s['location'])
            if 'poll_sec'    in s: self.interval_var.set(s['poll_sec'])
            if 'wx_refresh'  in s: self.wx_int_var.set(s['wx_refresh'])
            if 'fps'         in s: self.fps_var.set(s['fps']); self._fps = int(s['fps'])
            if 'mode' in s:
                self.mode_var.set(s['mode'])
                self.root.after(50, self._on_mode_change)
                self.root.after(60, self._style_tabs)
            if 'np_enabled'  in s and self.np_enabled:
                self.np_enabled.set(bool(s['np_enabled']))
            if 'wx_enabled'  in s and self.wx_enabled:
                self.wx_enabled.set(bool(s['wx_enabled']))
            if 'disc_enabled' in s and self.discord_enabled:
                self.discord_enabled.set(bool(s['disc_enabled']))
            if 'disc_client_id' in s and hasattr(self,'disc_cid_var'):
                self.disc_cid_var.set(s['disc_client_id'])
            if 'disc_status' in s and hasattr(self,'disc_status_var'):
                self.disc_status_var.set(s['disc_status'])
            if 'disc_fallback' in s and hasattr(self,'disc_fallback_var'):
                self.disc_fallback_var.set(s['disc_fallback'])
            if 'disc_autoconnect' in s and hasattr(self,'disc_autoconnect_var'):
                self.disc_autoconnect_var.set(bool(s['disc_autoconnect']))
            if 'np_custom' in s and hasattr(self,'_np_custom_var'):
                self._np_custom_var.set(bool(s['np_custom']))
                self._np_custom = bool(s['np_custom'])
            self.root.after(10, self._style_tabs)
        except Exception:
            pass

    # ── Status bar ────────────────────────────────────────────────────────────
    _last_disc_ts = ""
    _last_wx_ts   = ""
    _last_np_ts   = ""

    def _set_status(self, msg):
        ts = time.strftime('%H:%M:%S')
        if msg.startswith("Discord:") or msg.startswith("Discord "):
            DP104App._last_disc_ts = ts
        elif "Weather" in msg or "weather" in msg or "wx" in msg.lower():
            DP104App._last_wx_ts = ts
        elif "Now Playing" in msg or "NP " in msg:
            DP104App._last_np_ts = ts
        self.root.after(0, self._refresh_status_bar, ts, msg)

    def _refresh_status_bar(self, ts, msg):
        parts = [f"{ts}  {msg}"]
        if DP104App._last_wx_ts:
            parts.append(f"wx:{DP104App._last_wx_ts}")
        if DP104App._last_np_ts:
            parts.append(f"np:{DP104App._last_np_ts}")
        if DP104App._last_disc_ts:
            parts.append(f"disc:{DP104App._last_disc_ts}")
        try:
            self.lbl_status.config(text="  ·  ".join(parts))
        except Exception:
            pass

    # ── Discord methods ───────────────────────────────────────────────────────
    def _check_skin_folder(self):
        """Warn if skins/default missing or incomplete."""
        skin_dir = Path(__file__).parent / 'skins' / 'default'
        if not skin_dir.exists():
            self.lbl_skin_warn.config(
                text=f"⚠ Skin folder not found: {skin_dir}\n"
                     "Create skins\\default\\ and add the 12 PNG files "
                     "(ggg.png … rrr.png) to use Discord VC display.")
            return
        pngs = list(skin_dir.glob('*.png'))
        if len(pngs) < 12:
            self.lbl_skin_warn.config(
                text=f"⚠ Only {len(pngs)}/12 PNG files in {skin_dir}. "
                     "Some states may fall back to default skin.")
        else:
            self.lbl_skin_warn.config(text="")

    def _disc_autoconnect(self):
        """Auto-connect Discord on startup if enabled, creds saved, token cached."""
        try:
            cid       = self.disc_cid_var.get().strip()
            disc_on   = self.discord_enabled and self.discord_enabled.get()
            auto_on   = hasattr(self,'disc_autoconnect_var') and self.disc_autoconnect_var.get()
            tok_path  = Path(__file__).parent / '.discord_token'
            if disc_on and auto_on and cid and tok_path.exists() and not self._discord_connected:
                self._set_status("Auto-connecting Discord...")
                self._disc_connect()
        except Exception:
            pass

    def _disc_connect(self):
        """Start Discord IPC using DiscordIPC directly.
        All skin sends go through _PIXEL_QUEUE — no parallel HID access."""
        if not _DISC_MOD:
            self._set_status("dp104_discord.py not found in same folder")
            return
        cid = self.disc_cid_var.get().strip()
        if not cid:
            self._set_status("Enter a Discord Client ID first")
            return
        sec = self.disc_csec_var.get().strip() or None

        skin_dir = Path(__file__).parent / 'skins' / 'default'
        try:
            self._disc_skin = _DISC_MOD.load_skin(str(skin_dir))
            print(f"[Discord GUI] Skin loaded: {len(self._disc_skin)} variants")
        except Exception as e:
            self._disc_skin = {}
            print(f"[Discord GUI] Skin load failed: {e}")
            self._set_status(f"Skin load failed: {e}")

        self._discord_connected = True
        self.root.after(0, self.lbl_disc_status.config,
                        {'text': '● Connecting...', 'fg': ORG})
        self.root.after(0, self.btn_disc_connect.config,
                        {'text': 'Disconnect', 'command': self._disc_disconnect})

        def _on_ipc_ready():
            self.root.after(0, self.lbl_disc_status.config,
                            {'text': '● Connected — waiting for VC state...', 'fg': ACC})
            self.root.after(0, self._style_tabs)
            print("[Discord GUI] Auth complete — poll loop active")

        def _ipc_run():
            retry = 5.0
            while self._discord_connected:
                try:
                    print(f"[Discord GUI] Creating DiscordIPC (cid={cid[:8]}...)")
                    self.root.after(0, self.lbl_disc_status.config,
                                    {'text': '● Authenticating... (may take 15-20s)',
                                     'fg': ORG})
                    # After 30s still authenticating → show it's connected but verifying
                    def _auth_timeout():
                        try:
                            cur = self.lbl_disc_status.cget('text')
                            if 'Authenticating' in cur and self._discord_connected:
                                self.lbl_disc_status.config(
                                    text='● Connected (verifying...)', fg=ACC)
                        except Exception: pass
                    self.root.after(30000, _auth_timeout)
                    ipc = _DISC_MOD.DiscordIPC(
                        client_id=cid,
                        client_secret=sec,
                        on_state_change=self._disc_on_state,
                        on_ready=_on_ipc_ready,
                        on_vc_leave=self._disc_on_vc_leave,
                    )
                    self._discord_ipc = ipc
                    print("[Discord GUI] IPC run() starting (auth may take 15-20s)...")
                    ipc.run()
                    print("[Discord GUI] IPC run() exited")
                except Exception as e:
                    print(f"[Discord GUI] IPC error: {e}")
                    self.root.after(0, self.lbl_disc_status.config,
                                    {'text': f'● {str(e)[:45]}', 'fg': RED})
                if self._discord_connected:
                    self.root.after(0, self.lbl_disc_status.config,
                                    {'text': '● Reconnecting...', 'fg': ORG})
                    time.sleep(retry)

        self._discord_thread = threading.Thread(target=_ipc_run, daemon=True)
        self._discord_thread.start()

    def _disc_disconnect(self):
        """Stop Discord IPC."""
        self._discord_connected = False
        ipc = getattr(self, '_discord_ipc', None)
        if ipc:
            try: ipc.stop()
            except: pass
            self._discord_ipc = None
        self._discord_in_vc = False
        self.root.after(0, self.lbl_disc_status.config,
                        {'text': '● Disconnected', 'fg': RED})
        self.root.after(0, self.btn_disc_connect.config,
                        {'text': 'Connect', 'command': self._disc_connect})
        self._style_tabs()

    def _disc_set_status(self):
        """Update online status from selector."""
        status = self.disc_status_var.get()
        ipc = getattr(self, '_discord_ipc', None)
        if ipc:
            ipc.set_status(status)
        self._set_status(f"Discord status → {status}")

    def _disc_on_state(self, mic_muted, status, deafened):
        """Called by DiscordIPC on mic/deafen/status change. Queues skin frame."""
        self._discord_in_vc = True
        self.root.after(0, self._style_tabs)
        skin = getattr(self, '_disc_skin', {})
        if not skin:
            print("[Discord GUI] No skin loaded — skipping send")
            return
        key   = _DISC_MOD.state_to_key(mic_muted, status, deafened)
        frame = skin.get(key) or skin.get('ggg')
        if not frame:
            print(f"[Discord GUI] No frame for key '{key}' — skipping")
            return
        print(f"[Discord GUI] State → key={key}  queuing PRIO_DISCORD send")
        self.root.after(0, self._update_disc_preview, frame, key,
                        mic_muted, status, deafened)
        send_pixel_animation([frame], fps=self._fps, priority=PRIO_DISCORD)
        self._set_status(
            f"Discord: mic={'muted' if mic_muted else 'live'} "
            f"deaf={'yes' if deafened else 'no'} status={status}")

    def _update_disc_preview(self, frame, key, mic_muted, status, deafened):
        """Update Discord tab preview widget."""
        try:
            if hasattr(self, 'disc_preview'):
                self.disc_preview.set_frame(frame)
            if hasattr(self, 'lbl_disc_state'):
                mic_s  = "🔴 muted" if mic_muted else "🟢 live"
                deaf_s = "🔇 deaf"  if deafened  else "🔊 hearing"
                st_s   = {'online':'🟢','away':'🟡','dnd':'🔴',
                          'invisible':'⚫'}.get(status,'●') + ' ' + status
                self.lbl_disc_state.config(
                    text=f"{mic_s}  {deaf_s}  {st_s}  [{key}]")
        except Exception:
            pass

    def _disc_on_vc_leave(self):
        """Called when user leaves VC — revert to fallback display."""
        self._discord_in_vc = False
        self.root.after(0, self._style_tabs)
        fb = self.disc_fallback_var.get() if hasattr(self,'disc_fallback_var') else 'weather'
        if fb == 'weather' and self.wx_enabled and self.wx_enabled.get():
            threading.Thread(target=self._do_fetch_weather, daemon=True).start()
        elif fb == 'nowplaying' and self._np_frames:
            send_pixel_animation(list(self._np_frames), fps=self._fps, priority=PRIO_NP)

    # ── Temperature override ──────────────────────────────────────────────────
    def _on_temp_override_toggle(self):
        enabled = self.temp_ovr_enabled.get()
        state = 'normal' if enabled else 'disabled'
        if hasattr(self, 'temp_ovr_entry'):
            self.temp_ovr_entry.config(state=state)

    # ── Debug preview helpers ─────────────────────────────────────────────────
    def _apply_debug_preview(self):
        """Rebuild preview frames using debug overrides."""
        code = self.debug_wx_var.get()
        if code == -1:
            return
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
            if self.temp_ovr_enabled.get():
                try: temp_f = int(self.temp_ovr_var.get())
                except: pass
            wind = self.debug_wind_var.get()
            import dp104_weather_v2 as _WX
            frames = _WX.build_frames(code, temp_f, high_f, low_f, wind)
            self._wx_frames  = frames
            self._prev_idx   = 0
            n = len(frames)
            fps = self._fps
            self.root.after(0, self.lbl_frame.config,
                            {'text': f"frame 1 / {n}"})
        except Exception as e:
            self._set_status(f"Debug preview error: {e}")

    def _send_debug_animation(self):
        """Send the currently previewed debug weather animation to keyboard."""
        if not self._wx_frames:
            self._set_status("No frames to send — pick an animation first")
            return
        frames = list(self._wx_frames)
        fps    = self._fps
        threading.Thread(
            target=lambda: send_pixel_animation(frames, fps=fps, priority=PRIO_WEATHER),
            daemon=True).start()
        self._set_status(f"Debug animation sent ({len(frames)} frames)")

    # ── Debug window ──────────────────────────────────────────────────────────
    def _open_debug(self):
        """Open the debug panel (tilde key)."""
        if self._debug_win and tk.Toplevel.winfo_exists(self._debug_win):
            self._debug_win.lift(); self._debug_win.focus_force(); return

        win = tk.Toplevel(self.root)
        win.title(f"Debug  —  DP-104  v{APP_VERSION}")
        win.configure(bg=BG)
        win.resizable(False, False)
        win.transient(self.root)
        win.bind("<grave>", lambda e: win.destroy())
        win.bind("<F1>",    lambda e: self._open_credits())
        self._debug_win = win

        def _reposition(*_):
            if not tk.Toplevel.winfo_exists(win): return
            self.root.update_idletasks()
            rx = self.root.winfo_x() + self.root.winfo_width() + 8
            ry = self.root.winfo_y()
            win.geometry(f"+{rx}+{ry}")
        _reposition()
        self.root.bind("<Configure>", _reposition, add='+')
        win.protocol("WM_DELETE_WINDOW",
                     lambda: [self.root.unbind("<Configure>"), win.destroy()])

        tk.Label(win, text="  ⚙  DEBUG MENU",
                 font=('Consolas',11,'bold'), bg=BG, fg=ORG,
                 pady=10, padx=14).pack(anchor='w')
        tk.Frame(win, bg=SEP, height=1).pack(fill='x', padx=14)

        # ── Tabs ──────────────────────────────────────────────────────────────
        nb = ttk.Notebook(win)
        nb.pack(fill='both', expand=True, padx=14, pady=10)

        # ── Weather tab ────────────────────────────────────────────────────────
        wx_tab = tk.Frame(nb, bg=BG2); nb.add(wx_tab, text=" ⛅ Weather ")

        _label(wx_tab, "FORCE ANIMATION", fg=ORG).pack(anchor='w', pady=(8,4), padx=10)
        WEATHER_NAMES = [(0,"0 — Sunny"),(1,"1 — Partly Cloudy"),(2,"2 — Cloudy (House)"),
                         (3,"3 — Rainy"),(4,"4 — Snowy"),(5,"5 — Thunderstorm"),
                         (6,"6 — Night Clear"),(7,"7 — Night Partly Cloudy")]
        self.debug_wx_var.set(-1)
        f_wx = tk.Frame(wx_tab, bg=BG2); f_wx.pack(fill='x', padx=10)
        tk.Radiobutton(f_wx, text="—  Live (weather API)",
                       variable=self.debug_wx_var, value=-1,
                       font=('Consolas',9), fg=FG, bg=BG2, selectcolor=BG3,
                       activebackground=BG2, activeforeground=ACC,
                       indicatoron=0, width=26, pady=3, relief='flat',
                       cursor='hand2', command=self._apply_debug_preview).pack(fill='x', pady=1)
        for code, lbl in WEATHER_NAMES:
            tk.Radiobutton(f_wx, text=lbl, variable=self.debug_wx_var, value=code,
                           font=('Consolas',9), fg=FG, bg=BG2, selectcolor=BG3,
                           activebackground=BG2, activeforeground=ACC,
                           indicatoron=0, width=26, pady=3, relief='flat',
                           cursor='hand2', command=self._apply_debug_preview).pack(fill='x', pady=1)
        _btn(f_wx, "▶  SEND TO KEYBOARD", self._send_debug_animation,
             fg=BG, bg=ORG).pack(anchor='w', pady=(8,0))

        tk.Frame(wx_tab, bg=SEP, height=1).pack(fill='x', padx=10, pady=(10,6))
        _label(wx_tab, "WIND SPEED OVERRIDE (mph)", fg=ORG).pack(anchor='w', padx=10, pady=(0,4))
        wind_row = tk.Frame(wx_tab, bg=BG2); wind_row.pack(anchor='w', padx=10)
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

        tk.Frame(wx_tab, bg=SEP, height=1).pack(fill='x', padx=10, pady=(10,6))
        _label(wx_tab, "TEMPERATURE OVERRIDE", fg=ORG).pack(anchor='w', padx=10, pady=(0,4))
        tmp_row = tk.Frame(wx_tab, bg=BG2); tmp_row.pack(anchor='w', padx=10)
        self.temp_ovr_enabled.set(False)
        tk.Checkbutton(tmp_row, text="Enable override:",
                       variable=self.temp_ovr_enabled,
                       font=('Consolas',9), fg=FG, bg=BG2, selectcolor=BG3,
                       activebackground=BG2, activeforeground=FG,
                       cursor='hand2',
                       command=self._on_temp_override_toggle).pack(side='left')
        self.temp_ovr_entry = tk.Entry(tmp_row, textvariable=self.temp_ovr_var,
                                        width=6, font=('Consolas',10),
                                        bg=BG3, fg=FG, insertbackground=FG,
                                        relief='flat', state='disabled',
                                        highlightthickness=1,
                                        highlightbackground=DIM,
                                        highlightcolor=ACC)
        self.temp_ovr_entry.pack(side='left', padx=(8,4))
        _label(tmp_row, "°F", fg=FG, font=('Consolas',9)).pack(side='left')

        # ── Now Playing tab ────────────────────────────────────────────────────
        np_tab = tk.Frame(nb, bg=BG2); nb.add(np_tab, text=" ♪ Now Playing ")

        _label(np_tab, "SOURCE ICON", fg=ORG).pack(anchor='w', pady=(8,4), padx=10)
        NP_SOURCES = ['default','spotify','youtube','youtubemusic','twitch','winamp',
                      'foobar2000','tidal','applemusic','amazonmusic','vlc',
                      'soundcloud','pandora','deezer','browser']
        self._debug_np_src = tk.StringVar(value='default')
        self._debug_np_playing = tk.BooleanVar(value=True)
        self._debug_np_eq = tk.BooleanVar(value=True)

        src_row = tk.Frame(np_tab, bg=BG2); src_row.pack(fill='x', padx=10, pady=(0,4))
        _label(src_row, "Source:", fg=FG, font=('Consolas',9)).pack(side='left')
        src_dd = ttk.Combobox(src_row, textvariable=self._debug_np_src,
                               values=NP_SOURCES, state='readonly',
                               font=('Consolas',9), width=18)
        src_dd.pack(side='left', padx=(6,0))
        src_dd.bind('<<ComboboxSelected>>', lambda e: self._debug_send_np())

        pp_row = tk.Frame(np_tab, bg=BG2); pp_row.pack(fill='x', padx=10, pady=(0,4))
        tk.Checkbutton(pp_row, text="Playing (▶) / Paused (⏸)",
                       variable=self._debug_np_playing,
                       font=('Consolas',9), fg=FG, bg=BG2, selectcolor=BG3,
                       activebackground=BG2, activeforeground=FG,
                       cursor='hand2',
                       command=self._debug_send_np).pack(side='left')

        eq_row = tk.Frame(np_tab, bg=BG2); eq_row.pack(fill='x', padx=10, pady=(0,8))
        tk.Checkbutton(eq_row, text="EQ bars enabled",
                       variable=self._debug_np_eq,
                       font=('Consolas',9), fg=FG, bg=BG2, selectcolor=BG3,
                       activebackground=BG2, activeforeground=FG,
                       cursor='hand2',
                       command=self._debug_send_np).pack(side='left')

        _btn(np_tab, "▶  SEND TO KEYBOARD", self._debug_send_np,
             fg=BG, bg=ORG).pack(anchor='w', padx=10, pady=(0,8))

        # ── Discord tab ────────────────────────────────────────────────────────
        disc_tab = tk.Frame(nb, bg=BG2); nb.add(disc_tab, text=" 🎮 Discord ")

        _label(disc_tab, "SKIN COMBINATION", fg=ORG).pack(anchor='w', pady=(8,4), padx=10)
        DISC_KEYS = ['ggg','ggr','grg','grr','gyg','gyr',
                     'rgg','rgr','rrg','rrr','ryg','ryr',
                     'gig','gir','rig','rir']
        KEY_LABELS = {
            'ggg':'mic=on  status=online  deaf=off',
            'ggr':'mic=on  status=online  deaf=ON',
            'grg':'mic=on  status=dnd    deaf=off',
            'grr':'mic=on  status=dnd    deaf=ON',
            'gyg':'mic=on  status=away   deaf=off',
            'gyr':'mic=on  status=away   deaf=ON',
            'rgg':'mic=MUTED  status=online  deaf=off',
            'rgr':'mic=MUTED  status=online  deaf=ON',
            'rrg':'mic=MUTED  status=dnd    deaf=off',
            'rrr':'mic=MUTED  status=dnd    deaf=ON',
            'ryg':'mic=MUTED  status=away   deaf=off',
            'ryr':'mic=MUTED  status=away   deaf=ON',
            'gig':'mic=on  status=invisible  deaf=off',
            'gir':'mic=on  status=invisible  deaf=ON',
            'rig':'mic=MUTED  status=invisible  deaf=off',
            'rir':'mic=MUTED  status=invisible  deaf=ON',
        }
        self._debug_disc_key = tk.StringVar(value='ggg')

        key_row = tk.Frame(disc_tab, bg=BG2); key_row.pack(fill='x', padx=10, pady=(0,4))
        _label(key_row, "Key:", fg=FG, font=('Consolas',9)).pack(side='left')
        key_dd = ttk.Combobox(key_row, textvariable=self._debug_disc_key,
                               values=[f"{k} — {KEY_LABELS[k]}" for k in DISC_KEYS],
                               state='readonly', font=('Consolas',8), width=36)
        key_dd.pack(side='left', padx=(6,0))
        key_dd.bind('<<ComboboxSelected>>', lambda e: self._debug_preview_disc())

        _label(disc_tab,
               "Preview updates the Discord tab preview.\nSend pushes to keyboard.",
               font=('Consolas',8), fg=DIM).pack(anchor='w', padx=10)

        btn_row = tk.Frame(disc_tab, bg=BG2); btn_row.pack(anchor='w', padx=10, pady=(6,0))
        _btn(btn_row, "👁  PREVIEW", self._debug_preview_disc,
             fg=FG, bg=BG3).pack(side='left', padx=(0,6))
        _btn(btn_row, "▶  SEND TO KEYBOARD", self._debug_send_disc,
             fg=BG, bg=ORG).pack(side='left')

        # ── Footer ─────────────────────────────────────────────────────────────
        tk.Frame(win, bg=SEP, height=1).pack(fill='x', padx=14)
        footer = tk.Frame(win, bg=BG, padx=14, pady=6)
        footer.pack(fill='x')
        tk.Label(footer, text="~  close   F1  credits",
                 font=('Consolas',7), bg=BG, fg=DIM).pack(side='left')

    def _debug_send_np(self):
        """Send NP debug frame to keyboard."""
        if not _NP_MOD: return
        src     = self._debug_np_src.get()
        playing = self._debug_np_playing.get()
        frames  = _NP_MOD.build_frames(src, playing)
        send_pixel_animation(frames, fps=self._fps, priority=PRIO_NP)
        self._set_status(f"Debug NP sent: {src} {'playing' if playing else 'paused'}")

    def _debug_preview_disc(self):
        """Preview Discord skin key in the Discord tab preview."""
        skin = getattr(self, '_disc_skin', {})
        raw  = self._debug_disc_key.get()
        key  = raw.split(' — ')[0].strip()
        if not skin:
            # Try loading skin now
            skin_dir = Path(__file__).parent / 'skins' / 'default'
            try:
                self._disc_skin = _DISC_MOD.load_skin(str(skin_dir)) if _DISC_MOD else {}
                skin = self._disc_skin
            except Exception: pass
        frame = skin.get(key) if skin else None
        if frame:
            self.root.after(0, self._update_disc_preview, frame, key, False, 'online', False)
            self._set_status(f"Discord preview: {key}")
        else:
            self._set_status(f"No skin frame for '{key}' — connect Discord first to load skin")

    def _debug_send_disc(self):
        """Send Discord skin frame to keyboard."""
        skin = getattr(self, '_disc_skin', {})
        raw  = self._debug_disc_key.get()
        key  = raw.split(' — ')[0].strip()
        frame = skin.get(key) if skin else None
        if frame:
            send_pixel_animation([frame], fps=self._fps, priority=PRIO_DISCORD)
            self._set_status(f"Discord skin sent: {key}")
        else:
            self._set_status(f"No skin frame for '{key}' — skin not loaded")

    # ── Credits ───────────────────────────────────────────────────────────────
    def _open_credits(self):
        """F1 — Credits screen."""
        win = tk.Toplevel(self.root)
        win.title("Credits")
        win.configure(bg=BG)
        win.resizable(False, False)
        win.transient(self.root)
        win.bind("<F1>",    lambda e: win.destroy())
        win.bind("<grave>", lambda e: win.destroy())

        self.root.update_idletasks()
        cx = self.root.winfo_x() + self.root.winfo_width()  // 2
        cy = self.root.winfo_y() + self.root.winfo_height() // 2
        win.update_idletasks()
        win.geometry(f"+{cx - 180}+{cy - 120}")

        tk.Label(win, text="DP-104 DISPLAY CONTROLLER",
                 font=('Consolas',11,'bold'), bg=BG, fg=ACC,
                 pady=18, padx=30).pack()

        for name, role, col in [
            ("Claude",  "Big Guy",   ACC2),
            ("Mikan",   "Human Guy", ACC),
            ("remedy",  "Artist Gal", ORG),
        ]:
            row = tk.Frame(win, bg=BG, pady=4)
            row.pack()
            tk.Label(row, text=name, font=('Consolas',10,'bold'),
                     bg=BG, fg=col, width=14, anchor='e').pack(side='left')
            tk.Label(row, text=" — ", font=('Consolas',10),
                     bg=BG, fg=DIM).pack(side='left')
            tk.Label(row, text=role, font=('Consolas',10),
                     bg=BG, fg=FG, width=14, anchor='w').pack(side='left')

        tk.Label(win, text=f"2026  ·  v{APP_VERSION}",
                 font=('Consolas',8), bg=BG, fg=DIM).pack(pady=(12,18))

    # ── Quit / lifecycle ──────────────────────────────────────────────────────
    def _on_minimize(self, event=None):
        self.root.withdraw()
        if self.tray:
            self.tray.visible = True


    def _on_close(self):
        self._save_settings()
        self._disc_disconnect()
        self.running = False
        if self.tray:
            try: self.tray.stop()
            except: pass
        self.root.destroy()

    def _quit(self):
        self._on_close()

    def run(self):
        self.root.mainloop()


APP_VERSION = "1.2.5"


if __name__ == '__main__':
    try:
        app = DP104App()
    except Exception as _e:
        import traceback as _tb
        _msg = _tb.format_exc()
        print(_msg, file=sys.stderr)
        try:
            import tkinter as _tk
            import tkinter.messagebox as _mb
            _r = _tk.Tk(); _r.withdraw()
            _mb.showerror("DP-104 Startup Error", _msg[:1200])
            _r.destroy()
        except Exception:
            pass
        sys.exit(1)
    app.run()
