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
- **Custom pixel page** — floating source icon, brand-color background, 9-bar EQ visualizer
- **16 source icons** — Spotify, YouTube, YouTube Music, Twitch, WinAMP, foobar2000, TIDAL, Apple Music, Amazon Music, VLC, SoundCloud, Pandora, Deezer, Browser, and more
- EQ animates while playing, silences when paused
- Text scroll always sends first; pixel animation follows
- Toggle between custom pixel display and text-scroll-only mode

### 🎮 Discord VC
- Mic mute and deafen status at a glance — read automatically from Discord local IPC
- Online status (Online / Away / DnD / Invisible) set manually in GUI
- VC join/leave detected automatically via `GET_SELECTED_VOICE_CHANNEL` — fallback display restores when you leave
- One-time OAuth2 authorization via Discord popup; token cached locally
- Idle detection — auto-sets Away after 5 minutes of system inactivity (never overrides DnD/Invisible)
- **Skin system** — 12 PNG files (24×8 each) in `skins/default/`, artist-designed
- Invisible variant auto-generated from online variants

### 🖥 GUI
- **Live 24×8 pixel previews** on Weather, Now Playing, and Discord tabs
- **Three tabs** — Now Playing, Weather, Discord — right-clickable to enable/disable
- **Weather tab color states**:
  - 🟢 Green — weather on, Discord tab off
  - 🟡 Yellow — Discord on, not in VC (weather showing, will be overwritten when VC starts)
  - 🟠 Orange — Discord in VC (weather suppressed)
  - 🔴 Red — weather disabled
- **Priority system** — Discord VC > NP Custom > Weather for the pixel page
- **4-second cooldown** between keyboard sends — prevents firmware crashes
- **FPS selector** — 5 / 10 / 15 / 20 fps
- **Settings persistence** — all preferences saved on exit, restored on launch
- **Debug menu** (`~` key) — Weather / Now Playing / Discord tabs with full send controls
- **Page switch** — CLEAR button switches keyboard to blank page
- **Credits** (`F1`) — Big Guy, Human Guy, Artist Gal
- **System tray** — minimize to tray, X exits completely
- **Windows toast notifications** on successful weather update

---

## Requirements

- Windows 10 or 11
- Python 3.10+
- TickType DP-104 keyboard connected via USB

### Python dependencies

```
pip install hidapi pystray pillow
```

---

## Installation

1. Clone or download this repository
2. Install dependencies:
   ```
   pip install hidapi pystray pillow
   ```
3. Place all files in the **same folder**:
   - `dp104_gui.pyw`
   - `dp104_weather_v2.py`
   - `dp104_nowplaying.py`
   - `dp104_discord.py`
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
| `skins/default/*.png` | Discord VC skin files (12 PNGs, 24×8 each) |
| `dp104_settings.json` | Auto-generated settings |
| `.discord_token` | Auto-generated OAuth2 token cache |

---

## Discord VC Setup

1. Go to [discord.com/developers/applications](https://discord.com/developers/applications)
2. Create a new application (free)
3. Under **OAuth2 → Redirects**, add `http://127.0.0.1` and `http://localhost`
4. Make sure **Public Client** is **OFF** in OAuth2 settings
5. Copy your **Client ID** from General Information
6. Copy your **Client Secret** from General Information
7. In the GUI Discord tab: paste Client ID, paste Client Secret, click **Connect**
8. Discord shows a native authorization popup — click **Authorize**
9. Token cached in `.discord_token` — client secret only needed once

### Skin File Naming

24×8 PNGs in `skins/default/`, named by state:

| Letter | Represents | Values |
|--------|-----------|--------|
| 1st | Mic | `g` = unmuted, `r` = muted |
| 2nd | Status | `g` = online, `y` = away, `r` = DnD |
| 3rd | Deafen | `g` = undeafened, `r` = deafened |

Examples: `ggg.png`, `rrr.png`, `ryr.png`, `gyg.png`
Invisible variants (`gi*`, `ri*`) are auto-generated — no extra files needed.

---

## How It Works

The DP-104 exposes a Raw HID interface (`VID=0xE560 PID=0xE104 MI_01`).

**Text / Scroll page** (`0x07 0x1A 0x05 ...`)
Sends ASCII text to the keyboard's scrolling display. Used for Now Playing text.

**Pixel / Custom page** (`0xD1 0x30` → `0xD1 0x31` packets)
Streams HSV pixel frames to the 24×8 LED matrix. Protocol requires:
- Header packet with frame count and FPS, followed by ACK read
- 1-second buffer allocation delay
- 25-byte pixel chunks with 4-byte global offsets
- 320ms inter-frame gap between frames

**Page switch** (`0x07 0x1A 0x02 [page] ...`)
Switches the active display page: `0x00` = OFF, `0x02` = CUSTOM, `0x06` = SCROLL.

**Pixel format:** HSV (not RGB). Hardware quirk: any pixel with saturation > 0 at brightness < 20/255 renders red regardless of hue. All color math respects this threshold.

**Priority queue:** A single background worker serialises all sends. 4-second cooldown between sends. Higher-priority sends discard pending lower-priority ones — Discord always wins over NP which always wins over Weather.

---

## Weather Animation Codes

| Code | Animation | When Used |
|------|-----------|-----------|
| 0 | Sunny | Clear day |
| 1 | Partly Cloudy | Partly cloudy day |
| 2 | Cloudy (House) | Overcast / foggy |
| 3 | Rainy | Rain / drizzle / showers |
| 4 | Snowy | Snow / sleet / ice |
| 5 | Thunderstorm | Thunder / storms |
| 6 | Night Clear | Clear night |
| 7 | Night Partly Cloudy | Partly cloudy night |

---

## Troubleshooting

**GUI opens then immediately goes to tray**
Was a startup bug in older versions — fixed in v1.2.5. If it still happens, run `python dp104_gui.pyw` from the console to see the full error.

**Discord shows "Authenticating..." for a long time**
Normal — OAuth2 with the Discord popup takes 15–20 seconds. After 30 seconds it switches to "Connected (verifying...)" which means it's still working. Don't close it.

**Weather tab shows Orange instead of Yellow**
Fixed in v1.2.5. Previously VC status was set incorrectly from voice settings updates rather than from `GET_SELECTED_VOICE_CHANNEL`.

**Keyboard crashes / flashes then goes blank**
Previously caused by concurrent HID sends. Fixed in v1.2.0 with the priority queue and 4-second cooldown.

---

## Credits

| | |
|-|-|
| **Claude** | Big Guy |
| **Mikan** | Human Guy |
| **remedy** | Artist Gal |

*2026 · v1.2.5*

> **Note:** This targets a specific firmware version of the DP-104. If TickType releases a firmware update that changes the HID protocol, byte sequences may need updating. The debug menu (`~`) and CHANGELOG.md will orient anyone picking up where we left off.
