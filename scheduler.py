"""
Background scheduler: switches camera presets at sunrise/sunset.
Uses the NOAA solar position algorithm — no external dependencies.
"""
import math
import threading
import time
import logging
from datetime import datetime, timezone, timedelta, date as date_t

logger = logging.getLogger(__name__)

# ── NOAA sunrise/sunset ───────────────────────────────────────────────────────

def sun_times(lat: float, lon: float, d: date_t | None = None,
              zenith_deg: float = 90.833) -> tuple:
    """
    Return (sunrise, sunset) as UTC datetime objects for date d (today if None).
    zenith_deg: 90.833 standard | 96 civil twilight | 102 nautical | 108 astronomical
    Either value may be None for polar day/night.
    """
    if d is None:
        d = datetime.now(timezone.utc).date()
    doy = d.timetuple().tm_yday
    gamma = 2 * math.pi / 365 * (doy - 1 + 12 / 24)
    eqtime = 229.18 * (0.000075 + 0.001868 * math.cos(gamma)
                       - 0.032077 * math.sin(gamma)
                       - 0.014615 * math.cos(2 * gamma)
                       - 0.04089 * math.sin(2 * gamma))
    decl = (0.006918 - 0.399912 * math.cos(gamma)
            + 0.070257 * math.sin(gamma)
            - 0.006758 * math.cos(2 * gamma)
            + 0.000907 * math.sin(2 * gamma)
            - 0.002697 * math.cos(3 * gamma)
            + 0.00148 * math.sin(3 * gamma))
    lat_r = math.radians(lat)
    cos_h = ((math.cos(math.radians(zenith_deg))
              - math.sin(decl) * math.sin(lat_r))
             / (math.cos(decl) * math.cos(lat_r)))
    if cos_h > 1 or cos_h < -1:
        return None, None
    H = math.degrees(math.acos(cos_h))
    noon = 720 - 4 * lon - eqtime
    base = datetime(d.year, d.month, d.day, tzinfo=timezone.utc)
    return (base + timedelta(minutes=noon - H * 4),
            base + timedelta(minutes=noon + H * 4))


# ── Scheduler ─────────────────────────────────────────────────────────────────

class Scheduler:
    def __init__(self):
        self._running = False
        self._thread = None
        self._last_period = None

    def start(self):
        self._running = True
        self._thread = threading.Thread(target=self._run, daemon=True, name="scheduler")
        self._thread.start()

    def stop(self):
        self._running = False

    # ── Override API ──────────────────────────────────────────────────────────

    def set_override(self, hours: float):
        from config import config
        config.set("schedule_override_until", time.time() + hours * 3600)
        logger.info("Schedule overridden for %.1fh", hours)

    def set_override_until_sunrise(self):
        from config import config
        lat = config.get("latitude", 0.0)
        lon = config.get("longitude", 0.0)
        now = datetime.now(timezone.utc)
        rise, _ = sun_times(lat, lon)
        if rise is None or rise <= now:
            rise, _ = sun_times(lat, lon, (now + timedelta(days=1)).date())
        if rise:
            config.set("schedule_override_until", rise.timestamp())
            logger.info("Override until sunrise %s UTC", rise.strftime("%H:%M"))
        else:
            self.set_override(12)

    def clear_override(self):
        from config import config
        config.set("schedule_override_until", 0)
        logger.info("Schedule override cleared")

    # ── Status ────────────────────────────────────────────────────────────────

    def status(self) -> dict:
        from config import config
        cfg = config.all()
        lat = cfg.get("latitude", 0.0)
        lon = cfg.get("longitude", 0.0)
        now = datetime.now(timezone.utc)

        soff = cfg.get("schedule_sunset_offset", 30)
        roff = cfg.get("schedule_sunrise_offset", 30)
        raw = _raw_sun_events(now, lat, lon)
        events = _sun_events(now, lat, lon, soff, roff)
        period, _, (nxt_ts, nxt_period) = _classify_now(events, now)
        rise, sset = _today_sun(raw, now)
        night_start = sset + timedelta(minutes=soff) if sset else None
        day_start   = rise - timedelta(minutes=roff)  if rise else None

        override_until = cfg.get("schedule_override_until", 0)
        override_active = override_until > time.time()

        def epoch(dt):
            return int(dt.timestamp()) if dt else None

        return {
            "enabled":          cfg.get("schedule_enabled", False),
            "current_period":   period,
            "day_preset":       cfg.get("schedule_day_preset", "day"),
            "night_preset":     cfg.get("schedule_night_preset", "deepsky"),
            "sunrise_epoch":    epoch(rise),
            "sunset_epoch":     epoch(sset),
            "night_start_epoch": epoch(night_start),
            "day_start_epoch":  epoch(day_start),
            "next_transition_epoch": epoch(nxt_ts),
            "next_period":      nxt_period,
            "override_active":  override_active,
            "override_until_epoch": int(override_until) if override_active else None,
        }

    # ── Background loop ───────────────────────────────────────────────────────

    def _run(self):
        while self._running:
            try:
                self._tick()
            except Exception as e:
                logger.error("Scheduler tick error: %s", e)
            time.sleep(60)

    def _tick(self):
        from config import config
        from camera_service import camera_service
        cfg = config.all()

        if not cfg.get("schedule_enabled", False):
            return
        if cfg.get("schedule_override_until", 0) > time.time():
            return  # manual override active
        lat = cfg.get("latitude", 0.0)
        lon = cfg.get("longitude", 0.0)
        if lat == 0.0 and lon == 0.0:
            return  # location not configured

        now = datetime.now(timezone.utc)
        soff = cfg.get("schedule_sunset_offset", 30)
        roff = cfg.get("schedule_sunrise_offset", 30)
        events = _sun_events(now, lat, lon, soff, roff)
        period, _, _ = _classify_now(events, now)
        if period == self._last_period:
            return

        first_tick = self._last_period is None
        self._last_period = period
        if first_tick:
            # Just woke up — record the current period without applying a preset
            # so we don't clobber manually set settings on every restart.
            logger.info("Schedule: initialized in %s period (no preset applied)", period)
            return

        preset_key = "schedule_night_preset" if period == "night" else "schedule_day_preset"
        preset_name = cfg.get(preset_key, "deepsky" if period == "night" else "day")
        preset_entry = cfg.get("presets", {}).get(preset_name)
        preset_values = preset_entry.get("values", {}) if preset_entry else {}
        if preset_values:
            config.update(preset_values)
            needs_rtsp = any(k in ("stream_fps", "sub_fps") for k in preset_values)
            needs_cam_restart = (
                "exposure_mode" in preset_values or
                ("exposure_time" in preset_values and int(preset_values["exposure_time"]) > 1_000_000)
            )
            if needs_rtsp:
                from rtsp_feeder import rtsp_feeder
                rtsp_feeder.restart()
                camera_service.restart()
            elif needs_cam_restart:
                camera_service.restart()
            else:
                camera_service.apply_settings()
            logger.info("Schedule: %s → %s preset", period, preset_name)
        else:
            logger.warning("Schedule: %s preset '%s' not found, skipping",
                           period, preset_name)


