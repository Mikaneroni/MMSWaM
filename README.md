# DP-104 Display Controller

A custom display controller for the **TickType DP-104** mechanical keyboard — a 24×8 RGB LED matrix embedded in the keyboard chassis.

Built through reverse engineering the keyboard's Raw HID protocol from USB traffic captures of the official TickType web configurator.

---

## Features

### 🌤 Weather
- 8 animated weather types — Sunny, Partly Cloudy, Cloudy/House, Rainy, Snowy, Thunderstorm, Night Clear, Night Partly Cloudy
- Day/night routing, temperature gradient, wind-driven rain slant
- Doubled frame counts on all animated types for smoother motion
- Auto-refresh configurable (default 30 min)

### ♪ Now Playing
- Pulls current track from any Windows media session (Spotify, browsers, VLC, etc.)
- 16 source icons, brand-color backgrounds, 9-bar EQ visualizer
- **Change-only mode** — only updates when the source app changes (tab turns purple)
- Text scroll sends first; pixel page follows after 4.5s queue cooldown

### 🎮 Discord VC
- Mic mute and deafen read from Discord local IPC
- Manual online status (Online / Away / DnD / Invisible) — idle detection never overrides DnD/Invisible
- One-time OAuth2 authorization; token cached
- Skin system — 12 PNGs (24×8) in `skins/default/`
- **When not in VC show:** Weather · Now Playing · WPM · Clock · None

### ⌨ WPM / APM Tracker — Three Screens

| Symbol | Screen | Description |
|--------|--------|-------------|
| ● | **Live** | 10-bar rolling minute graph + current value |
| ▬ | **History** | 24-bar session history chart, PB session marked |
| ★ | **Stats** | All-time PB (left) + All-time average (right) |

- WPM = keystrokes ÷ 5 · APM = keystrokes + mouse clicks
- APM checkbox flips all labels, graph history, and keyboard display
- F-key cycles screens (F13–F24, configurable)
- Tab button shows current screen symbol: `⌨ WPM ●`
- Auto-remaps Red→F13 and Pause→LcdChangeScr on start; restores on stop/exit
- Personal best, session history, and rolling average persist to `dp104_wpm_pb.json`
- History scale: last 5/10/15/20/24 sessions or days

### 🕐 World Clock
- Configurable city list — label + IANA timezone + named color
- 10 colors (Red/Orange/Amber/Yellow/Green/Cyan/Blue/Purple/Pink/White)
- F-key cycling through cities — immediate send on press
- **Three display modes:** ⌚ Still · ⏱ Seconds · ✨ Blink (animated colon)
- STILL mode auto-remaps Red→F13, Pause→LcdChangeScr; restores on stop/exit

### 🖥 GUI
- Live 24×8 pixel previews on all five tabs
- **Drag-to-reorder tabs** — leftmost = highest priority
- **📌 Pin tab** — forces current tab to priority 0 (beats all others)
- **↺ Resume on start** — auto-restarts WPM, Clock, Discord 3s after launch
- **⊞ STARTUP** — Start with Windows toggle (green = on, red = off)
- Tab symbols: 🟢 enabled · 🔴 disabled · 🟣 NP change-only · 🟡 Discord no-VC · 🟠 Discord in-VC
- SEND NOW dispatches correctly for all five tabs
- Debug menu (`~`) — test controls + key remap buttons
- System tray watchdog — rebuilds icon if Explorer restarts
- Settings: 27 keys saved and loaded symmetrically

---

## Requirements

- Windows 10 or 11
- Python 3.12+
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

Or build a standalone `.exe` with `build.bat`.

---

## Files

| File | Purpose |
|------|---------|
| `dp104_gui.pyw` | Main GUI |
| `dp104_weather_v2.py` | Weather animations |
| `dp104_nowplaying.py` | Now Playing — 16 source icons, EQ |
| `dp104_discord.py` | Discord IPC + OAuth2 + skin loader |
| `dp104_wpm.py` | WPM/APM tracker — 3 screens, session history |
| `dp104_worldclock.py` | World clock — frame builder + F-key cycling |
| `skins/default/*.png` | Discord VC skins (12 PNGs, 24×8) |
| `dp104_icon.ico` | Application icon |
| `build.bat` / `dp104.spec` | PyInstaller build tools |
| `dp104_hid_sniffer.py` | HID packet logger (dev tool) |
| `webhid_sniffer.html` | WebHID browser sniffer |
| `dp104_settings.json` | Auto-generated — all settings |
| `dp104_wpm_pb.json` | Auto-generated — WPM/APM PB, history, averages |
| `.discord_token` | Auto-generated — OAuth2 token cache |

---

## Discord Setup

1. [discord.com/developers/applications](https://discord.com/developers/applications) → New Application
2. OAuth2 → Redirects → add `http://127.0.0.1`
3. Copy Client ID + Client Secret → GUI Discord tab → Connect
4. Authorize in the **Discord desktop client**
5. Token cached — client secret only needed once

### Skin naming: `{mic}{status}{deaf}.png`

| Position | `g` | `r` / `y` |
|----------|-----|-----------|
| Mic | unmuted | muted |
| Status | online | `r`=DnD · `y`=away |
| Deafen | undeafened | deafened |

---

## Key Remap (Debug Menu)

Packet format: `[0x00][0x05][0x00][slot_hi][slot_lo][func_hi][func_lo][0x00×26]` = 33 bytes

| Button | Options |
|--------|---------|
| Red button | F13 ↔ LcdChangeScr |
| Pause key | Pause ↔ LcdChangeScr |

WPM tracker and Clock STILL mode both auto-remap and auto-restore.

---

## How It Works

**HID interface:** `VID=0xE560 PID=0xE104` — pixel sends use **MI_01** (interface_number=1).

**Pixel protocol:** `0xD1 0x30` (allocate buffer) → `0xD1 0x31` chunks (25 bytes, HSV not RGB)

**Page switch:** `[0x00][0x07 0x1A 0x02 page]` — OFF=0, CUSTOM=2, SCROLL=6

**Priority queue:** Drag-reorderable tabs, leftmost = priority 1. Pinned tab = priority 0.

**HSV quirk:** Brightness < 20/255 with any saturation renders red.

---

## Known Issues

- **Discord presence sync** — PRESENCE_UPDATE fires but online status auto-sync not yet confirmed working
- **Flash on update** — `0xD1 0x30` buffer allocation causes a brief blank on every send; hardware limitation

---

## Credits

| | |
|-|-|
| **Claude** | Big Guy |
| **Mikan** | Human Guy |
| **remedy** | Artist Gal |

*2026 · v1.5.0*
