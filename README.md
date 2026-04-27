# DP-104 Display Controller

A custom weather and Now Playing display controller for the **TickType DP-104** mechanical keyboard — a 24×8 RGB LED matrix embedded directly in the keyboard chassis.

Built through reverse engineering the keyboard's Raw HID protocol from web app traffic captures.

---

v1.2.0 — 2026-04-26
New Features
Discord VC Tab

New Discord tab in the GUI for at-a-glance voice channel status display
Connects to Discord via local IPC pipe (no bot token required)
Mic mute and deafen status read automatically from Discord
Online status selector (Online / Away / DnD / Invisible) — set manually, displayed on keyboard
One-time OAuth2 authorization flow; token cached locally after first run
"When not in VC show:" fallback selector — Weather or Now Playing
Idle detection: automatically sets status to Away after 5 minutes of system inactivity
Skin system: loads 12 PNG files (24×8, one per state combination) from skins/default/
Invisible variant auto-generated from online variants (greyed status box)
Reconnects automatically if Discord restarts
Now Playing Custom Pixel Display

Now Playing tab now sends a full pixel animation to the CUSTOM page
Floating source icon (Spotify, YouTube, Twitch, WinAMP, foobar2000, TIDAL, Apple Music, Amazon Music, VLC, SoundCloud, Pandora, Deezer, Browser, and more)
Brand-color tinted background per source
Black framing rows above and below the icon (move with bob animation)
Play/pause indicator in random color, bobs at half-phase offset from icon
7-bar EQ visualizer — animates while playing, goes silent when paused
"Custom display" checkbox — uncheck to use text scroll only (reverts to SCROLL page)
Pixel preview added to the Now Playing tab
Text scroll always sends before pixel animation (scroll page updates first)
Priority System for Custom Pixel Page

Clear hierarchy for the CUSTOM page: Discord VC > NP Custom > Weather
Weather fetches and updates preview while NP custom is active, but does not overwrite
Weather automatically restores to custom page when NP stops or custom display is disabled
Discord VC skin takes full priority; weather and NP queue behind it
Weather Tab Color States

Weather tab button now reflects Discord integration state:
🟢 Green — weather enabled, Discord tab disabled (normal)
🟡 Yellow — Discord enabled, not in VC (weather showing, will be overwritten when VC starts)
🟠 Orange — Discord enabled and actively in VC (weather suppressed)
🔴 Red — weather disabled entirely
Changes
Firmware Red-Rendering Bug Fix (Confirmed Threshold)

Hardware test confirmed: any pixel with saturation > 0 at val < 20 renders red on firmware
dim() and bright() in all three modules now clamp to val=20 minimum for colored pixels
Additional fix: pixels with hue near 0° (< 5° or > 350°) and low saturation (< 40/255) have their saturation stripped to 0 — renders as safe neutral grey instead of red
Applied consistently across dp104_weather_v2.py, dp104_nowplaying.py, dp104_discord.py
Discord Skin Loader

Anti-aliasing bleed pixels (0 < val < 40) snapped to confirmed-safe grey HSV(0,0,20)
Near-zero hue / low-sat separator pixels stripped to pure grey to prevent red rendering
RGBA PNG support (artist files exported with alpha channel)
Now Playing Pixel Display

EQ visualizer extended from 7 to 9 bars (cols 15–23)
EQ bars use pure white (sat=0) for maximum contrast against any brand-color background
Play/pause symbol uses a random hue each refresh (only changes when keyboard actually updates)
Bob animation uses 2-position cosine wave — static bottom black row always visible
Twitch icon tail corrected to bottom-right (matches actual Twitch logo)
Spotify circle background fills behind soundwave arcs
YouTube center white dot added to play triangle
Tab System

Right-click tab to toggle enable/disable (green = on, red = off) now applies to all 3 tabs
Disabling active tab switches view to the next available enabled tab
Tab enable/disable states saved and restored on startup
Settings Persistence

Discord client ID, online status, fallback preference, and NP custom toggle now saved to dp104_settings.json and restored on launch


---

## Features

### Weather Display
- **8 animated weather types** — Sunny, Partly Cloudy, Cloudy (House scene), Rainy, Snowy, Thunderstorm, Night Clear, Night Partly Cloudy
- **Day/night routing** — automatically switches between day and night animations based on real sunrise/sunset data from the weather API
- **Color-coded temperature** — current temp displays in the left zone using a smooth gradient: deep blue (≤10°F) → cyan → green → yellow → orange → deep red (≥90°F)
- **High or Low of day** — right zone shows High (red) during the day and Low (blue) at night, full-height tall font
- **Wind-driven rain** — rain slant angle scales linearly with wind speed (0–60 mph → 0–40°)
- **Zip code or City,ST** — enter a US zip code and it resolves to the correct city name via zippopotam.us before querying the weather API

### Now Playing
- Pulls the currently playing track from any Windows media session (Spotify, browsers, etc.)
- Sends title and artist to the keyboard's scroll display page
- Runs concurrently with weather — both update independently regardless of which tab is active

### GUI
- **Live 24×8 preview** — pixel-accurate simulation of what the keyboard is showing, cycling at the selected FPS
- **FPS selector** — 5 / 10 / 15 / 20 fps
- **Tab enable/disable** — right-click either tab button to toggle that service on or off (green = enabled, red = disabled)
- **Auto-refresh** — weather refreshes on a configurable timer (15 / 30 / 60 / 120 / 240 / 480 minutes)
- **Settings persistence** — location, poll interval, refresh interval, FPS, and active tab are saved on exit and restored on startup
- **Debug menu** (`~` key) — force any animation, override temperature, set wind speed for testing
- **Credits** (`F1`) — you know who made this
- **System tray** — minimize to tray via the Tray button or the title bar minimize button; X button exits completely
- **Windows toast notifications** — notifies when weather successfully updates

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
3. Place `dp104_gui.pyw` and `dp104_weather_v2.py` in the **same folder**
4. Run the GUI:
   ```
   pythonw dp104_gui.pyw
   ```
   Or just double-click `dp104_gui.pyw` if `.pyw` files are associated with Python

---

## File Overview

| File | Purpose |
|------|---------|
| `dp104_gui.pyw` | Main GUI application — weather fetch, Now Playing, HID send |
| `dp104_weather_v2.py` | Weather animation engine — builds HSV pixel frames for each weather type |

---

## How It Works

The DP-104 exposes a Raw HID interface (`VID=0xE560 PID=0xE104 MI_01`) with two distinct protocols:

**Text / Scroll page** (`0x07 0x1A 0x05 ...`)
Used for Now Playing — sends ASCII text to the keyboard's built-in scrolling display.

**Pixel / Custom page** (`0xD1 0x30` header → `0xD1 0x31` pixel packets)
Used for weather animations — streams full HSV pixel frames directly to the 24×8 LED matrix. Pixel format is HSV (not RGB). The protocol requires:
- A header packet with frame count and FPS, followed by an ACK read
- A 1-second buffer allocation delay
- 25-byte pixel chunks with 4-byte global offsets
- 320ms inter-frame gap between frames

All of this was reverse-engineered from USB traffic captures of the official TickType web configurator.

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
| **Mikan** | Human Guy |
| **Claude** | Big Guy (ai) |

*2026*

---

## Version

**v1.1.3**

> Note: This project targets a specific firmware version of the DP-104. If TickType releases a firmware update that changes the HID protocol, the byte sequences in `dp104_weather_v2.py` may need to be updated. The debug menu (`~`) and this README will help orient anyone picking up where we left off.
