"""
dp104_discord.py  —  Discord VC status display for DP-104
Reads mic/deafen/online state via Discord local IPC (no bot token required).
Sends skin PNG frames to keyboard via proven HID pixel protocol.

Skin folder structure:
  skins/default/
    ggg.png  (mic=unmuted, status=online,  deaf=undeafened)
    ggr.png  (mic=unmuted, status=online,  deaf=deafened)
    grg.png  (mic=unmuted, status=dnd,     deaf=undeafened)
    grr.png  (mic=unmuted, status=dnd,     deaf=deafened)
    gyg.png  (mic=unmuted, status=away,    deaf=undeafened)
    gyr.png  (mic=unmuted, status=away,    deaf=deafened)
    rgg.png  (mic=muted,   status=online,  deaf=undeafened)
    rgr.png  (mic=muted,   status=online,  deaf=deafened)
    rrg.png  (mic=muted,   status=dnd,     deaf=undeafened)
    rrr.png  (mic=muted,   status=dnd,     deaf=deafened)
    ryg.png  (mic=muted,   status=away,    deaf=undeafened)
    ryr.png  (mic=muted,   status=away,    deaf=deafened)
    # Invisible is auto-generated from online variants (grey status box)

Filename letter scheme:
  Letter 1: Mic       G=unmuted  R=muted
  Letter 2: Status    G=online   Y=away   R=dnd   I=invisible(generated)
  Letter 3: Deafen    G=undeaf   R=deafened

Requirements:
  pip install hidapi pillow
  A Discord Developer Application client_id (free at discord.com/developers)
"""

import json, struct, time, threading, os, sys
import colorsys, importlib
from pathlib import Path

# ── HID constants (same as weather module) ────────────────────────────────────
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
ROWS, COLS       = 8, 24
FRAME_BYTES      = ROWS * COLS * 3

# ── Status code mapping ───────────────────────────────────────────────────────
# Letter 2 (status): maps Discord status string → skin letter
STATUS_MAP = {
    'online':    'g',
    'away':      'y',
    'idle':      'y',   # Discord uses "idle" for away
    'dnd':       'r',
    'invisible': 'i',
    'offline':   'i',   # treat offline same as invisible
}

def state_to_key(mic_muted, status, deafened):
    """Convert current Discord state to a skin frame key."""
    m = 'r' if mic_muted  else 'g'
    s = STATUS_MAP.get(status, 'g')
    d = 'r' if deafened   else 'g'
    return f"{m}{s}{d}"

# ── Skin loader ───────────────────────────────────────────────────────────────
def rgb_to_hsv256(r, g, b):
    h, s, v = colorsys.rgb_to_hsv(r/255, g/255, b/255)
    return int(h*255), int(s*255), int(v*255)