def _raw_sun_events(now, lat, lon):
    """Yield (datetime, 'rise'|'sset') across yesterday/today/tomorrow UTC.

    Spanning three UTC dates is necessary because sunset for far-west
    longitudes (UTC-6, UTC-7) lands on the next UTC date, so a single-day
    computation classifies local evening as 'before today's sunrise' and
    incorrectly flips to night up to ~6 hours early.
    """
    today = now.date()
    events = []
    for delta in (-1, 0, 1):
        d = today + timedelta(days=delta)
        rise, sset = sun_times(lat, lon, d)
        if rise: events.append((rise, "rise"))
        if sset: events.append((sset, "sset"))
    events.sort()
    return events


def _sun_events(now, lat, lon, soff_min, roff_min):
    """Boundary events (offsets applied) labelled with the period they start."""
    return sorted(
        (dt - timedelta(minutes=roff_min) if k == "rise" else dt + timedelta(minutes=soff_min),
         "day" if k == "rise" else "night")
        for dt, k in _raw_sun_events(now, lat, lon)
    )


def _classify_now(events, now):
    """Return (current_period, last_event_dt, (next_event_dt, next_period))."""
    past   = [e for e in events if e[0] <= now]
    future = [e for e in events if e[0] >  now]
    cur     = past[-1][1] if past else "day"
    last_dt = past[-1][0] if past else None
    nxt     = future[0]   if future else (None, None)
    return cur, last_dt, nxt


def _today_sun(raw_events, now):
    """Pick raw sunrise/sunset bracketing the current local-day window.

    During day: most recent past sunrise + next upcoming sunset.
    During night: most recent past sunset + next upcoming sunrise.
    """
    rises = [dt for dt, k in raw_events if k == "rise"]
    ssets = [dt for dt, k in raw_events if k == "sset"]
    rise_past = [dt for dt in rises if dt <= now]
    sset_past = [dt for dt in ssets if dt <= now]
    rise_fut  = [dt for dt in rises if dt >  now]
    sset_fut  = [dt for dt in ssets if dt >  now]
    rise = (rise_past[-1] if rise_past else (rise_fut[0] if rise_fut else None))
    sset = (sset_fut[0]   if sset_fut  else (sset_past[-1] if sset_past else None))
    return rise, sset


scheduler = Scheduler()
