import subprocess
import threading
import time
import logging

logger = logging.getLogger(__name__)

# IMX477 full sensor resolution — camera always captures at this size.
# The stream is scaled/letterboxed to cfg["resolution"] by FFmpeg.
CAPTURE_W, CAPTURE_H = 4056, 3040


class RtspFeeder:
    def __init__(self):
        self._running = False
        self._thread = None
        self._restart_event = threading.Event()

    def start(self):
        self._running = True
        self._thread = threading.Thread(target=self._run_loop, daemon=True, name="rtsp")
        self._thread.start()

    def stop(self):
        self._running = False

    def restart(self):
        """Signal the running FFmpeg process to stop so _run_loop restarts it."""
        self._restart_event.set()

    def _run_loop(self):
        while self._running:
            try:
                self._run_ffmpeg()
            except Exception as e:
                logger.error("RTSP feeder error: %s", e)
            if self._running:
                logger.info("RTSP feeder restarting in 5s...")
                time.sleep(5)

    def _run_ffmpeg(self):
        from config import config
        from camera_service import camera_service

        self._restart_event.clear()

        cfg = config.all()
        main_w, main_h = cfg["resolution"]
        main_fps = max(1, min(10, int(cfg.get("stream_fps", 10))))
        sub_w, sub_h = cfg.get("sub_resolution", [1280, 720])
        sub_fps  = max(1, min(10, int(cfg.get("sub_fps", 10))))
        rtsp_port = cfg.get("rtsp_port", 8554)
        frame_interval = 1.0 / main_fps

        # Compute scaled widths that are exactly even and maintain the 4:3 AR.
        # Using explicit target heights (not force_original_aspect_ratio) avoids
        # off-by-one rounding that would make the scaled image 1-2 px taller than
        # the target, causing pad to clip the top row(s).
        def _scaled_w(target_h):
            return (int(target_h * CAPTURE_W / CAPTURE_H) // 2) * 2

        main_sw = _scaled_w(main_h)
        sub_sw  = _scaled_w(sub_h)
        main_xpad = (main_w - main_sw) // 2
        sub_xpad  = (sub_w  - sub_sw)  // 2

        # One FFmpeg process: split the raw input into main + sub paths.
        # The fps filter on the sub branch handles up- or down-sampling to sub_fps.
        x264_common = "repeat-headers=1:annexb=1:colorprim=bt709:transfer=bt709:colormatrix=bt709"
        filter_complex = (
            f"[0:v]split=2[m][s];"
            f"[m]scale={main_sw}:{main_h}:flags=bilinear,"
            f"pad={main_w}:{main_h}:{main_xpad}:0[main];"
            f"[s]fps={sub_fps},"
            f"scale={sub_sw}:{sub_h}:flags=bilinear,"
            f"pad={sub_w}:{sub_h}:{sub_xpad}:0[sub]"
        )

        cmd = [
            "ffmpeg", "-loglevel", "warning",
            "-f", "rawvideo", "-pix_fmt", "rgb24",
            "-s", f"{CAPTURE_W}x{CAPTURE_H}",
            "-r", str(main_fps),
            "-i", "pipe:0",
            "-filter_complex", filter_complex,
            # ── Main stream ──────────────────────────────────────────────────
            "-map", "[main]",
            "-c:v", "libx264", "-profile:v", "main", "-level:v", "5.1",
            "-preset", "ultrafast", "-tune", "zerolatency",
            "-pix_fmt", "yuv420p",
            "-maxrate", "8M", "-bufsize", "16M",
            "-g", str(main_fps), "-keyint_min", str(main_fps),
            "-sc_threshold", "0", "-flags", "+cgop",
            "-x264-params", x264_common,
            "-f", "rtsp", "-rtsp_transport", "tcp",
            f"rtsp://localhost:{rtsp_port}/main",
            # ── Sub stream ───────────────────────────────────────────────────
            "-map", "[sub]",
            "-c:v", "libx264", "-profile:v", "baseline", "-level:v", "3.1",
            "-preset", "ultrafast", "-tune", "zerolatency",
            "-pix_fmt", "yuv420p",
            "-maxrate", "2M", "-bufsize", "4M",
            "-g", str(sub_fps), "-keyint_min", str(sub_fps),
            "-sc_threshold", "0", "-flags", "+cgop",
            "-x264-params", x264_common,
            "-f", "rtsp", "-rtsp_transport", "tcp",
            f"rtsp://localhost:{rtsp_port}/sub",
        ]

        logger.info(
            "Starting FFmpeg: %dx%d → main %dx%d@%dfps  sub %dx%d@%dfps",
            CAPTURE_W, CAPTURE_H,
            main_w, main_h, main_fps,
            sub_w, sub_h, sub_fps,
        )
        proc = subprocess.Popen(cmd, stdin=subprocess.PIPE, stderr=subprocess.PIPE)

        # Drain stderr in background — without this, a full 64 KB stderr pipe
        # causes FFmpeg to deadlock trying to write errors while we block on stdin.
        def _drain_stderr():
            for line in proc.stderr:
                logger.warning("FFmpeg: %s", line.decode(errors="replace").rstrip())
        threading.Thread(target=_drain_stderr, daemon=True).start()

        last_frame_bytes = None
        next_send = time.monotonic()

        try:
            while self._running and proc.poll() is None and not self._restart_event.is_set():
                now = time.monotonic()
                wait = next_send - now
                if wait > 0:
                    time.sleep(wait)
                    now = time.monotonic()

                frame, _ = camera_service.peek_frame()
                if frame is not None:
                    last_frame_bytes = frame.tobytes()

                if last_frame_bytes is not None:
                    try:
                        proc.stdin.write(last_frame_bytes)
                        proc.stdin.flush()
                    except (BrokenPipeError, OSError):
                        break

                next_send += frame_interval
        finally:
            try:
                proc.stdin.close()
            except Exception:
                pass
            try:
                proc.wait(timeout=5)
            except subprocess.TimeoutExpired:
                proc.kill()

        rc = proc.returncode
        if rc not in (0, None, -15):
            logger.error("FFmpeg exited with code %d", rc)


rtsp_feeder = RtspFeeder()
