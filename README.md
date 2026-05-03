# DP-104 Display Controller

A custom display controller for the **TickType DP-104** mechanical keyboard — a 24×8 RGB LED matrix embedded in the keyboard chassis.

Built through reverse engineering the keyboard's Raw HID protocol from USB traffic captures of the official TickType web configurator.

---

## Features

### 🌤 Weather Display
- **8 animated weather types** — Sunny, Partly Cloudy, Cloudy (House scene), Rainy, Snowy, Thunderstorm, Night Clear, Night Partly Cloudy
- **Day/night routing** — automatically switches using real sunrise/sunset data
- **Color-coded temperature** — gradient from deep blue (≤10°F) to deep red (≥90°F)
- **High or Low of day** — right zone shows High (red) in the day, Low (blue) at night
- **Wind-driven rain** — slant scales with wind speed (0–60 mph)
- **Seamless cloud loops** — tiled rendering, no gap or pause on wrap

### ♪ Now Playing
- Pulls the current track from any Windows media session (Spotify, browsers, VLC, etc.)
- **Custom pixel page** — floating source icon with brand-color background, 9-bar EQ visualizer
- **16 source icons** — Spotify, YouTube, YouTube Music, Twitch, WinAMP, foobar2000, TIDAL, Apple Music, Amazon Music, VLC, SoundCloud, Pandora, Deezer, Browser, and more
- EQ animates while playing, silences when paused
- Toggle between custom pixel display and text-scroll-only mode

### 🎮 Discord VC
- Mic mute and deafen status at a glance — read automatically from Discord local IPC
- Online status (Online / Away / DnD / Invisible) set manually in GUI
- One-time OAuth2 authorization; token cached locally after first run
- **Skin system** — 12 PNG files (24×8 each) in `skins/default/`, artist-designed by remedy
- Idle detection — auto-sets Away after 5 minutes of system inactivity (never overrides DnD/Invisible)
- Invisible variant auto-generated from online variants

### ⌨ WPM Tracker
- Tracks rolling 60-second keystroke average globally
- **24×8 pixel display** — 10-bar history graph (one bar per minute) + current WPM number
- **Color relative to personal best**: Green (casual) → Yellow (average) → Red (pushing)
- Personal best cached to `dp104_wpm_pb.json`, persists between sessions
- Configurable send interval (1 / 2 / 5 / 10 / 15 / 30 seconds)
- Uses Windows `GetAsyncKeyState` polling — works inside the GUI process

### 🖥 GUI
- **Live 24×8 pixel previews** on all four tabs
- **Four tabs** — Now Playing, Weather, Discord, WPM — right-clickable to enable/disable
- **Weather tab color states**: 🟢 normal · 🟡 Discord on, not in VC · 🟠 Discord in VC · 🔴 disabled
- **Priority system** for the custom pixel page: Discord (1) > NP Custom (2) > Weather (3) > WPM (4)
- **4-second cooldown** between keyboard sends — prevents firmware crashes
- **Shared HID lock** — text sends and pixel sends are fully serialized, no concurrent device access
- **FPS selector** — 5 / 10 / 15 / 20 fps
- **Settings persistence** — all preferences saved on exit, restored on launch
- **Debug menu** (`~` key) — Weather / Now Playing / Discord tabs with full send controls
- **Credits** (`F1`) — Big Guy, Human Guy, Artist Gal
- **System tray** — TRAY button minimizes to tray; X button exits completely
- **Windows toast notifications** on successful weather update

---

## Requirements

- Windows 10 or 11
- Python 3.10+
- TickType DP-104 keyboard connected via USB

### Python dependencies

```
pip install hidapi pystray pillow pynput
```

---

## Installation

1. Clone or download this repository
2. Install dependencies:
   ```
   pip install hidapi pystray pillow pynput
   ```
3. Place all files in the **same folder**:
   - `dp104_gui.pyw`
   - `dp104_weather_v2.py`
   - `dp104_nowplaying.py`
   - `dp104_discord.py`
   - `dp104_wpm.py`
