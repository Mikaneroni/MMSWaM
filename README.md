# DP-104 Display Controller

A custom display controller for the **TickType DP-104** mechanical keyboard — a 24×8 RGB LED matrix embedded in the keyboard chassis.

Built through reverse engineering the keyboard's Raw HID protocol from USB traffic captures of the official TickType web configurator.

---

## Features

### 🌤 Weather
- 8 animated weather types — Sunny, Partly Cloudy, Cloudy/House, Rainy, Snowy, Thunderstorm, Night Clear, Night Partly Cloudy
- Day/night routing, temperature gradient, wind-driven rain slant
- Auto-refresh every 30 minutes by default (configurable)

### ♪ Now Playing
- Pulls current track from any Windows media session (Spotify, browsers, VLC, etc.)
- 16 source icons with brand-color backgrounds, 9-bar EQ visualizer
- **Change-only mode** — pixel display only updates when the source app changes (tab turns purple)
- Text scroll always sends first; pixel page follows with a 4.5s gap

### 🎮 Discord VC
- Mic mute and deafen read automatically from Discord local IPC
- Manual online status (Online / Away / DnD / Invisible) — never auto-overridden
- Idle detection — auto-sets Away after 5 min; never overrides DnD/Invisible
- One-time OAuth2 authorization; token cached
- Skin system — 12 PNGs (24×8) in `skins/default/`
- **When not in VC show:** Weather · Now Playing · WPM · Clock · None

### ⌨ WPM / APM Tracker
- Rolling 60-second keystroke average using `GetAsyncKeyState` polling (works inside GUI process)
- **WPM** (words per minute, keystrokes÷5) and **APM** (raw keystrokes per minute) tracked simultaneously
- APM checkbox flips all labels, values, graph history, and keyboard display to APM
- 10-bar history graph + current value number on the 24×8 display
- Color: Green (casual) → Yellow (average) → Red (pushing hard), relative to personal best
- Personal best cached to `dp104_wpm_pb.json`
- **Three send modes:** ⏱ Timer (every X seconds) · ✋ Pause detection (WPM drops ≥30%) · 🔀 Both

### 🕐 World Clock
- Configurable city list — label + IANA timezone + **color**
- 10 named colors (Red/Orange/Amber/Yellow/Green/Cyan/Blue/Purple/Pink/White) — render on keyboard AND in GUI list
- F-key cycling (F13–F24) through cities — immediate send on key press
- **Three display modes:**
  - ⌚ Still — one frame per minute; auto-remaps Red→F13 and Pause→LcdChangeScr; restores keys on stop/exit
  - ⏱ Seconds — seconds progress bar, re-sent every 15 seconds
  - ✨ Blink — animated colon (5 on / 5 off at 10fps)
- Smart timing: skips top-of-minute refresh if within 4 seconds of last send

### 🖥 GUI
- Live 24×8 pixel previews on all five tabs
- **📌 Pin tab** — forces current tab's sends to priority 0 (beats everything)
- Tab color states: 🟢 enabled · 🔴 disabled · 🟣 NP change-only · 🟡 Discord no-VC · 🟠 Discord in-VC
- Priority: Discord (1) > NP (2) > Weather (3) > WPM (4) > Clock (5) · Pinned tab (0)
- Shared HID lock — all sends serialized; 4-second cooldown between pixel sends
- Debug menu (`~`) — test controls + **key remap buttons** (confirmed working)
- System tray watchdog — rebuilds tray icon if Explorer restarts
- Settings persistence — all tab states, intervals, and preferences saved/restored

---

## Requirements

- Windows 10 or 11
- Python 3.10+
- TickType DP-104 keyboard via USB

```
pip install hidapi pystray pillow pynput
```

---

## Installation

1. Place all files in the same folder
2. `pip install hidapi pystray pillow pynput`
3. For Discord VC: create `skins\default\` with the 12 skin PNGs
4. Run: `pythonw dp104_gui.pyw`

**Or** build a standalone `.exe` with `build.bat`.

---

## Files

| File | Purpose |
|------|---------|
| `dp104_gui.pyw` | Main GUI |
| `dp104_weather_v2.py` | Weather animations |
| `dp104_nowplaying.py` | Now Playing pixel display |
| `dp104_discord.py` | Discord IPC + OAuth2 + skin loader |
| `dp104_wpm.py` | WPM/APM tracker |
| `dp104_worldclock.py` | World clock — frame builder + F-key cycling |
| `skins/default/*.png` | Discord VC skins (12 PNGs, 24×8) |
| `build.bat` / `dp104.spec` | PyInstaller build tools |
| `dp104_hid_sniffer.py` | HID packet logger (dev tool) |
| `webhid_sniffer.html` | WebHID browser sniffer (captures web configurator traffic) |

---

## Discord Setup

1. [discord.com/developers/applications](https://discord.com/developers/applications) → New Application
2. OAuth2 → Redirects → add `http://127.0.0.1`
3. Copy Client ID + Client Secret → paste in GUI Discord tab → Connect
4. Authorize in the **Discord desktop client** (not browser) — native popup appears
5. Token cached; client secret only needed once

### Skin naming: `{mic}{status}{deaf}.png`

| Position | g | r/y |
|----------|---|-----|
| Mic (1st) | unmuted | muted |
| Status (2nd) | online | r=DnD · y=away |
| Deafen (3rd) | undeafened | deafened |

Invisible variants auto-generated.

---

## Key Remap (Debug Menu → 🎮 Discord tab)

Confirmed working via WebHID packet capture:

| Button | Remap |
|--------|-------|
| Red → F13 | Enables F-key cycling for world clock |
| Red → LcdChangeScr | Restores screen cycle function |
| Pause → LcdChangeScr | Frees up Pause key |
| Pause → Pause | Restores Pause key |

World Clock STILL mode auto-remaps and auto-restores on enable/disable.

---

## How It Works

**HID interface:** `VID=0xE560 PID=0xE104 MI_01`

**Pixel protocol:** `0xD1 0x30` (allocate buffer) → `0xD1 0x31` chunks (25 bytes each, 4-byte global offset)

**Text protocol:** `0x07 0x1A 0x05 block offset count data...`

**Page switch:** `[0x00] [0x07 0x1A 0x02 page] + padding` — OFF=0 CUSTOM=2 SCROLL=6

**Key remap:** `[0x00] [0x05] [0x00] [slot_hi] [slot_lo] [func_hi] [func_lo] [0x00 × 26]` = 33 bytes

**HSV quirk:** Brightness < 20/255 with any saturation renders red. All color math clamps to this.

**Flash on update:** The `0xD1 0x30` buffer allocation causes a brief blank on every send. In-place writes were tested and rejected by firmware. Minimized by sending infrequently and timing sends to natural pauses.

---

## Known Issues

- **Discord presence sync** — PRESENCE_UPDATE fires but response payload format varies; online status auto-sync not yet confirmed working
- **Flash on update** — Hardware limitation; TickType dev is considering an optional transition in firmware config

---

## Credits

| | |
|-|-|
| **Claude** | Big Guy |
| **Mikan** | Human Guy |
| **remedy** | Artist Gal |

*2026 · v1.4.0*
