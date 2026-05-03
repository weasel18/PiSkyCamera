# piSkyCam Setup & Usage

Raspberry Pi 5 + HQ Camera (IMX477) — ONVIF-compliant astrophotography camera server.

---

## Installation

```bash
cd /home/weasel/piSkyCamera
bash install.sh
```

This installs: `python3-picamera2`, `python3-flask`, `python3-pil`, `python3-numpy`, `ffmpeg`, and downloads MediaMTX.

### Start as a service (runs on boot)

```bash
sudo systemctl start piSkyCam
sudo systemctl status piSkyCam   # check it's running
journalctl -u piSkyCam -f        # live logs
```

### Or run manually (useful for testing)

```bash
python3 main.py
```

The terminal will print your URLs when it starts.

---

## Access

After starting, open a browser to:

```
http://<pi-ip>:8080
```

| Endpoint | What it is |
|---|---|
| `http://<pi-ip>:8080` | Web control UI |
| `rtsp://<pi-ip>:8554/live` | RTSP stream (for NVR or VLC) |
| `http://<pi-ip>:8080/onvif/device_service` | ONVIF device endpoint |
| `http://<pi-ip>:8080/snapshot.jpg` | Latest still frame |

---

## Adding to an NVR (ONVIF)

### Auto-discovery
Most NVRs (Hikvision, Dahua, Blue Iris, etc.) support ONVIF auto-discovery.
Run a camera scan — piSkyCam announces itself on the network via WS-Discovery (UDP multicast on port 3702).

### Manual add
If auto-discovery doesn't work, add manually:

- **Protocol**: ONVIF
- **IP**: `<pi-ip>`
- **Port**: `8080`
- **Username**: `admin`
- **Password**: `admin`

The NVR will call ONVIF `GetStreamUri` and connect to `rtsp://<pi-ip>:8554/live` automatically.
You can also point the NVR directly at the RTSP URL if it supports generic RTSP streams.

Default credentials can be changed in `camera_settings.json` after first run.

---

## Web UI Controls

### Presets (quick-start buttons)

| Preset | Exposure | Gain | Stream FPS | Best for |
|---|---|---|---|---|
| Day Auto | Auto | Auto | 30 | Normal daytime |
| Night Auto | Auto | 4× | 5 | Twilight / security |
| Planets | 50ms | 4× | 15 | Moon, planets |
| Deep Sky | 30s | 8× | 1 | Nebulae, galaxies |
| Star Trails | 15s | 4× | 1 | Star trail composites |
| Long Exp 60s | 60s | 16× | 1 | Faint deep sky objects |

### Exposure section

- **Mode**: Auto lets the sensor pick exposure/gain. Manual unlocks the sliders below.
- **Time**: Logarithmic slider from 100µs up to 60 seconds. Displays as `30s`, `250ms`, `100µs`, etc.
- **Gain**: Analog sensor gain, 1× to 16×. Higher gain = more sensitivity = more noise.

### White Balance section

- **Mode**: Auto works well for daytime. Switch to Manual for night work to avoid the sensor
  guessing and shifting colors between frames.
- **Red / Blue gains**: Adjust individually. For neutral night sky try R≈2.2, B≈1.6.
  For solar/lunar imaging try R≈1.8, B≈1.8.

### Image section

- **Brightness**: -1.0 to +1.0 (default 0)
- **Contrast**: 0 to 3.0 (default 1.0)
- **Saturation**: 0 to 2.0 (default 1.0) — set to 0 for monochrome
- **Sharpness**: 0 to 2.0 (default 1.0) — reduce for long exposures to avoid noise amplification

### Stream section

- **Resolution**: Full sensor is 4056×3040. Lower resolutions reduce network load.
  Changing resolution briefly restarts the camera (~3s outage on the RTSP stream).
- **Stream FPS**: How fast the RTSP stream runs. During long exposures the last captured
  frame repeats at this rate to keep the NVR connected. Set to 1 for long-exposure work.
- **Flip H/V**: Mirror the image if your lens/mount requires it.

---

## Long Exposure Behavior

When exposure is longer than 1 second:

1. The camera captures one frame per exposure interval (e.g. one frame every 30s).
2. The RTSP stream stays alive — the last captured frame repeats at the configured
   stream FPS so the NVR never loses the connection or creates a gap in recording.
3. The web UI shows a countdown bar and updates the preview the moment a new frame arrives.
4. Settings changes (gain, white balance, etc.) take effect on the **next** frame after the
   current exposure completes.

---

## Settings persistence

All settings are saved automatically to `camera_settings.json` in the project folder
whenever you change something. They reload on the next start.

To reset to defaults, delete `camera_settings.json` and restart.

---

## Troubleshooting

**Camera not detected**
```bash
libcamera-hello --list-cameras
```
Should show `imx477`. If not, check the ribbon cable connection. On Pi 5, use the CAM0 or CAM1 port and ensure the locking tab is fully closed.

**RTSP stream not working**
```bash
# Check mediamtx is running
pgrep -a mediamtx

# Test with VLC
vlc rtsp://<pi-ip>:8554/live
```

**ONVIF not discovered by NVR**
- Ensure the Pi and NVR are on the same subnet.
- Check that UDP port 3702 is not blocked by a firewall.
- Try adding the camera manually using the IP and port 8080.

**Long exposures capping out**
- The IMX477 supports exposures up to ~200s in theory, but results vary.
- If you see the actual exposure (shown in the web UI overlay) is shorter than requested,
  try reducing gain first — at very high gain the sensor may limit max exposure.

**Permission denied on camera**
```bash
sudo usermod -aG video $USER
# then log out and back in, or reboot
```

---

## File layout

```
piSkyCamera/
├── main.py              Entry point — starts all services
├── config.py            Thread-safe settings (reads/writes camera_settings.json)
├── camera_service.py    picamera2 capture thread
├── rtsp_feeder.py       Pipes frames to FFmpeg → MediaMTX RTSP server
├── app.py               Flask: web UI + ONVIF SOAP + SSE + snapshot endpoint
├── discovery.py         WS-Discovery UDP (NVR auto-detection)
├── templates/index.html Web control UI
├── static/style.css     Dark red astrophotography theme
├── static/app.js        UI logic — settings, presets, live preview
├── mediamtx.yml         MediaMTX RTSP server config (port 8554)
├── piSkyCam.service     systemd unit file
├── install.sh           Dependency installer
└── camera_settings.json Auto-created on first run — persists your settings
```