def _gen_invisible(frame_green):
    """Auto-generate invisible variant: grey out the status box (cols 10-14)."""
    frame = list(frame_green)
    for row in range(8):
        for col in range(10, 15):
            idx = (row * 24 + col) * 3
            h, s, v = frame[idx], frame[idx+1], frame[idx+2]
            if v > 10 and s > 30:
                frame[idx]   = 0
                frame[idx+1] = 0
                frame[idx+2] = max(10, v // 2)
    return frame

def load_skin(skin_dir):
    """
    Load all skin PNGs from skin_dir and return a dict of key → HSV flat list.
    Invisible variants are auto-generated if not present.
    """
    try:
        from PIL import Image
    except ImportError:
        raise RuntimeError("pip install pillow")

    skin_dir = Path(skin_dir)
    frames = {}

    CODES = ['ggg','ggr','grg','grr','gyg','gyr',
             'rgg','rgr','rrg','rrr','ryg','ryr']

    for code in CODES:
        # Support both 'ggg.png' and timestamped names containing the code
        candidates = list(skin_dir.glob(f'*{code}*.png')) + \
                     list(skin_dir.glob(f'{code}.png'))
        if not candidates:
            raise FileNotFoundError(f"Skin file for '{code}' not found in {skin_dir}")

        path = candidates[0]
        img  = Image.open(path).convert('RGB')
        if img.size != (COLS, ROWS):
            img = img.resize((COLS, ROWS), Image.NEAREST)

        flat = []
        for row in range(ROWS):
            for col in range(COLS):
                h, s, v = rgb_to_hsv256(*img.getpixel((col, row)))
                # Snap all dim bleed pixels to black.
                # Only the core mic icon area (cols 5-8, rows 1-4) gets
                # safe grey (0,0,20) to avoid the firmware red-rendering bug.
                # Pure black stays black.
                # Low-val bleed pixels → safe grey.
                if 0 < v < 40:
                    h, s, v = 0, 0, 20
                # Near-zero hue with low sat renders red on firmware.
                # RGB(63,60,60) → HSV(0,12,63) is the separator colour — snap to grey.
                elif (h < 5 or h > 250) and s < 40:
                    h, s = 0, 0   # strip the hue, keep val as-is → pure grey
                flat.extend([h, s, v])
        frames[code] = flat

    # Auto-generate invisible variants
    for mic in ('g', 'r'):
        green_key = f"{mic}g{'g'}"   # e.g. ggg or rgg (online + undeafened)
        inv_g     = f"{mic}ig"        # gig or rig
        inv_r     = f"{mic}ir"        # gir or rir

        # Build invisible-undeafened
        src_key_g = f"{mic}gg"
        frames[inv_g] = _gen_invisible(frames.get(f"{mic}gg", frames[f"{mic}gg"]))
        frames[inv_r] = _gen_invisible(frames.get(f"{mic}gr", frames[f"{mic}gr"]))

    return frames

# ── HID send ──────────────────────────────────────────────────────────────────
def _num_into_bytes(n):
    return [(n>>24)&0xFF, (n>>16)&0xFF, (n>>8)&0xFF, n&0xFF]

def _open_device():
    if not _hid:
        raise RuntimeError("HID library not available")
    dev = _hid.device()
    try:
        dev.open_path(DP104_PIXEL_PATH)
        dev.set_nonblocking(False)
        return dev
    except Exception:
        pass
    for info in _hid.enumerate():
        if info['usage_page'] == RAW_USAGE_PAGE:
            try:
                dev = _hid.device()
                dev.open_path(info['path'])
                dev.set_nonblocking(False)
                return dev
            except Exception:
                continue
    raise RuntimeError("DP-104 not found")

def send_frame(flat_hsv, fps=10, retries=3):
    """Send a single static frame (repeated N times for stability) to keyboard."""
    # Send as a 1-frame animation — the keyboard loops it = static display
    frames = [flat_hsv]
    n = len(frames)
    last_err = "unknown"

    for attempt in range(1, retries + 1):
        try:
            dev = _open_device()
            hdr = [0xd1, 0x30, n, fps, ROWS, COLS] + [0]*26
            dev.write([0x00] + hdr)
            resp = dev.read(32, timeout_ms=2000)
            if not resp or resp[0] != 0xd1:
                raise RuntimeError(f"Bad ACK: {list(resp[:4]) if resp else 'timeout'}")
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

            dev.close()
            return True
        except Exception as e:
            last_err = str(e)
            try: dev.close()
            except: pass
            if attempt < retries:
                time.sleep(2.0)

    raise RuntimeError(f"Send failed after {retries} attempts: {last_err}")

# ── Discord IPC client ────────────────────────────────────────────────────────
OP_HANDSHAKE = 0
OP_FRAME     = 1
OP_CLOSE     = 2

class DiscordIPC:
    """
    Minimal Discord local IPC client.
    Reads mic/deafen via GET_VOICE_SETTINGS + VOICE_SETTINGS_UPDATE subscription.
    Online status must be set externally (no reliable way to read without OAuth2).
    """

    def __init__(self, client_id, client_secret=None,
                 token_cache='.discord_token',
                 on_state_change=None, on_ready=None,
                 on_vc_leave=None, on_vc_join=None):
        self.client_id       = client_id
        self.client_secret   = client_secret
        self.token_cache     = token_cache
        self.on_state_change = on_state_change
        self.on_ready        = on_ready
        self.on_vc_leave     = on_vc_leave
        self.on_vc_join      = on_vc_join
        self._pipe_handle    = None
        self._k32            = None
        self._running        = False
        self._nonce          = 0

        # Current state
        self.mic_muted        = False
        self.deafened         = False
        self.status           = 'online'
        self._ever_received   = False
        self._in_vc           = False      # True when in a voice channel
        self._idle_threshold  = 300
        self._idle_monitoring = True

    # ── Pipe I/O (Windows named pipe via ctypes) ──────────────────────────────
    def _connect_pipe(self):
        import ctypes, ctypes.wintypes as wt
        k32 = ctypes.windll.kernel32

        GENERIC_READ  = 0x80000000
        GENERIC_WRITE = 0x40000000
        OPEN_EXISTING = 3
        # Switch pipe to byte-stream mode after opening
        PIPE_READMODE_BYTE = 0x0
        PIPE_WAIT          = 0x0

        for i in range(10):
            path = f'\\\\.\\pipe\\discord-ipc-{i}'
            handle = k32.CreateFileW(
                path, GENERIC_READ | GENERIC_WRITE,
                0, None, OPEN_EXISTING, 0, None
            )
            if handle not in (0, 0xFFFFFFFF, ctypes.c_void_p(-1).value):
                # Switch to byte-stream mode so we can read arbitrary lengths
                mode = wt.DWORD(PIPE_READMODE_BYTE | PIPE_WAIT)
                k32.SetNamedPipeHandleState(handle, ctypes.byref(mode), None, None)
                self._pipe_handle = handle
                self._k32 = k32
                return

        raise RuntimeError("Discord IPC pipe not found — is Discord running?")

    def _send(self, op, data):
        import ctypes, ctypes.wintypes as wt
        payload = json.dumps(data).encode('utf-8')
        header  = struct.pack('<II', op, len(payload))
        buf     = header + payload
        written = wt.DWORD(0)
        self._k32.WriteFile(
            self._pipe_handle, buf, len(buf), ctypes.byref(written), None)

    def _read_exact(self, n):
        """Read exactly n bytes from the pipe handle."""
        import ctypes, ctypes.wintypes as wt
        result = b''
        while len(result) < n:
            needed = n - len(result)
            buf    = ctypes.create_string_buffer(needed)
            nread  = wt.DWORD(0)
            ok = self._k32.ReadFile(
                self._pipe_handle, buf, needed, ctypes.byref(nread), None)
            if not ok and nread.value == 0:
                raise RuntimeError("Pipe closed or read error")
            result += buf.raw[:nread.value]
        return result

    def _recv(self):
        header = self._read_exact(8)
        op, length = struct.unpack('<II', header)
        payload = self._read_exact(length)
        return op, json.loads(payload)

    def _next_nonce(self):
        self._nonce += 1
        return str(self._nonce)

    # ── Commands ──────────────────────────────────────────────────────────────
    def _cmd(self, cmd, args=None):
        nonce = self._next_nonce()
        msg   = {"cmd": cmd, "nonce": nonce}
        if args:
            msg["args"] = args
        self._send(OP_FRAME, msg)
        return nonce

    def _subscribe(self, event, args=None):
        nonce = self._next_nonce()
        msg   = {"cmd": "SUBSCRIBE", "evt": event, "nonce": nonce}
        if args:
            msg["args"] = args
        self._send(OP_FRAME, msg)

    # ── State handling ────────────────────────────────────────────────────────
    def _apply_voice_settings(self, data):
        mute = data.get('mute')
        deaf = data.get('deaf')
        # Only update fields that are present in the response
        if mute is None and deaf is None:
            return
        new_mute = bool(mute) if mute is not None else self.mic_muted
        new_deaf = bool(deaf) if deaf is not None else self.deafened
        first    = not self._ever_received
        changed  = (new_mute != self.mic_muted or new_deaf != self.deafened)
        self.mic_muted      = new_mute
        self.deafened       = new_deaf
        self._ever_received = True
        if (changed or first) and self.on_state_change:
            self.on_state_change(self.mic_muted, self.status, self.deafened)

    def set_status(self, status):
        """Manually update online status and trigger redraw if changed."""
        if status != self.status:
            self.status = status
            if self.on_state_change:
                self.on_state_change(self.mic_muted, self.status, self.deafened)

    # ── Main loop ─────────────────────────────────────────────────────────────
    # ── OAuth2 authorization (one-time) ──────────────────────────────────────
    def _load_token(self):
        """Load cached access token from file. Returns None if not found."""
        try:
            p = Path(self.token_cache)
            if p.exists():
                data = json.loads(p.read_text())
                return data.get('access_token')
        except Exception:
            pass
        return None

    def _save_token(self, token):
        """Cache access token to file."""
        try:
            Path(self.token_cache).write_text(json.dumps({'access_token': token}))
        except Exception as e:
            print(f"[Discord IPC] Warning: could not cache token: {e}")

    def _authorize(self):
        """
        One-time OAuth2 authorization via Discord IPC.
        Discord will show a native popup — click Allow.
        Returns an auth code to be exchanged for a token.
        Requires client_secret to exchange. Adds redirect_uri http://127.0.0.1
        to your app in discord.com/developers/applications.
        """
        print("[Discord IPC] Requesting authorization — watch for Discord popup...")
        nonce = self._next_nonce()
        self._send(OP_FRAME, {
            "cmd":   "AUTHORIZE",
            "args":  {
                "client_id":    self.client_id,
                "scopes":       ["rpc"],
                "prompt":       "none",   # skip popup if already approved before
            },
            "nonce": nonce,
        })

        # Wait for AUTHORIZE response (may take a few seconds for user to click Allow)
        while True:
            op, msg = self._recv()
            if msg.get('cmd') == 'AUTHORIZE':
                d = msg.get('data') or {}
                if msg.get('evt') == 'ERROR':
                    raise RuntimeError(
                        f"Authorization rejected: {d.get('message')} "
                        "(did you click Allow in Discord?)")
                code = d.get('code')
                if code:
                    print("[Discord IPC] Authorization code received.")
                    return code

    def _exchange_code(self, code):
        """Exchange authorization code for access token using client_secret."""
        import urllib.request, urllib.parse
        if not self.client_secret:
            raise RuntimeError(
                "client_secret is required for first-time authorization. "
                "Get it from discord.com/developers/applications → your app → "
                "General Information → Client Secret. "
                "Pass it with --client-secret or set it in the script.")

        print(f"[Discord IPC] Exchanging code for token (client_id={self.client_id})...")

        attempts = [
            {},                                    # no redirect_uri
            {'redirect_uri': 'http://127.0.0.1'},
            {'redirect_uri': 'http://localhost'},
        ]

        last_error = None
        for extra in attempts:
            params = {
                'grant_type':    'authorization_code',
                'code':          code,
                'client_id':     self.client_id,
                'client_secret': self.client_secret,
            }
            params.update(extra)
            label = extra.get('redirect_uri', 'no redirect_uri')
            print(f"[Discord IPC]   Trying {label}...")
            try:
                data = urllib.parse.urlencode(params).encode()
                req  = urllib.request.Request(
                    'https://discord.com/api/oauth2/token',
                    data=data,
                    headers={
                        'Content-Type': 'application/x-www-form-urlencoded',
                        'User-Agent':   'DiscordApp/1.0.9007 CFNetwork/1325.0.1 Darwin/21.1.0',
                        'Accept':       'application/json',
                    })
                with urllib.request.urlopen(req, timeout=10) as resp:
                    result = json.loads(resp.read())
                if 'access_token' in result:
                    self._save_token(result['access_token'])
                    print("[Discord IPC] Token obtained and cached.")
                    return result['access_token']
                last_error = f"No access_token in response: {result}"
                print(f"[Discord IPC]   Unexpected response: {result}")
            except Exception as e:
                # Broad catch — read body if it's an HTTPError
                body = ''
                try:
                    body = e.read().decode(errors='replace')
                except Exception:
                    pass
                last_error = f"{type(e).__name__}: {e}  body={body!r}"
                print(f"[Discord IPC]   Failed: {last_error}")

        raise RuntimeError(f"All token exchange attempts failed. Last: {last_error}")

    def _authenticate(self, token):
        """Send AUTHENTICATE command. Returns True on success, False if token expired."""
        nonce = self._next_nonce()
        self._send(OP_FRAME, {
            "cmd":   "AUTHENTICATE",
            "args":  {"access_token": token},
            "nonce": nonce,
        })
        op, msg = self._recv()
        if msg.get('evt') == 'ERROR':
            print(f"[Discord IPC] Auth failed: {(msg.get('data') or {}).get('message')}")
            return False
        print("[Discord IPC] Authenticated successfully.")
        return True

    def _get_idle_seconds(self):
        """Return seconds since last keyboard/mouse input (Windows only)."""
        try:
            import ctypes, ctypes.wintypes as wt
            class LASTINPUTINFO(ctypes.Structure):
                _fields_ = [("cbSize", wt.UINT), ("dwTime", wt.DWORD)]
            lii = LASTINPUTINFO()
            lii.cbSize = ctypes.sizeof(LASTINPUTINFO)
            ctypes.windll.user32.GetLastInputInfo(ctypes.byref(lii))
            millis = ctypes.windll.kernel32.GetTickCount() - lii.dwTime
            return millis / 1000.0
        except Exception:
            return 0.0

    def _check_idle(self):
        """Update status based on system idle time. Fires callback if changed."""
        if not self._idle_monitoring:
            return
        idle = self._get_idle_seconds()
        new_status = 'away' if idle >= self._idle_threshold else 'online'
        if new_status != self.status:
            self.status = new_status
            print(f"[Discord IPC] Status → {self.status} "
                  f"(idle {idle:.0f}s, threshold {self._idle_threshold}s)")
            if self._ever_received and self.on_state_change:
                self.on_state_change(self.mic_muted, self.status, self.deafened)

    def _peek_available(self):
        """Return bytes available in pipe without blocking."""
        import ctypes, ctypes.wintypes as wt
        avail = wt.DWORD(0)
        self._k32.PeekNamedPipe(
            self._pipe_handle, None, 0, None,
            ctypes.byref(avail), None)
        return avail.value

    def run(self):
        """Single-threaded: poll GET_VOICE_SETTINGS every 2s, read responses non-blocking."""
        self._connect_pipe()
        self._running = True

        # Handshake
        self._send(OP_HANDSHAKE, {"v": 1, "client_id": self.client_id})
        op, data = self._recv()

        if data.get('evt') == 'ERROR':
            raise RuntimeError(
                f"Discord IPC error: {data.get('data', {}).get('message')}")

        user = (data.get('data') or {}).get('user') or {}
        print(f"[Discord IPC] Connected — user: {user.get('username', '?')}")

        # ── OAuth2 authentication ────────────────────────────────────────────
        # Try cached token first; if it fails or doesn't exist, do full auth flow.
        token = self._load_token()
        authed = False

        if token:
            authed = self._authenticate(token)
            if not authed:
                print("[Discord IPC] Cached token expired — re-authorizing...")
                # Delete the stale cache
                try: Path(self.token_cache).unlink()
                except: pass
                token = None

        if not authed:
            code  = self._authorize()
            token = self._exchange_code(code)
            authed = self._authenticate(token)

        if not authed:
            raise RuntimeError("Could not authenticate with Discord IPC")

        print("[Discord IPC] Ready — polling mic/deafen state.")
        if self.on_ready:
            try: self.on_ready()
            except Exception: pass
        last_poll = 0.0

        while self._running:
            try:
                now = time.time()

                # Poll every 2 seconds
                if now - last_poll >= 2.0:
                    self._cmd("GET_VOICE_SETTINGS")
                    self._cmd("GET_SELECTED_VOICE_CHANNEL")  # detect VC join/leave
                    self._check_idle()
                    last_poll = now

                # Non-blocking check for incoming data
                avail = self._peek_available()
                if avail > 0:
                    op, msg = self._recv()
                    if op == OP_CLOSE:
                        print("[Discord IPC] Connection closed by Discord")
                        break

                    cmd = msg.get('cmd', '')
                    evt = msg.get('evt', '')
                    d   = msg.get('data') or {}

                    if evt == 'ERROR':
                        print(f"[IPC] ERROR code={d.get('code')} msg={d.get('message')}")
                    if cmd == 'GET_VOICE_SETTINGS' and evt != 'ERROR':
                        self._apply_voice_settings(d)
                    elif cmd == 'GET_SELECTED_VOICE_CHANNEL':
                        # id present = in a VC; empty/null = not in VC
                        in_vc = bool(d and d.get('id'))
                        if in_vc and not self._in_vc:
                            print(f"[Discord IPC] Joined: {d.get('name','?')}")
                            self._in_vc = True
                            if self.on_vc_join:
                                self.on_vc_join()
                        elif not in_vc and self._in_vc and self._ever_received:
                            print("[Discord IPC] Left voice channel")
                            self._in_vc = False
                            if self.on_vc_leave:
                                self.on_vc_leave()
                else:
                    time.sleep(0.05)   # 50ms idle — near-zero CPU

            except RuntimeError as e:
                print(f"[Discord IPC] Lost connection: {e}")
                break
            except Exception as e:
                print(f"[Discord IPC] Error: {e}")
                break

        self._running = False
        try:
            if hasattr(self, '_pipe_handle'):
                self._k32.CloseHandle(self._pipe_handle)
        except: pass


    def stop(self):
        self._running = False
        try:
            self._send(OP_CLOSE, {})
        except: pass
        try:
            if hasattr(self, '_pipe_handle'):
                self._k32.CloseHandle(self._pipe_handle)
        except: pass

# ── Main controller ───────────────────────────────────────────────────────────
class DiscordDisplay:
    """
    Ties together: Discord IPC → skin frame selection → keyboard send.

    Usage:
        display = DiscordDisplay(
            client_id  = "YOUR_DISCORD_APP_ID",
            skin_dir   = "skins/default",
        )
        display.start()
        # ... runs in background thread
        display.stop()
    """

    def __init__(self, client_id, client_secret=None,
                 skin_dir='skins/default', fps=10, retry_interval=5.0):
        self.fps            = fps
        self.retry_interval = retry_interval
        self._skin_dir      = skin_dir
        self._frames        = {}
        self._ipc           = DiscordIPC(client_id, client_secret=client_secret,
                                          on_state_change=self._on_state)
        self._thread        = None
        self._send_thread   = None
        self._pending       = None   # frame key waiting to be sent
        self._lock          = threading.Lock()
        self._last_key      = None

    def _load_skin(self):
        try:
            self._frames = load_skin(self._skin_dir)
            print(f"[DiscordDisplay] Loaded skin from {self._skin_dir} "
                  f"({len(self._frames)} variants)")
        except Exception as e:
            print(f"[DiscordDisplay] Skin load failed: {e}")

    def _on_state(self, mic_muted, status, deafened):
        key = state_to_key(mic_muted, status, deafened)
        print(f"[DiscordDisplay] State: mic={'muted' if mic_muted else 'on'} "
              f"status={status} deaf={'yes' if deafened else 'no'} → {key}")
        with self._lock:
            self._pending = key
        self._flush_pending()

    def _flush_pending(self):
        with self._lock:
            key = self._pending
            if key is None or key == self._last_key:
                return
            frame = self._frames.get(key)
            if frame is None:
                # Fallback: try removing middle letter variation
                fallback = key[0] + 'g' + key[2]
                frame = self._frames.get(fallback)
                if frame is None:
                    print(f"[DiscordDisplay] No frame for key '{key}' — skipping")
                    return
            self._pending  = None
            self._last_key = key

        # Send in a separate thread so IPC loop isn't blocked
        t = threading.Thread(target=self._send, args=(frame,), daemon=True)
        t.start()

    def _send(self, frame):
        try:
            send_frame(frame, fps=self.fps)
        except Exception as e:
            print(f"[DiscordDisplay] HID send failed: {e}")

    def set_status(self, status):
        """Update online status manually (online/away/dnd/invisible)."""
        self._ipc.set_status(status)

    def start(self):
        """Start IPC connection + send loop in background threads."""
        self._load_skin()
        self._thread = threading.Thread(target=self._ipc_loop, daemon=True)
        self._thread.start()

    def _ipc_loop(self):
        while True:
            try:
                print("[DiscordDisplay] Connecting to Discord IPC...")
                self._ipc.run()
            except Exception as e:
                print(f"[DiscordDisplay] IPC error: {e}")
            print(f"[DiscordDisplay] Reconnecting in {self.retry_interval}s...")
            time.sleep(self.retry_interval)

    def stop(self):
        self._ipc.stop()

# ── CLI usage ─────────────────────────────────────────────────────────────────
if __name__ == '__main__':
    import argparse

    parser = argparse.ArgumentParser(description='DP-104 Discord VC Display')
    parser.add_argument('--client-id',  required=True,
                        help='Discord app client ID (discord.com/developers/applications)')
    parser.add_argument('--client-secret', default=None,
                        help='Discord app client secret (required first run only, then cached)')
    parser.add_argument('--skin',       default='skins/default',
                        help='Path to skin folder containing PNG files')
    parser.add_argument('--preview',    action='store_true',
                        help='Print current state without sending to keyboard')
    parser.add_argument('--status',     default=None,
                        choices=['online','away','dnd','invisible'],
                        help='Override online status (set once, then IPC controls mic/deaf)')
    args = parser.parse_args()

    display = DiscordDisplay(
        client_id=args.client_id,
        client_secret=args.client_secret,
        skin_dir=args.skin,
    )

    if args.status:
        display._ipc.status = args.status

    display.start()

    try:
        print("Running — press Ctrl+C to stop")
        print("Note: Online status must be set manually with --status or via the GUI.")
        print("      Mic mute and deafen are read automatically from Discord.")
        while True:
            time.sleep(1)
    except KeyboardInterrupt:
        print("\nStopping...")
        display.stop()
