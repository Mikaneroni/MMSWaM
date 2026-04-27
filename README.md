# DP-104 Display Controller

A custom display controller for the **TickType DP-104** mechanical keyboard — a 24×8 RGB LED matrix embedded in the keyboard chassis.

Built through reverse engineering the keyboard's Raw HID protocol from USB traffic captures of the official TickType web configurator.

---

## Features

### 🌤 Weather Display
- **8 animated weather types** — Sunny, Partly Cloudy, Cloudy (House scene), Rainy, Snowy, Thunderstorm, Night Clear, Night Partly Cloudy
- **Day/night routing** — automatically switches between day and night animations using real sunrise/sunset data
- **Color-coded temperature** — smooth gradient: deep blue (≤10°F) → cyan → green → yellow → orange → deep red (≥90°F)
- **High or Low of day** — right zone shows High (red) during the day, Low (blue) at night in full-height TALL7 font
- **Wind-driven rain** — slant angle scales linearly with wind speed (0–60 mph → 0–40°)
- **Seamless cloud loops** — tiled cloud rendering, clouds wrap from right edge back to left with no gap or pause
- **Zip code or City,ST** — resolves US zip codes to city names via zippopotam.us

### ♪ Now Playing
- Pulls the currently playing track from any Windows media session (Spotify, browsers, VLC, etc.)
- **Custom pixel page** — floating source icon with brand-color background, play/pause indicator, 9-bar EQ visualizer
- **16 source icons** — Spotify, YouTube, YouTube Music, Twitch, WinAMP, foobar2000, TIDAL, Apple Music, Amazon Music, VLC, SoundCloud, Pandora, Deezer, Browser, and more
- EQ visualizer animates when playing, goes silent when paused
- Text scroll always sends before pixel animation (scroll page updates first)
- Toggle between custom pixel display and text-scroll-only mode

### 🎮 Discord VC
- Displays mic status, deafen status, and online status at a glance
- Connects via **Discord local IPC** — no bot token required
- Mic mute and deafen read automatically; online status set manually in GUI
- One-time OAuth2 authorization; token cached locally after first run
- **Skin system** — 12 PNG files (24×8, one per state combination) in `skins/default/`
- Idle detection — automatically sets Away after 5 minutes of system inactivity
- Invisible variant auto-generated (greyed status box)

### 🖥 GUI
- **Live 24×8 pixel preview** on both Weather and Now Playing tabs, cycling at selected FPS
- **Three tabs** — Now Playing, Weather, Discord — each right-clickable to enable/disable
- **Weather tab color states** based on Discord integration:
  - 🟢 Green — weather enabled, Discord disabled
  - 🟡 Yellow — Discord enabled, not in VC (weather showing, will be overwritten when VC starts)
  - 🟠 Orange — Discord enabled, actively in VC (weather suppressed)
  - 🔴 Red — weather disabled
- **Priority system** for the custom pixel page: Discord VC > NP Custom > Weather
- **FPS selector** — 5 / 10 / 15 / 20 fps
- **Auto-refresh** — weather refreshes on a configurable timer
- **Settings persistence** — all preferences saved on exit and restored on startup
- **Debug menu** (`~` key) — force any weather animation, override temperature and wind speed
- **Credits** (`F1`)
- **System tray** — minimize via Tray button or title bar minimize; X button exits completely
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
4. For Discord VC: create a `skins/default/` folder and add your 12 skin PNG files
5. Run:
   ```
   pythonw dp104_gui.pyw
   ```
   Or double-click `dp104_gui.pyw` if `.pyw` files are associated with Python

---

## File Overview

| File | Purpose |
|------|---------|
| `dp104_gui.pyw` | Main GUI — weather fetch, Now Playing, Discord VC, HID send, priority management |
| `dp104_weather_v2.py` | Weather animation engine — builds HSV pixel frames for each weather type |
| `dp104_nowplaying.py` | Now Playing pixel display — source icons, EQ visualizer, bob animation |
| `dp104_discord.py` | Discord VC display — IPC client, OAuth2, skin loader, idle detection |
| `skins/default/*.png` | Discord VC skin files (12 PNGs, 24×8 pixels each) |
| `dp104_settings.json` | Auto-generated settings file |
| `.discord_token` | Auto-generated OAuth2 token cache (first Discord run only) |

---

## Discord VC Setup

1. Go to [discord.com/developers/applications](https://discord.com/developers/applications)
2. Create a new application (free)
3. Under **OAuth2 → Redirects**, add `http://127.0.0.1` and `http://localhost`
4. Copy your **Client ID** from General Information
5. Copy your **Client Secret** from General Information
6. In the GUI, open the Discord tab, paste your Client ID and Client Secret, click Connect
7. Discord will show a native authorization popup — click **Authorize**
8. Token is cached in `.discord_token` — client secret only needed once

### Skin File Naming

Place 24×8 PNG files in `skins/default/` named by state:

| Letter | Position | Values |
|--------|----------|--------|
| 1st | Mic | `g` = unmuted, `r` = muted |
| 2nd | Status | `g` = online, `y` = away, `r` = DnD |
| 3rd | Deafen | `g` = undeafened, `r` = deafened |

Examples: `ggg.png`, `rrr.png`, `ryr.png`, `gyg.png`  
Invisible variants (`gi`, `ri`) are auto-generated — no files needed.

---

## How It Works

The DP-104 exposes a Raw HID interface (`VID=0xE560 PID=0xE104 MI_01`) with two protocols:

**Text / Scroll page** (`0x07 0x1A 0x05 ...`)
Sends ASCII text to the keyboard's built-in scrolling display. Used for Now Playing text.

**Pixel / Custom page** (`0xD1 0x30` header → `0xD1 0x31` pixel packets)
Streams full HSV pixel frames directly to the 24×8 LED matrix. Used for weather, NP custom display, and Discord VC. Protocol requires:
- A header packet with frame count and FPS, followed by an ACK read
- 1-second buffer allocation delay
- 25-byte pixel chunks with 4-byte global offsets
- 320ms inter-frame gap between frames

**Pixel format:** HSV (not RGB). A confirmed hardware quirk: any pixel with saturation > 0 at brightness < 20/255 renders red on the firmware regardless of hue. All color math clamps to this threshold.

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

## Credits

| | |
|-|-|
| **Claude** | Big Guy |
| **Mikan** | Human Guy |

*2026*

---

## Version

**v1.2.0**

See [CHANGELOG.md](CHANGELOG.md) for full history.

> **Note:** This project targets a specific firmware version of the DP-104.
> If TickType releases a firmware update that changes the HID protocol, the byte
> sequences may need updating. The debug menu (`~`) and CHANGELOG.md will help
> orient anyone picking up where we left off.
