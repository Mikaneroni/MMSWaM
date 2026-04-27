# DP-104 Display Controller — Changelog

---

## v1.2.0 — 2026-04-26

### New Features

**Discord VC Tab**
- New Discord tab in the GUI for at-a-glance voice channel status display
- Connects to Discord via local IPC pipe (no bot token required)
- Mic mute and deafen status read automatically from Discord
- Online status selector (Online / Away / DnD / Invisible) — set manually, displayed on keyboard
- One-time OAuth2 authorization flow; token cached locally after first run
- "When not in VC show:" fallback selector — Weather or Now Playing
- Idle detection: automatically sets status to Away after 5 minutes of system inactivity
- Skin system: loads 12 PNG files (24×8, one per state combination) from `skins/default/`
- Invisible variant auto-generated from online variants (greyed status box)
- Reconnects automatically if Discord restarts

**Now Playing Custom Pixel Display**
- Now Playing tab now sends a full pixel animation to the CUSTOM page
  - Floating source icon (Spotify, YouTube, Twitch, WinAMP, foobar2000, TIDAL,
    Apple Music, Amazon Music, VLC, SoundCloud, Pandora, Deezer, Browser, and more)
  - Brand-color tinted background per source
  - Black framing rows above and below the icon (move with bob animation)
  - Play/pause indicator in random color, bobs at half-phase offset from icon
  - 7-bar EQ visualizer — animates while playing, goes silent when paused
- "Custom display" checkbox — uncheck to use text scroll only (reverts to SCROLL page)
- Pixel preview added to the Now Playing tab
- Text scroll always sends before pixel animation (scroll page updates first)

**Priority System for Custom Pixel Page**
- Clear hierarchy for the CUSTOM page: Discord VC > NP Custom > Weather
- Weather fetches and updates preview while NP custom is active, but does not overwrite
- Weather automatically restores to custom page when NP stops or custom display is disabled
- Discord VC skin takes full priority; weather and NP queue behind it

**Weather Tab Color States**
- Weather tab button now reflects Discord integration state:
  - 🟢 Green — weather enabled, Discord tab disabled (normal)
  - 🟡 Yellow — Discord enabled, not in VC (weather showing, will be overwritten when VC starts)
  - 🟠 Orange — Discord enabled and actively in VC (weather suppressed)
  - 🔴 Red — weather disabled entirely

---

### Changes

**Firmware Red-Rendering Bug Fix (Confirmed Threshold)**
- Hardware test confirmed: any pixel with saturation > 0 at val < 20 renders red on firmware
- `dim()` and `bright()` in all three modules now clamp to val=20 minimum for colored pixels
- Additional fix: pixels with hue near 0° (< 5° or > 350°) and low saturation (< 40/255)
  have their saturation stripped to 0 — renders as safe neutral grey instead of red
- Applied consistently across `dp104_weather_v2.py`, `dp104_nowplaying.py`, `dp104_discord.py`

**Discord Skin Loader**
- Anti-aliasing bleed pixels (0 < val < 40) snapped to confirmed-safe grey HSV(0,0,20)
- Near-zero hue / low-sat separator pixels stripped to pure grey to prevent red rendering
- RGBA PNG support (artist files exported with alpha channel)

**Now Playing Pixel Display**
- EQ visualizer extended from 7 to 9 bars (cols 15–23)
- EQ bars use pure white (sat=0) for maximum contrast against any brand-color background
- Play/pause symbol uses a random hue each refresh (only changes when keyboard actually updates)
- Bob animation uses 2-position cosine wave — static bottom black row always visible
- Twitch icon tail corrected to bottom-right (matches actual Twitch logo)
- Spotify circle background fills behind soundwave arcs
- YouTube center white dot added to play triangle

**Tab System**
- Right-click tab to toggle enable/disable (green = on, red = off) now applies to all 3 tabs
- Disabling active tab switches view to the next available enabled tab
- Tab enable/disable states saved and restored on startup

**Settings Persistence**
- Discord client ID, online status, fallback preference, and NP custom toggle
  now saved to `dp104_settings.json` and restored on launch

---

## v1.1.3 — 2026-04-25

### New Features
- Debug menu (`~` key): force any weather animation, wind speed override, temperature override
- Debug menu attached to main window as transient (no always-on-top)
- Wind speed override slider in debug menu (0–60 mph, affects rain slant and snow drift)
- F1 Credits screen
- Temperature override moved from weather panel into debug menu

### Changes
- Rain slant linear scaling 0–6px over full 0–60 mph range (was capped at 3px above 15 mph)
- Moon color corrected to lemon yellow (hue 68°, clearly distinct from orange-red temp text)
- Shooting star: purple color, full-screen diagonal, overwrites moon and stars
- Night partly cloudy: white cloud (sat=0), blue star twinkles visible through cloud
- Thunderstorm: pure white cloud flash on both lightning bolts
- All cloud animations (partly cloudy, cloudy/house, night partly cloudy) use tiled wrapping
  — cloud exits right edge and immediately re-enters from left with no pause or gap
- Cloudy/house: blue separator at col 10, clouds confined to icon zone
- Frame counts: partly cloudy 20f, cloudy 20f, thunderstorm 20f, night partly cloudy 30f
- Version label in header

---

## v1.1.2 — 2026-04-24

### New Features
- FPS dropdown in weather preview header (5 / 10 / 15 / 20 fps)
- Concurrent NP + Weather polling regardless of active tab
- Settings auto-save/load (`dp104_settings.json`): location, poll interval, auto-refresh,
  FPS, active tab
- X button exits completely; minimize button and Tray button go to system tray
- Windows toast notification on successful weather update
- Temperature override (debug)
- Reconnect/retry: 3 attempts with 2s gap on USB disconnect
- `_wx_sending` stuck guard: auto-releases after 120s

### Changes
- urllib `timeout=12` added to weather fetch
- Single H or Low display in right zone using TALL7 font (day → High, night → Low)
- Preview synced to FPS setting
- Status bar shows contextual message for both services
- Weather tab shows NP ticker at bottom

---

## v1.1.0 — 2026-04-23 (Initial Release)

- Weather animations: Sunny, Partly Cloudy, Cloudy (House), Rainy, Snowy,
  Thunderstorm, Night Clear, Night Partly Cloudy
- Day/night routing from sunrise/sunset data
- Temperature color gradient (blue → green → yellow → red)
- Wind-driven rain slant
- Zip code resolution via zippopotam.us
- Now Playing text scroll via Windows media session API
- System tray support
- Live 24×8 pixel preview
- Raw HID pixel protocol (reverse-engineered from TickType web configurator)

---

*DP-104 Display Controller is an unofficial third-party tool for the TickType DP-104 keyboard.*  
*Claude (Big Guy) · Mikan (Human Guy) · 2026*
