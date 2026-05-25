import os
import threading
import time
import logging

logger = logging.getLogger(__name__)


def _safe_create_picamera2():
    """Construct a Picamera2 without leaking pipe FDs on partial init failure.

    Picamera2.__init__ allocates a notifyme pipe pair early, then probes for
    cameras.  When no camera is attached the probe raises IndexError and the
    partially-constructed object's __del__ hits AttributeError on `_preview`
    before reaching close(), so the pipe FDs never get released.  At the
    default retry cadence that exhausts the process FD pool within minutes.

    Splitting __new__ from __init__ keeps a live reference to the partial
    instance even when __init__ raises, so we can close the pipes ourselves.
    """
    from picamera2 import Picamera2
    obj = object.__new__(Picamera2)
    try:
        Picamera2.__init__(obj)
        return obj
    except Exception:
        for attr in ("notifyme_r", "notifyme_w"):
            fd = getattr(obj, attr, None)
            if isinstance(fd, int) and fd >= 0:
                try:
                    os.close(fd)
                except OSError:
                    pass
                setattr(obj, attr, -1)
        raise


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
        self._picam2 = None  # live reference for immediate set_controls()

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
        """Push new controls as soon as possible.

        Calls set_controls() directly on the live camera if one is running so
        the new values are queued for the very next frame boundary — without
        waiting for the camera loop to come back around (which can be an entire
        exposure-time away on long exposures).  The _apply_event acts as a
        fallback in case the direct call races with a camera restart.
        """
        picam2 = self._picam2
        if picam2 is not None:
            try:
                picam2.set_controls(self._build_controls())
            except Exception:
                pass
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
            # Highlight constraint: biases AE toward underexposure to protect
            # bright areas from clipping (good for night/star work).
            controls["AeConstraintMode"] = int(cfg.get("ae_constraint_mode", 0))

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
        consecutive_failures = 0
        while self._running:
            try:
                self._run_camera()
                consecutive_failures = 0
            except Exception as e:
                consecutive_failures += 1
                logger.error("Camera error: %s", e, exc_info=True)
                if not self._running:
                    break
                # Exponential backoff so an absent/disconnected camera doesn't
                # spin a tight retry loop.  Picamera2 init can leak resources
                # even with our cleanup wrapper (libcamera internals), so slow
                # retries also prevent secondary OS-wide exhaustion.
                backoff = min(5 * (2 ** min(consecutive_failures - 1, 6)), 300)
                logger.info("Camera retry in %ds (failure #%d)",
                            backoff, consecutive_failures)
                time.sleep(backoff)

    def _run_camera(self):
        from config import config

        self._restart_event.clear()
        cfg = config.all()

        from libcamera import Transform
        picam2 = _safe_create_picamera2()
        try:
            # Always boot the pipeline at a short FrameDurationLimits.
            # libcamera's IPA marks the first ~6 frames as Status != Success
            # while AE/AGC settles, and picamera2 silently discards those.
            # If we start at the user's long FDL (e.g. 120s) those 6 settling
            # frames take 12 minutes before any frame is delivered.  Starting
            # short lets warmup complete in <1s, then we switch to the real
            # FDL via set_controls below.
            WARMUP_FDL_US = 100_000

            video_config = picam2.create_video_configuration(
                main={"format": "RGB888", "size": (4056, 3040)},
                transform=Transform(hflip=bool(cfg.get("hflip")), vflip=bool(cfg.get("vflip"))),
                controls={"FrameDurationLimits": (WARMUP_FDL_US, WARMUP_FDL_US)},
                buffer_count=2,
            )
            picam2.configure(video_config)

            picam2.start()
            time.sleep(1.5)  # sensor + IPA warmup at short FDL

            props = picam2.camera_properties
            pixel_size   = props.get('PixelArraySize', (4056, 3040))
            active_areas = props.get('PixelArrayActiveAreas')
            logger.info("PixelArraySize=%s  PixelArrayActiveAreas=%s", pixel_size, active_areas)

            controls = self._build_controls()
            # Pin ScalerCrop to the full pixel array.  PixelArrayActiveAreas
            # would seem more correct (it skips optical-black rows) but its
            # tuple format is ambiguous on the Pi 5 / PiSP backend — the IMX477
            # reports (8, 16, 4056, 3040) which can't be (x, y, w, h) since
            # 8+4056 > 4056.  Using the full array forces libcamera to clamp
            # to the real bounds and never silently shrinks the frame.
            controls["ScalerCrop"] = (0, 0, int(pixel_size[0]), int(pixel_size[1]))
            logger.info("Setting ScalerCrop=%s", controls["ScalerCrop"])
            picam2.set_controls(controls)
            time.sleep(0.5)  # controls settle

            self._picam2 = picam2  # expose for direct set_controls() from apply_settings()
            _diag_frames = 0

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

                # Log actual ScalerCrop readback for the first few frames so we
                # can verify the ISP is honouring what we requested.
                if _diag_frames < 3:
                    _diag_frames += 1
                    logger.info("Frame %d: ScalerCrop readback=%s  shape=%s",
                                _diag_frames, meta.get('ScalerCrop'), frame.shape)

                self._publish(frame, time.time(), meta)

        finally:
            self._picam2 = None
            try:
                picam2.stop()
            except Exception:
                pass
            try:
                picam2.close()
            except Exception:
                pass


camera_service = CameraService()
