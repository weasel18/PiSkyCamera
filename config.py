import json
import os
import threading

CONFIG_FILE = os.path.join(os.path.dirname(__file__), "camera_settings.json")

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
}


class Config:
    def __init__(self):
        self._lock = threading.RLock()
        self._data = DEFAULTS.copy()
        self._load()

    def _load(self):
        if os.path.exists(CONFIG_FILE):
            try:
                with open(CONFIG_FILE) as f:
                    saved = json.load(f)
                with self._lock:
                    self._data.update(saved)
            except Exception:
                pass

    def save(self):
        with self._lock:
            data = self._data.copy()
        with open(CONFIG_FILE, "w") as f:
            json.dump(data, f, indent=2)

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
