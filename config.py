import json
import logging
import os
import threading

logger = logging.getLogger(__name__)

CONFIG_FILE = os.path.join(os.path.dirname(__file__), "camera_settings.json")
CONFIG_TMP  = CONFIG_FILE + ".tmp"
CONFIG_BAK  = CONFIG_FILE + ".bak"

DEFAULTS = {
    "exposure_mode": "auto",
    "exposure_time": 100_000,       # microseconds (100ms)
    "exposure_value": 0.0,          # EV offset for auto mode (-3 to +3 stops)
    "ae_constraint_mode": 0,        # 0=Normal 1=Highlights 2=Shadows
    "analogue_gain": 1.0,
    "awb_mode": "auto",
    "colour_gain_r": 2.0,
    "colour_gain_b": 1.5,
    "noise_reduction_mode": 0,  # 0=Off 1=Fast 2=HighQuality 3=Minimal
    "brightness": 0.0,
    "contrast": 1.0,
    "saturation": 1.0,
    "sharpness": 1.0,
    "resolution": [3840, 2160],
    "sub_resolution": [1280, 720],
    "sub_fps": 10,
    "hflip": False,
    "vflip": False,
    "stream_fps": 10,
    "app_port": 8080,
    "rtsp_port": 8554,
    "device_name": "piSkyCam",
    "onvif_username": "admin",
    "onvif_password": "admin",
    # Fisheye lens
    "fisheye_lens": False,
    "fisheye_fov": 180,
    # Sunrise/sunset schedule
    "schedule_enabled": False,
    "latitude": 0.0,
    "longitude": 0.0,
    "schedule_day_preset": "day",
    "schedule_night_preset": "deepsky",
    "schedule_sunset_offset": 30,    # minutes AFTER sunset → switch to night
    "schedule_sunrise_offset": 30,   # minutes BEFORE sunrise → switch back to day
    "schedule_override_until": 0,    # Unix timestamp; 0 = no override
    # User-editable preset library. Each entry: {label, values}.  `values` is a
    # dict of camera settings applied when the preset button is clicked or when
    # the scheduler flips period.  Seeded with sensible defaults on first run.
    "presets": {
        "day":     {"label": "Day Auto",
                    "values": {"exposure_mode": "auto", "awb_mode": "auto",
                               "stream_fps": 10, "analogue_gain": 1.0,
                               "noise_reduction_mode": 2, "exposure_value": 0.0,
                               "ae_constraint_mode": 0}},
        "night":   {"label": "Night Auto",
                    "values": {"exposure_mode": "auto", "awb_mode": "auto",
                               "stream_fps": 5, "analogue_gain": 4.0,
                               "noise_reduction_mode": 1, "exposure_value": -0.75,
                               "ae_constraint_mode": 1}},
        "planets": {"label": "Planets",
                    "values": {"exposure_mode": "manual", "awb_mode": "auto",
                               "stream_fps": 10, "analogue_gain": 4.0,
                               "noise_reduction_mode": 0, "exposure_time": 50_000,
                               "colour_gain_r": 2.0, "colour_gain_b": 1.5}},
        "deepsky": {"label": "Deep Sky",
                    "values": {"exposure_mode": "manual", "awb_mode": "manual",
                               "stream_fps": 1, "analogue_gain": 8.0,
                               "noise_reduction_mode": 0, "exposure_time": 30_000_000,
                               "colour_gain_r": 2.2, "colour_gain_b": 1.6}},
        "trails":  {"label": "Star Trails",
                    "values": {"exposure_mode": "manual", "awb_mode": "manual",
                               "stream_fps": 1, "analogue_gain": 4.0,
                               "noise_reduction_mode": 0, "exposure_time": 15_000_000,
                               "colour_gain_r": 2.0, "colour_gain_b": 1.5}},
        "longexp": {"label": "Long Exp 120s",
                    "values": {"exposure_mode": "manual", "awb_mode": "manual",
                               "stream_fps": 1, "analogue_gain": 16.0,
                               "noise_reduction_mode": 0, "exposure_time": 120_000_000,
                               "colour_gain_r": 2.2, "colour_gain_b": 1.6}},
    },
}


class Config:
    def __init__(self):
        self._lock = threading.RLock()
        self._data = DEFAULTS.copy()
        self._load()

    def _load(self):
        if not os.path.exists(CONFIG_FILE):
            return
        try:
            with open(CONFIG_FILE) as f:
                saved = json.load(f)
            with self._lock:
                self._data.update(saved)
        except Exception as e:
            # Don't silently lose user settings — preserve the corrupt file as
            # .bak so it can be inspected, then fall back to defaults.
            logger.warning("Config load failed (%s); preserving as %s and using defaults",
                           e, CONFIG_BAK)
            try:
                os.replace(CONFIG_FILE, CONFIG_BAK)
            except OSError:
                pass

    def save(self):
        with self._lock:
            data = self._data.copy()
            # Atomic write: tmp file + fsync + rename. A power loss between the
            # write and rename leaves the prior valid config intact.
            with open(CONFIG_TMP, "w") as f:
                json.dump(data, f, indent=2)
                f.flush()
                os.fsync(f.fileno())
            os.replace(CONFIG_TMP, CONFIG_FILE)

    def get(self, key, default=None):
        with self._lock:
            return self._data.get(key, default)

    def set(self, key, value):
        with self._lock:
            self._data[key] = value
            self.save()

    def update(self, d):
        with self._lock:
            self._data.update(d)
            self.save()

    def all(self):
        with self._lock:
            return self._data.copy()


config = Config()
