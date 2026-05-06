# piSkyCam Code Review — 2026-05-06

Comprehensive review across 5 focus areas, 94 findings total. Prioritised
into Critical / High / Medium / Low buckets and a 3-session work plan.

Reviewers (parallel sub-agents):
1. Camera + RTSP pipeline (`camera_service.py`, `rtsp_feeder.py`, `main.py`, `mediamtx.yml`)
2. Flask app & API endpoints (`app.py` non-ONVIF routes, `config.py`)
3. ONVIF Profile S + WS-Discovery (`app.py` ONVIF handlers, `discovery.py`)
4. Frontend (`static/app.js`, `templates/index.html`, `static/style.css`)
5. Scheduler + config + presets (`scheduler.py`, `config.py`, PRESETS dict)

---

## Critical (data loss / security)

| # | File:line | Issue |
|---|---|---|
| C1 | `config.py:63-67` | **Non-atomic config write** — power loss truncates `camera_settings.json` to 0 bytes; on next start `_load`'s bare except silently resets to defaults. All user settings lost. Fix: write `.tmp` + `os.fsync` + `os.replace`. |
| C2 | `app.py:206` | **ONVIF auth fully bypassed when no `Security` header** — `if token_elem is None: return True`. Anyone on the LAN can hit GetStreamUri/Snapshot/etc. without creds. |
| C3 | `app.py:236` | **ONVIF auth fail-open on parse error** — bare except returns True. Malformed SOAP bypasses auth. |
| C4 | `camera_service.py` (ScalerCrop) | **Likely-still-cropped image** — we set `ScalerCrop=(8,16,4056,3040)`, but `PixelArrayActiveAreas` returned `(8,16,4056,3040)` looks like `(x1,y1,x2,y2)` (otherwise it'd extend past the 4056×3040 array). picamera2's ScalerCrop expects `(x,y,w,h)`. So we're requesting a region from (8,16) extending +4056×+3040 — libcamera clamps, still excluding top 16 rows. May explain why user still saw top crop. Test with `(0,0,4056,3040)` literal. |

---

## High impact

| # | File:line | Issue |
|---|---|---|
| H1 | `main.py:77-83` | Shutdown doesn't `join()` camera/RTSP threads or `wait()` mediamtx — port 8554/camera resource may still be held when new process starts. Combine with H2. |
| H2 | `scheduler.py:175`, `rtsp_feeder.py:39` | `time.sleep(60)` and `time.sleep(5)` block on SIGTERM. Replace with `Event.wait(...)` so `stop()` cancels them immediately. |
| H3 | `app.py:580` | `/snapshot.jpg` allocates 37 MB and JPEG-encodes on **every** request, no cache, no rate limit. Easy DoS. Cache last-encoded JPEG keyed on `frame_ts`; serve cached bytes if `frame_ts` unchanged. |
| H4 | `camera_service.py` (~line 202) | `make_array("main")[:, :, ::-1].copy()` does ~370 MB/s memcpy at 10 fps. Feed BGR direct to FFmpeg with `-pix_fmt bgr24` instead — significant CPU win on Pi 5 (helps the heat issue). |
| H5 | `scheduler.py:90-93, 200-206` | Override-expiry doesn't reapply preset — when override clears, `_last_period` matches current period so no preset fires until next dawn/dusk. Camera stays on user's manual settings forever. |
| H6 | `app.js:597-598, 425-429` | `setInterval`s never cleared, EventSource never closed on `pagehide`, SSE reconnects with no backoff. On a slow restart the browser hammers the server. |
| H7 | `static/app.js` (after POST) | UI never re-syncs from server after a POST, so server-side clamps (oversize FPS, lat/lon, etc.) leave the UI showing the wrong value until reload. Have `/api/settings` POST return the clamped dict and the client merges it. |
| H8 | `app.py:474-480` | `GetServiceCapabilities` uses `tt:` namespace where `tds:` is required by the Device WSDL. Strict NVRs reject. |
| H9 | `discovery.py:36, 60, 117` | WS-Discovery missing `Profile/S` scope, `MessageID` is `uuid:…` not `urn:uuid:…`, `RelatesTo` extraction only matches `a:` prefix. Each one breaks discovery on at least some NVRs. |
| H10 | `scheduler.py` PRESETS | `deepsky` preset (30s @ gain 8) will saturate badly under any moonlight; `longexp` (120s @ stream_fps=1) will stall RTSP because the encoder still expects frames every 1s. |

---

## Medium (race conditions, robustness, validation)

- `camera_service.py:43-49` — `apply_settings()` reads `self._picam2` without a lock; potential race with `_run_camera`'s finally. Bare except swallows it but logs nothing.
- `app.py:759-773` — concurrent POSTs to `/api/settings` race; needs a module-level lock around validate→update→apply.
- `app.py:624` — SSE bare `except Exception: yield ": error"` loops forever on programming bugs. Log + break after N consecutive errors.
- `app.py:863-887` — `vcgencmd` forks on every `/api/stats` poll. Cache 2–5 s.
- `app.py:786-797` — `/api/schedule/override` `float(hours)` 500s on bad input, no upper bound. Clamp `0 < hours ≤ 168`.
- `app.py:763-766` — `rtsp_port` in restart-trigger but not in `ALLOWED` set → dead code. Either remove or add with port-range validation.
- `app.py:32` — `local_ip()` `s.close()` in finally raises `UnboundLocalError` if socket creation fails.
- `app.py:734` — `request.json` deprecated; use `request.get_json(silent=True)` and explicitly check for dict.
- `app.py:712` — `_validate_settings` doesn't catch `ValueError` from `int("abc")`/`float("xyz")` — bad input becomes 500 instead of 400.
- `app.py:692-693` — `sub_resolution` accepts unbounded width/height; clamp to a max or restrict to discrete set.
- `app.py:702-703` — Latitude/longitude clamped silently; with `schedule_enabled=true` and lat/lon at default 0,0 the scheduler computes against the prime meridian. Log a warning or require non-zero when enabling schedule.
- `app.py:577` — `from PIL import Image as PILImage` runs inside the request handler on every snapshot. Hoist to module level.
- `config.py:78-81` — `config.update()` writes file outside the lock; concurrent updates can corrupt JSON.
- `config.py:53-61` — silent `except Exception: pass` masks corruption. Log a warning and rename corrupt file to `.bak` before resetting.
- `rtsp_feeder.py:140` — `proc.stdin.write()` can block indefinitely if FFmpeg/mediamtx stalls; `_restart_event` won't fire.
- `rtsp_feeder.py:120` — `_drain_stderr` thread leaks on FFmpeg restart cycles.
- `rtsp_feeder.py:125-147` — duplicate frames written at stream_fps when no new camera frame; ~37 MB/duplicate fed to encoder. Use `wait_for_frame()` cadence instead of polling.
- `camera_service.py:25-28` — `stop()` sets `_running=False` but `capture_request()` blocks for the full exposure; on long exposure shutdown takes up to 30 s. Also `set` `_restart_event` in `stop()` to break sooner.
- `camera_service.py:159-164` — at full 4056×3040 the IMX477 caps at ~10 fps; if `stream_fps > 10` is requested the camera silently clamps. Log the clamp or document the ceiling.
- `app.js:421` — `startCountdown` called on every SSE frame (10 Hz); kills/restarts timer causing visible jitter. Only restart on new exposure cycle.
- `app.js:412` — `refreshPreview()` spammed at SSE rate; throttle with an `imgLoading` flag.
- `app.js:381-407` — auto→manual transition via SSE-driven preset switch: `lastFrameTs` is the previous (auto) frame's ts, so `alreadyElapsed` is huge and bar shows 100% / "Processing…". Reset `lastFrameTs = Date.now()/1000` on mode change.
- `app.js:106-110` — `queueSetting` mutates local `settings[key]=value` immediately; on POST failure the local state is out of sync with server. On error, refetch.
- `app.js:336-339` — `refreshPreview` has no `img.onerror` handler; broken-image icon shows on camera 500.
- `scheduler.py:212` — race between scheduler `config.update(preset)` and concurrent `/api/settings` POST. Single shared lock around update+restart.
- `scheduler.py:63-64` — polar day/night returns `(None, None)` indistinguishably; defaults to `"day"` even during polar night. Return a sentinel.
- `scheduler.py:156-159` — `_next_transition` mis-orders if `night_start < day_start` (can happen with offsets near solstice at high latitudes). Sort boundaries.
- `scheduler.py:175` — duplicate listing — sleep blocks `stop()`. Use `Event.wait()`.
- ONVIF `app.py:264` — bitrate hardcoded 8192/2048 kbps; should come from config.
- ONVIF `app.py:288, 365, 404` — sensor bounds hardcoded 4056×3040; centralise.
- ONVIF `app.py:501-504` — `ResolutionsAvailable` hardcoded list ignores actual configured `resolution`.
- ONVIF `app.py:366-368` — Brightness/Saturation/Sharpness raw floats; ONVIF expects Brightness in [0,100]. Clamp/scale on output.
- ONVIF `app.py:54-62` — `soap_wrap` lacks `wsa:` namespace; strict clients (Milestone, some UniFi builds) check for `wsa:Action`/`wsa:RelatesTo`.
- ONVIF `app.py:74-75` — `msg` interpolated raw into XML; if it contains `<` or `&` output is invalid. Use `xml.sax.saxutils.escape`.
- ONVIF `app.py:548` — unknown device action returns DeviceInfo silently; should return SOAP fault.
- ONVIF `app.py:472` — `GetWsdlUrl` returns empty value; some clients fail.
- ONVIF replay protection — no Created freshness window or nonce tracking. Reject if `abs(now - parsed_created) > 300 s`.
- discovery.py:140 — multicast bound to `INADDR_ANY`; on multi-homed Pis (Wi-Fi + Ethernet) join may pick wrong interface.
- discovery.py:130 — no `Bye` on shutdown; NVRs keep stale entries until next discovery cycle.
- discovery.py:55, 90 — only WS-Discovery 2005/04 namespace; some clients send 2009/01 — detect and respond in kind.

---

## Low / polish

- `app.py:223-224` / `config.py:30-31` — log a warning when ONVIF creds are still default `admin/admin`.
- `app.py:234` — `hmac.compare_digest()` for ONVIF digest comparison (timing-attack resistance; minor on LAN).
- `app.js:539` (or wherever `loadScheduleStatus` is called) — schedule status is stale up to 60 s after toggling settings. Call `loadScheduleStatus()` from `setSchedEnabled` and preset handlers.
- Frontend a11y: `:focus-visible` styles, `role="radio"`/`aria-checked` on toggle pairs, `for=` label associations, `aria-label` on icon-only buttons (◀ ▶ ↓).
- `style.css:301-307` — `min-height: 0` on `.preview-container` (Chrome flex-shrink quirk; image may fail to scale on first paint).
- `style.css:336-356` — `.capture-progress` width 0% leaves a visible gap before the label; hide with `display:none` when 0% or absolute-position.
- Scheduler — `_next_transition` "next event" computation; document or test edge cases.
- Config schema versioning + migration hook (`config_version: 1` field).
- Camera service — bare `except` clauses that swallow errors; log at debug level.
- mediamtx — set `publisherOverride: yes` so a restarting FFmpeg reclaims the path immediately.
- main.py — replace `time.sleep(1)`/`time.sleep(2)` warmup with TCP-connect probes.

---

## Recommended work plan

### Session 1 — data integrity + security
- C1, C2, C3, C4, H7
- ~1–2 hours; high payoff, low risk

### Session 2 — responsiveness + perf
- H1, H2, H3, H4, H6
- Restart hangs and Pi 5 thermal load both improve

### Session 3 — NVR compatibility + scheduler robustness
- H5, H8, H9, H10 + medium bucket as time allows

### Backlog
- All Low / polish items, accessibility, schema versioning
