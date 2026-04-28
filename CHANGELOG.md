# DP-104 Display Controller — Changelog

---

## v1.2.5 — 2026-04-27

### New Features

**Page Switch Commands**
- Hardware page switching now works correctly via HID protocol
- `PAGE_OFF` — blanks the display entirely
- `PAGE_CUSTOM` — switches to the pixel animation page (weather/discord/NP)
- `PAGE_SCROLL` — switches to the scrolling text page (NP text)
- CLEAR button now actually blanks the keyboard display instead of just clearing app state

**Debug Menu — Expanded with Tabs**
- Debug menu reorganised into three tabs: ⛅ Weather, ♪ Now Playing, 🎮 Discord
- **Weather tab**: animation selector, wind speed slider, temperature override (unchanged)
- **Now Playing tab**: source icon dropdown (all 16 sources), play/pause toggle, EQ on/off toggle, send to keyboard
- **Discord tab**: skin key selector (all 16 combinations with descriptions), Preview button (updates Discord tab preview), Send to Keyboard button

**Discord VC Preview**
- Live 24×8 pixel preview on the Discord tab showing the current skin frame
- State label shows mic/deaf/online status with emoji indicators
- Debug tab can preview any skin key without being connected to Discord

**Skin Folder Warning**
- GUI checks for `skins\default\` on startup
- Red warning shown in Discord tab if folder missing or fewer than 12 PNG files found

**Now Playing Custom Display — Enhancements**
- Source detection now uses Windows app ID (not song title) for correct icon matching
- Pixel preview added to the Now Playing tab
- "Custom display" checkbox — uncheck to use text-scroll-only mode

**Status Bar Timestamps**
- One-line status bar now shows last-send timestamps per service: `wx:HH:MM:SS · np:HH:MM:SS · disc:HH:MM:SS`
- "Next weather refresh" countdown moved from status bar to the weather tab (next to "Last updated:")

---

### Changes & Fixes

**Priority Queue — Keyboard Crash Fix**
- Replaced the threading lock with a true priority queue (`_PixelQueue`)
- Single background worker serialises all pixel sends with a 4-second cooldown between sends
- Priority: Discord VC (1) > Now Playing custom (2) > Weather (3)
- Higher-priority sends drop any pending lower-priority queued send
- Eliminates keyboard firmware crashes caused by concurrent HID packet streams

**Discord VC Integration — Major Fixes**
- `DiscordDisplay` removed from GUI path entirely — was opening a second HID device concurrently and crashing the keyboard
- GUI now uses `DiscordIPC` directly; all sends go through the priority queue
- `on_ready` callback fires when OAuth2 auth completes and the poll loop starts (not when the object is created)
- Status label now shows accurate stages: "Authenticating..." → "Connected" → "In VC"
- After 30 seconds of authenticating, label switches to "Connected (verifying...)" so the user knows it's still working
- `_disc_on_state` no longer incorrectly sets `_discord_in_vc = True` — VC presence is now tracked exclusively via `GET_SELECTED_VOICE_CHANNEL` polled every 2 seconds
- `on_vc_join` / `on_vc_leave` callbacks fire correctly when entering/leaving a voice channel
- Fallback display (Weather or Now Playing) now triggers automatically on VC leave
- Manual online status (Online/Away/DnD/Invisible) preserved across reconnects
- `_check_idle` (idle→Away detection) no longer overrides DnD or Invisible — those are always manual

**Weather Tab Color States — Fixed**
- 🟢 Green — weather enabled, Discord disabled
- 🟡 Yellow — Discord enabled, not in VC (weather showing, will be overwritten when VC starts)  ← **was staying Orange**
- 🟠 Orange — Discord enabled, actively in VC (weather suppressed)
- 🔴 Red — weather disabled

**Weather Priority — Fixed**
- Weather no longer sends when NP custom display is active or Discord is in VC
- Weather fetches and updates preview but holds the send until it has priority
- `_do_fetch_weather` has a hard gate at the top: returns immediately if weather tab is disabled

**Now Playing**
- `get_now_playing()` now returns `(title, artist, app_id)` — app ID passed to `get_source()` for correct source icon detection
- `_reload_all` and `_force_send` updated to use the three-value return

**Startup Fix**
- `<Unmap>` event was firing during the initial window draw on Windows, immediately sending the window to tray
- Fixed with a `_window_ready` flag — minimize-to-tray only responds after 500ms post-launch

**`APP_VERSION` Constant**
- Single source of truth for version string, used in header label, debug window title, and credits footer
- Version in credits now always matches the GUI version

**Credits**
- remedy added as "Artist Gal" — designed the base Discord VC skins

---

## v1.2.1 — 2026-04-26

### Fixes
- `get_now_playing()` app ID fix — source detection was matching song titles instead of app IDs
- `wx_countdown` starts at full interval on launch (was 0, causing immediate weather send before settings loaded)
- `_do_fetch_weather` hard gate: checks `wx_enabled` before running — weather was sending even when disabled
- NP custom pixel page: `_np_prev_idx` reset to 0 when new frames built
- Discord auto-connect: won't double-connect if already connected

---

## v1.2.0 — 2026-04-26

### New Features

**Discord VC Tab**
- New Discord tab in the GUI for at-a-glance voice channel status display
- Connects via Discord local IPC — no bot token required
- Mic mute and deafen read automatically; online status set manually in GUI
- One-time OAuth2 authorization; token cached locally after first run
- Skin system: 12 PNG files (24×8, one per state combination) in `skins/default/`
- Idle detection — automatically sets Away after 5 minutes of system inactivity
- Invisible variant auto-generated (greyed status box)

**Now Playing Custom Pixel Display**
- Floating source icon (16 sources) with brand-color background
- Play/pause indicator in random color, bobs at half-phase offset from icon
- 9-bar EQ visualizer — animates while playing, silent when paused
- Text scroll sends before pixel animation
- "Custom display" checkbox

**Priority System**
- Discord VC > NP Custom > Weather for the custom pixel page

**Weather Tab Color States**
- 🟢 Green / 🟡 Yellow / 🟠 Orange / 🔴 Red based on Discord state

---

## v1.1.3 — 2026-04-25

- Debug menu (`~` key): force any weather animation, wind speed override, temperature override
- F1 Credits screen
- Rain slant linear scaling 0–6px over 0–60 mph
- Moon color corrected to lemon yellow
- Shooting star: purple, full-screen diagonal
- All cloud animations use tiled wrapping — no loop pause or gap
- Version label in header

---

## v1.1.2 — 2026-04-24

- FPS dropdown (5 / 10 / 15 / 20 fps)
- Concurrent NP + Weather polling
- Settings auto-save/load (`dp104_settings.json`)
- System tray support; X exits, minimize goes to tray
- Windows toast on successful weather update
- Reconnect/retry: 3 attempts with 2s gap on USB disconnect

---

## v1.1.0 — 2026-04-23 (Initial Release)

- Weather animations: 8 types, day/night routing, temperature gradient
- Wind-driven rain slant
- Now Playing text scroll via Windows media session API
- System tray, live 24×8 pixel preview
- Raw HID pixel protocol reverse-engineered from TickType web configurator

---

*DP-104 Display Controller is an unofficial third-party tool for the TickType DP-104 keyboard.*
*Claude (Big Guy) · Mikan (Human Guy) · remedy (Artist Gal) · 2026*

---

## v1.2.8 — 2026-04-28

### Bug Fixes

**Weather Tab**
- Fixed "Weather queued" stuck status — `on_complete` callback now fires after the keyboard actually ACKs the frame buffer, updating status to "Weather sent ✓" with timestamp
- Fixed `lbl_wx_temp` / `lbl_wx_cond` `AttributeError` — these widgets were never created; now correctly updates `lbl_weather` which does exist
- Fixed `fetch_weather` silently swallowing all exceptions — now returns `{'_error': str(e)}` so the real failure reason appears in the status bar
- Weather no longer auto-fetches at startup before the keyboard is ready — `wx_countdown` starts at 12s
- NP pixel sends held for 8s at startup via `startup_hold` — prevents overloading keyboard on first launch
- Removed `import dp104_weather_v2` reimport inside `_do_fetch_weather` and debug menu — now uses the pre-loaded `_WX_MOD` (fixes "module not found" on Windows where the local folder isn't in `sys.path`)

**Keyboard Crash Fixes**
- `switch_page` was sending 32 bytes instead of 33 — every other HID write in the codebase uses `[0x00] + 32_data_bytes` = 33 total; the malformed packet was crashing firmware
- Removed explicit `switch_page(PAGE_CUSTOM)` call from inside `_send_direct` — keyboard firmware auto-switches to CUSTOM page on receiving a valid pixel animation; the extra call was opening a second concurrent HID session and crashing

**Status Bar**
- Main status message now auto-clears after 5 seconds — only service timestamps remain (`wx:HH:MM:SS · np:HH:MM:SS · disc:HH:MM:SS`)
- Errors (`failed`, `error`) are exempt from auto-clear so the user sees them
- Disabled tabs no longer show a timestamp entry in the status bar

**Now Playing**
- `on_complete` callbacks added to all NP pixel sends — status shows "NP sent ✓ [source]" after keyboard ACK instead of remaining on "sending"
- `np_on` / `wx_on` bool checks fixed — `not self.np_enabled or self.np_enabled.get()` evaluated True when `np_enabled` was None; now `bool(self.np_enabled and self.np_enabled.get())`

**Discord**
- `on_complete` callbacks added to all Discord skin sends
- Removed `GET_SELECTED_VOICE_CHANNEL` from the poll loop — it requires an extra OAuth scope (`rpc`) that we don't have, was returning `ERROR 4006` and causing a reconnect loop every 20-25 seconds
- Non-fatal Discord IPC errors no longer break the poll loop — only `4006` (auth expired) triggers a reconnect

**GUI / Window**
- Tray restore fixed — `_show_window` now uses `event_generate('<<ShowWindow>>', when='tail')`, the only tkinter call documented as thread-safe from non-main threads; previous `root.after(0, ...)` was silently failing from pystray's Windows message loop thread
- `_do_show_window` handles the actual deiconify/lift/focus entirely on the main thread, avoiding a cross-thread deadlock with pystray
- Discord tab layout: NP section now above preview widget
- Discord credentials (Client ID + Secret) collapse on connect, re-expand on disconnect; "Show credentials" button includes a security warning dialog
- Client Secret now saved to `dp104_settings.json` and loaded on startup — no longer required after first use
- `APP_VERSION` duplicate definition at bottom of file removed — single source at line 177

---

## v1.2.6 — 2026-04-28

### Bug Fixes
- Fixed `switch_page` first introduced with wrong packet count (32 bytes, needs 33)
- Weather auto-fetch countdown starts correctly at configured interval after first fetch
- `_do_show_window` added for thread-safe tray restore (partial — fully fixed in v1.2.8)
- Discord `_check_idle` no longer overrides manually-set Away status
- `_manual_status_set` flag added to `DiscordIPC` — tracks what the user explicitly chose so idle detection respects it
- Discord preview widget added to Discord tab
- "Next weather refresh" countdown moved from status bar to weather tab (next to "Last updated:")
- `_update_wx_countdown` method added

---
