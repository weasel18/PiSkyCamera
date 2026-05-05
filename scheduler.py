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

# ── Preset data (mirrors JS PRESETS) ─────────────────────────────────────────

PRESETS = {
    "day":     {"exposure_mode": "auto",   "awb_mode": "auto",   "stream_fps": 10,
                "analogue_gain": 1.0,  "noise_reduction_mode": 2,
                "exposure_value": 0.0,  "ae_constraint_mode": 0},
    "night":   {"exposure_mode": "auto",   "awb_mode": "auto",   "stream_fps": 5,
                "analogue_gain": 4.0,  "noise_reduction_mode": 1,
                "exposure_value": -0.75, "ae_constraint_mode": 1},
    "planets": {"exposure_mode": "manual", "awb_mode": "auto",   "stream_fps": 10,
                "analogue_gain": 4.0,  "noise_reduction_mode": 0,
                "exposure_time": 50_000, "colour_gain_r": 2.0, "colour_gain_b": 1.5},
    "deepsky": {"exposure_mode": "manual", "awb_mode": "manual", "stream_fps": 1,
                "analogue_gain": 8.0,  "noise_reduction_mode": 0,
                "exposure_time": 30_000_000, "colour_gain_r": 2.2, "colour_gain_b": 1.6},
    "trails":  {"exposure_mode": "manual", "awb_mode": "manual", "stream_fps": 1,
                "analogue_gain": 4.0,  "noise_reduction_mode": 0,
                "exposure_time": 15_000_000, "colour_gain_r": 2.0, "colour_gain_b": 1.5},
    "longexp": {"exposure_mode": "manual", "awb_mode": "manual", "stream_fps": 1,
                "analogue_gain": 16.0, "noise_reduction_mode": 0,
                "exposure_time": 120_000_000, "colour_gain_r": 2.2, "colour_gain_b": 1.6},
}

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

        rise, sset = sun_times(lat, lon)
        soff = cfg.get("schedule_sunset_offset", 30)
        roff = cfg.get("schedule_sunrise_offset", 30)
        night_start = sset + timedelta(minutes=soff) if sset else None
        day_start   = rise - timedelta(minutes=roff)  if rise else None

        period = _current_period(now, day_start, night_start)
        nxt_ts, nxt_period = self._next_transition(now, day_start, night_start, lat, lon)

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

    def _next_transition(self, now, day_start, night_start, lat, lon):
        from config import config
        roff = config.get("schedule_sunrise_offset", 30)
        if day_start and now < day_start:
            return day_start, "day"
        if night_start and now < night_start:
            return night_start, "night"
        # Past tonight's night_start — next transition is tomorrow's day_start
        tomorrow = (now + timedelta(days=1)).date()
        rise_t, _ = sun_times(lat, lon, tomorrow)
        if rise_t:
            return rise_t - timedelta(minutes=roff), "day"
        return None, None

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
        rise, sset = sun_times(lat, lon)
        night_start = sset + timedelta(minutes=cfg.get("schedule_sunset_offset", 30)) if sset else None
        day_start   = rise - timedelta(minutes=cfg.get("schedule_sunrise_offset", 30)) if rise else None

        period = _current_period(now, day_start, night_start)
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
        preset_data = PRESETS.get(preset_name)
        if preset_data:
            config.update(preset_data)
            needs_rtsp = any(k in ("stream_fps", "sub_fps") for k in preset_data)
            needs_cam_restart = (
                "exposure_mode" in preset_data or
                ("exposure_time" in preset_data and int(preset_data["exposure_time"]) > 1_000_000)
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


def _current_period(now, day_start, night_start):
    if day_start is None or night_start is None:
        return "day"
    if day_start <= now < night_start:
        return "day"
    return "night"


scheduler = Scheduler()
