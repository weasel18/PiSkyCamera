#!/usr/bin/env bash
set -euo pipefail
cd "$(dirname "$0")"

echo "=== piSkyCam installation ==="

# ── System packages ────────────────────────────────────────────────────────────
echo "[1/4] Installing system packages..."
sudo apt-get update -qq
sudo apt-get install -y \
    python3-picamera2 \
    python3-libcamera \
    python3-flask \
    python3-pil \
    python3-numpy \
    ffmpeg

# ── MediaMTX (RTSP server) ─────────────────────────────────────────────────────
echo "[2/4] Checking MediaMTX..."
if ! command -v mediamtx &>/dev/null; then
    VERSION="v1.9.1"
    ARCH="$(dpkg --print-architecture)"
    case "$ARCH" in
        arm64)  MXARCH="arm64v8" ;;
        armhf)  MXARCH="armv7" ;;
        *)      MXARCH="arm64v8" ;;
    esac
    URL="https://github.com/bluenviron/mediamtx/releases/download/${VERSION}/mediamtx_${VERSION}_linux_${MXARCH}.tar.gz"
    echo "  Downloading MediaMTX ${VERSION} (${MXARCH})..."
    wget -q "$URL" -O /tmp/mediamtx.tar.gz
    sudo tar -xzf /tmp/mediamtx.tar.gz -C /usr/local/bin mediamtx
    rm /tmp/mediamtx.tar.gz
    echo "  MediaMTX installed at $(which mediamtx)"
else
    echo "  MediaMTX already installed: $(mediamtx --version 2>/dev/null || echo 'ok')"
fi

# ── Camera / GPIO access ───────────────────────────────────────────────────────
echo "[3/4] Configuring permissions..."
sudo usermod -aG video "$USER" 2>/dev/null || true

# Ensure camera is enabled in config (Pi 5 uses libcamera, no /boot/config.txt needed)
# Warn if camera not detected
if ! libcamera-hello --list-cameras 2>/dev/null | grep -q 'imx477\|hq'; then
    echo "  WARNING: IMX477 (HQ camera) not detected. Check camera cable and libcamera."
fi

# ── Systemd service ────────────────────────────────────────────────────────────
echo "[4/4] Installing systemd service..."
sudo cp piSkyCam.service /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable piSkyCam

IP="$(hostname -I | awk '{print $1}')"
echo ""
echo "╔══════════════════════════════════════════════════════╗"
echo "║              piSkyCam installation complete          ║"
echo "╠══════════════════════════════════════════════════════╣"
printf "║  Web UI   : http://%-33s║\n" "${IP}:8080"
printf "║  RTSP     : rtsp://%-33s║\n" "${IP}:8554/live"
printf "║  ONVIF    : http://%-33s║\n" "${IP}:8080/onvif/device_service"
echo "╚══════════════════════════════════════════════════════╝"
echo ""
echo "Start now : sudo systemctl start piSkyCam"
echo "Test run  : python3 main.py"
echo "Logs      : journalctl -u piSkyCam -f"
