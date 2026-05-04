import threading
import time
import logging

logger = logging.getLogger(__name__)


class CameraService:
    def __init__(self):
        self._cond = threading.Condition()
        self._frame = None
        self._frame_ts = 0.0
        self._metadata = {}
        self._running = False
        self._thread = None
        self._apply_event = threading.Event()
        self._restart_event = threading.Event()

    def start(self):
        self._running = True
        self._thread = threading.Thread(target=self._run_loop, daemon=True, name="camera")
        self._thread.start()

    def stop(self):
        self._running = False
        with self._cond:
            self._cond.notify_all()

    def restart(self):
        """Signal camera thread to reinitialize (e.g. after resolution change)."""
        self._restart_event.set()

    def apply_settings(self):
        """Signal camera thread to push new controls without restart."""
        self._apply_event.set()

    def peek_frame(self):
        """Return last frame immediately without blocking."""
        with self._cond:
            return self._frame, self._frame_ts

    def get_metadata(self):
        with self._cond:
            return self._metadata.copy()

    def wait_for_frame(self, last_ts=0.0, timeout=60.0):
        """Block until a frame newer than last_ts is available."""
        with self._cond:
            deadline = time.monotonic() + timeout
            while self._frame_ts <= last_ts and self._running:
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    break
                self._cond.wait(timeout=min(remaining, 1.0))
            return self._frame, self._frame_ts

    # ── internal ──────────────────────────────────────────────────────────────

    def _publish(self, frame, ts, metadata):
        with self._cond:
            self._frame = frame
            self._frame_ts = ts
            self._metadata = metadata
            self._cond.notify_all()

    def _build_controls(self):
        from config import config
        cfg = config.all()
        controls = {}

        stream_fps = max(1, int(cfg.get("stream_fps", 10)))
        stream_us  = 1_000_000 // stream_fps  # frame period at target fps

        if cfg["exposure_mode"] == "manual":
            exp_us = max(100, int(cfg["exposure_time"]))
            controls["AeEnable"] = False
            controls["ExposureTime"] = exp_us
            controls["AnalogueGain"] = float(cfg["analogue_gain"])
            # Frame must last at least as long as the exposure, but no shorter
            # than the target stream period (keeps fast-shutter at stream_fps,
            # not spinning at 250fps).
            frame_us = max(exp_us, stream_us)
            controls["FrameDurationLimits"] = (frame_us, frame_us)
        else:
            controls["AeEnable"] = True
            # Lock frame period to stream_fps so AE picks exposure within that
            # window rather than roaming freely and producing choppy/slow output.
            controls["FrameDurationLimits"] = (stream_us, stream_us)
            # Fix gain at the configured value; on libcamera ≥0.2 this makes AE
            # control only shutter (shutter-priority). Day preset uses 1.0.
            controls["AnalogueGain"] = float(cfg.get("analogue_gain", 1.0))
            # EV compensation: shifts the AE target up/down in stops.
            controls["ExposureValue"] = float(cfg.get("exposure_value", 0.0))

        if cfg["awb_mode"] == "manual":
            controls["AwbEnable"] = False
            controls["ColourGains"] = (
                float(cfg["colour_gain_r"]),
                float(cfg["colour_gain_b"]),
            )
        else:
            controls["AwbEnable"] = True

        if cfg.get("fisheye_lens", False):
            controls["AeMeteringMode"] = 1  # Spot — ignore black vignette corners

        controls["NoiseReductionMode"] = int(cfg.get("noise_reduction_mode", 0))
        controls["Brightness"] = float(cfg.get("brightness", 0.0))
        controls["Contrast"] = float(cfg.get("contrast", 1.0))
        controls["Saturation"] = float(cfg.get("saturation", 1.0))
        controls["Sharpness"] = float(cfg.get("sharpness", 1.0))

        return controls

    def _run_loop(self):
        while self._running:
            try:
                self._run_camera()
            except Exception as e:
                logger.error("Camera error: %s", e, exc_info=True)
                if self._running:
                    time.sleep(5)

    def _run_camera(self):
        from config import config
        from picamera2 import Picamera2

        self._restart_event.clear()
        cfg = config.all()

        from libcamera import Transform
        picam2 = Picamera2()
        try:
            stream_fps = max(1, int(cfg.get("stream_fps", 10)))
            stream_us  = 1_000_000 // stream_fps
            if cfg["exposure_mode"] == "manual":
                exp_us = max(100, int(cfg.get("exposure_time", 100_000)))
                init_max_us = max(exp_us, stream_us)
            else:
                init_max_us = stream_us  # cap AE from the start; no 30s drift

            video_config = picam2.create_video_configuration(
                main={"format": "RGB888", "size": (4056, 3040)},
                transform=Transform(hflip=bool(cfg.get("hflip")), vflip=bool(cfg.get("vflip"))),
                controls={"FrameDurationLimits": (100, init_max_us)},
                buffer_count=2,
            )
            picam2.configure(video_config)

            picam2.start()
            time.sleep(1.5)  # sensor warmup

            picam2.set_controls(self._build_controls())
            time.sleep(0.5)  # controls settle

            logger.info("Camera running at 4056x3040")

            while self._running and not self._restart_event.is_set():
                if self._apply_event.is_set():
                    self._apply_event.clear()
                    picam2.set_controls(self._build_controls())

                request = picam2.capture_request()
                if request is None:
                    time.sleep(0.01)
                    continue

                try:
                    # picamera2 RGB888 is BGR in memory (libcamera quirk); flip to RGB
                    frame = request.make_array("main")[:, :, ::-1].copy()
                    meta = dict(request.get_metadata())
                finally:
                    request.release()

                self._publish(frame, time.time(), meta)

        finally:
            try:
                picam2.stop()
            except Exception:
                pass
            try:
                picam2.close()
            except Exception:
                pass


camera_service = CameraService()
