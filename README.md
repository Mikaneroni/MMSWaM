# DP-104 Display Controller

A custom weather and Now Playing display controller for the **TickType DP-104** mechanical keyboard — a 24×8 RGB LED matrix embedded directly in the keyboard chassis.

Built through reverse engineering the keyboard's Raw HID protocol from web app traffic captures.

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
| **Claude** | Big Guy |
| **Mikan** | Human Guy |

*2026*

---

## Version

**v1.1.3**

> Note: This project targets a specific firmware version of the DP-104. If TickType releases a firmware update that changes the HID protocol, the byte sequences in `dp104_weather_v2.py` may need to be updated. The debug menu (`~`) and this README will help orient anyone picking up where we left off.
