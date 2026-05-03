#!/usr/bin/env python3
import logging
import os
import signal
import subprocess
import sys
import time

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(name)-16s %(levelname)s %(message)s",
)
logger = logging.getLogger("main")

BASE = os.path.dirname(os.path.abspath(__file__))
MEDIAMTX_CONF = os.path.join(BASE, "mediamtx.yml")


def find_executable(name):
    for d in os.environ.get("PATH", "/usr/local/bin:/usr/bin:/bin").split(":"):
        p = os.path.join(d, name)
        if os.access(p, os.X_OK):
            return p
    return None


def start_mediamtx():
    exe = find_executable("mediamtx")
    if not exe:
        logger.warning("mediamtx not found — RTSP stream disabled")
        return None
    proc = subprocess.Popen(
        [exe, MEDIAMTX_CONF],
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    logger.info("mediamtx started (PID %d)", proc.pid)
    return proc


def main():
    logger.info("=== piSkyCam starting ===")

    mediamtx = start_mediamtx()
    time.sleep(1)  # give mediamtx a moment to bind ports

    from camera_service import camera_service
    camera_service.start()
    logger.info("Camera service started")

    time.sleep(2)  # let camera warm up before feeding RTSP

    from rtsp_feeder import rtsp_feeder
    rtsp_feeder.start()
    logger.info("RTSP feeder started")

    from discovery import start_discovery
    start_discovery()

    from scheduler import scheduler
    scheduler.start()
    logger.info("Scheduler started")

    from config import config
    port = config.get("app_port", 8080)

    from app import local_ip
    ip = local_ip()
    logger.info("────────────────────────────────────────")
    logger.info("Web UI   : http://%s:%d", ip, port)
    logger.info("RTSP main: rtsp://%s:%d/main", ip, config.get("rtsp_port", 8554))
    logger.info("RTSP sub : rtsp://%s:%d/sub",  ip, config.get("rtsp_port", 8554))
    logger.info("ONVIF    : http://%s:%d/onvif/device_service", ip, port)
    logger.info("Snapshot : http://%s:%d/snapshot.jpg", ip, port)
    logger.info("────────────────────────────────────────")

    def shutdown(sig, _frame):
        logger.info("Shutting down (signal %d)…", sig)
        camera_service.stop()
        rtsp_feeder.stop()
        if mediamtx:
            mediamtx.terminate()
        sys.exit(0)

    signal.signal(signal.SIGINT, shutdown)
    signal.signal(signal.SIGTERM, shutdown)

    from app import app
    app.run(host="0.0.0.0", port=port, threaded=True, use_reloader=False)


if __name__ == "__main__":
    main()