4. For Discord VC: create `skins\default\` and add the 12 skin PNG files
5. Run:
   ```
   pythonw dp104_gui.pyw
   ```

---

## File Overview

| File | Purpose |
|------|---------|
| `dp104_gui.pyw` | Main GUI — all services, priority queue, HID send |
| `dp104_weather_v2.py` | Weather animation engine — 8 animation types |
| `dp104_nowplaying.py` | Now Playing pixel display — 16 source icons, EQ |
| `dp104_discord.py` | Discord VC — IPC client, OAuth2, skin loader |
| `dp104_wpm.py` | WPM tracker — keystroke counting, pixel frame builder |
| `skins/default/*.png` | Discord VC skin files (12 PNGs, 24×8 each) |
| `dp104_settings.json` | Auto-generated settings |
| `dp104_wpm_pb.json` | Auto-generated WPM personal best cache |
| `.discord_token` | Auto-generated OAuth2 token cache |

---

## Discord VC Setup

1. Go to [discord.com/developers/applications](https://discord.com/developers/applications)
2. Create a new application (free)
3. Under **OAuth2 → Redirects**, add `http://127.0.0.1`
4. Copy your **Client ID** and **Client Secret** from General Information
5. In the GUI Discord tab: paste Client ID, paste Client Secret, click **Connect**
6. Discord shows a native authorization popup — click **Authorize** in the Discord desktop app (not the browser)
7. Token cached in `.discord_token` — client secret only needed once

### Skin File Naming

| Letter | Represents | Values |
|--------|-----------|--------|
| 1st | Mic | `g` = unmuted, `r` = muted |
| 2nd | Status | `g` = online, `y` = away, `r` = DnD |
| 3rd | Deafen | `g` = undeafened, `r` = deafened |

Examples: `ggg.png`, `rrr.png`, `ryr.png`
Invisible variants auto-generated — no extra files needed.

---

## How It Works

The DP-104 exposes a Raw HID interface (`VID=0xE560 PID=0xE104 MI_01`).

**Text / Scroll page** (`0x07 0x1A 0x05 ...`)
Sends ASCII text to the keyboard's scrolling display. Used for Now Playing text.

**Pixel / Custom page** (`0xD1 0x30` → `0xD1 0x31` packets)
Streams HSV pixel frames to the 24×8 LED matrix. Protocol:
- Header packet with frame count and FPS, followed by ACK read
- 1-second buffer allocation delay
- 25-byte pixel chunks with 4-byte global offsets
- 320ms inter-frame gap between frames

**Page switch** (`0x07 0x1A 0x02 [page] ...`)
Switches the display: `0x00` = OFF, `0x02` = CUSTOM, `0x06` = SCROLL.

**Pixel format:** HSV (not RGB). Hardware quirk: any pixel with saturation > 0 at brightness < 20/255 renders red regardless of hue. All color math respects this threshold.

**Priority queue + HID lock:** A single background worker serialises all pixel sends with a 4-second cooldown. A shared `threading.Lock()` also gates text sends so nothing overlaps at the device level.

---

## Troubleshooting

**WPM shows 0** — Make sure `dp104_wpm.py` is in the same folder. The tracker uses `GetAsyncKeyState` polling — no extra setup needed, but it only counts keystrokes while the GUI process is running.

**Discord stays on "Authenticating"** — Auth takes 15–20 seconds. The popup appears in the Discord desktop client, not the browser. If you don't see it, check that Discord is open and running.

**Weather tab shows Orange instead of Yellow** — Discord tab is enabled and the GUI detects you may be in a VC. Set Discord to disabled if not using it.

**Keyboard crashes occasionally** — The 4-second cooldown between sends and the shared HID lock reduce this to a rare edge case. If it happens, CLEAR and wait a few seconds before the next send.

---

## Credits

| | |
|-|-|
| **Claude** | Big Guy |
| **Mikan** | Human Guy |
| **remedy** | Artist Gal |

*2026 · v1.3.0*

> **Note:** Targets a specific firmware version of the DP-104. If TickType releases a firmware update, byte sequences may need updating. The debug menu (`~`) and CHANGELOG.md will orient anyone picking up where we left off.
