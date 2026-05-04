# DP-104 Display Controller

A custom display controller for the **TickType DP-104** mechanical keyboard — a 24×8 RGB LED matrix embedded in the keyboard chassis.

Built through reverse engineering the keyboard's Raw HID protocol from USB traffic captures of the official TickType web configurator.

---

## Features

### 🌤 Weather
- 8 animated weather types — Sunny, Partly Cloudy, Cloudy/House, Rainy, Snowy, Thunderstorm, Night Clear, Night Partly Cloudy
- Day/night routing via real sunrise/sunset data
- Temperature color gradient (blue → red), high/low of day, wind-driven rain slant
- Auto-refresh every 30 minutes by default (configurable)

### ♪ Now Playing
- Pulls current track from any Windows media session (Spotify, browsers, VLC, etc.)
- Custom pixel page — source icon (16 sources), brand-color background, 9-bar EQ visualizer
- Text scroll always sends first; pixel page follows
- **Change-only mode** — pixel display only updates when the source app actually changes (tab turns purple when active)

### 🎮 Discord VC
- Mic mute and deafen read automatically from Discord local IPC
- Online status (Online / Away / DnD / Invisible) set manually in the GUI
- Idle detection — auto-sets Away after 5 min inactivity; never overrides DnD/Invisible
- One-time OAuth2 authorization; token cached
- Skin system — 12 PNGs (24×8) in `skins/default/`
- **When not in VC show:** Weather · Now Playing · WPM · Clock · None

### ⌨ WPM Tracker
- Rolling 60-second keystroke average, displayed as 10-bar history graph + current WPM number
- Color relative to personal best: Green (casual) → Yellow (average) → Red (pushing)
- **Three send modes:**
  - ⏱ Timer — send every X seconds
  - ✋ Pause detection — send when WPM drops ≥30% from recent peak
  - 🔀 Both — pause-priority with minute-interval fallback
- Personal best cached to `dp104_wpm_pb.json`

### 🕐 World Clock
- Configurable city list (label + IANA timezone)
- F-key cycling (F13–F24) to advance through cities — key configurable per-session
- **Three display modes:**
  - ⌚ Still — one frame per minute at top of minute; auto-remaps Red→F13 and Pause→LcdChangeScr with dismissable alert
  - ⏱ Seconds — live seconds progress bar, re-sent every 15 seconds; no auto-remap
  - ✨ Blink — 10-frame animated colon (5 on / 5 off) at 10fps
- Day/night color: amber (day) · orange (dusk/dawn) · blue (night)

### 🖥 GUI
- Live 24×8 pixel previews on all five tabs
- Tab color states: 🟢 enabled · 🔴 disabled · 🟣 NP change-only · 🟡 Discord no-VC · 🟠 Discord in-VC
- Priority queue: Discord (1) > NP (2) > Weather (3) > WPM (4) > Clock (5)
- Shared HID lock — all sends serialized, no concurrent device access
- 4-second cooldown between sends
- Debug menu (`~`) — weather, NP, Discord test controls + key remap buttons
- Settings persistence (`dp104_settings.json`)
- System tray — TRAY button minimizes; X exits

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

**Or** — build a standalone `.exe` with `build.bat` (no Python required for end users).

---

## Files

| File | Purpose |
|------|---------|
| `dp104_gui.pyw` | Main GUI |
| `dp104_weather_v2.py` | Weather animations |
| `dp104_nowplaying.py` | Now Playing pixel display |
| `dp104_discord.py` | Discord IPC + OAuth2 + skin loader |
| `dp104_wpm.py` | WPM tracker |
| `dp104_worldclock.py` | World clock — frame builder + F-key cycling |
| `skins/default/*.png` | Discord VC skins (12 PNGs, 24×8) |
| `build.bat` / `dp104.spec` | PyInstaller build tools |
| `dp104_hid_sniffer.py` | HID packet logger (dev tool) |
| `dp104_inplace_test.py` | In-place buffer test (dev tool) |

---

## Discord Setup

1. [discord.com/developers/applications](https://discord.com/developers/applications) → New Application
2. OAuth2 → Redirects → add `http://127.0.0.1`
3. Copy Client ID + Client Secret
4. GUI Discord tab → paste both → Connect
5. Authorize in the **Discord desktop client** (not browser)
6. Token cached — client secret only needed once

### Skin naming

`{mic}{status}{deaf}.png` — e.g. `ggg.png`, `ryr.png`

| Position | Key | Values |
|----------|-----|--------|
| Mic | 1st | `g`=unmuted `r`=muted |
| Status | 2nd | `g`=online `y`=away `r`=DnD |
| Deafen | 3rd | `g`=undeafened `r`=deafened |

Invisible variants auto-generated.

---

## How It Works

**HID interface:** `VID=0xE560 PID=0xE104 MI_01`

**Pixel protocol:**
- `0xD1 0x30` — allocate animation buffer (causes brief display blank)
- `0xD1 0x31` — stream 25-byte pixel chunks (HSV, not RGB)
- 4-byte global offset, 320ms inter-frame gap

**Text protocol:** `0x07 0x1A 0x05 block offset count data...`

**Page switch:** `0x07 0x1A 0x02 [page]` — OFF=0 CUSTOM=2 SCROLL=6

**HSV quirk:** Brightness < 20/255 with any saturation renders red on firmware. All color math clamps accordingly.

---

## Known Issues

- **Flash on update** — The `0xD1 0x30` buffer allocation causes a brief blank on every send. In-place writes (without the header) were tested and rejected by firmware. Flash masked by send timing — still/once-per-minute modes minimize this.
- **Key remap buttons** — Sniffer captured `KBD→PC` acknowledgement packets but not the `PC→KBD` remap command. Actual command format TBD pending USBPcap capture.
- **Discord presence sync** — PRESENCE_UPDATE subscription works but Discord's RPC response format for online status varies; auto-sync of Online/Away/DnD/Invisible is a known remaining issue.

---

## Credits

| | |
|-|-|
| **Claude** | Big Guy |
| **Mikan** | Human Guy |
| **remedy** | Artist Gal |

*2026 · v1.3.5*
