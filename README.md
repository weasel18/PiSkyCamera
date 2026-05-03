# piSkyCamera

A permanent outdoor sky / astrophotography camera built on a Raspberry Pi 5 with the IMX477 HQ Camera Module. Exposes a full ONVIF Profile S interface (dual RTSP streams, WS-Discovery, SOAP media service) so it integrates directly with NVRs like UniFi Protect, Blue Iris, and Milestone without any special drivers or plugins.

![piSkyCam Web UI](Screenshot.png)

---

## Hardware

| Part | Notes |
|---|---|
| Raspberry Pi 5 | 4 GB or 8 GB |
| IMX477 HQ Camera Module | 12.3 MP, 1/2.3" sensor |
| Optional: 180° fisheye lens | Full-sky capture; auto spot-metering when enabled |

---

## Features

- **Full 4056 × 3040 sensor readout** — no crop, no binning
- **Dual RTSP streams** — main (configurable res, up to 4K) + sub (1280 × 720) for NVR compatibility
- **ONVIF Profile S** — auto-discovery (WS-Discovery), GetProfiles, GetStreamUri, GetSnapshotUri, WS-Security digest auth
- **Web control UI** — dark astrophotography theme, live preview with SSE push updates, exposure overlay
- **Presets** — Day Auto, Night Auto, Planets, Deep Sky, Star Trails, Long Exp 120s
- **Sunrise/sunset scheduler** — automatically switches between day and night presets based on your GPS coordinates
- **Auto exposure with gain lock** — in auto mode the gain is fixed at your chosen value; AE controls only shutter (shutter-priority)
- **Fisheye support** — spot metering automatically enabled to ignore black vignette corners; ONVIF fisheye metadata
- **Long exposure mode** — the RTSP stream stays alive by repeating the last frame while the sensor integrates; web UI shows a countdown bar

---

## Installation

```bash
git clone https://github.com/weasel18/PiSkyCamera.git
cd PiSkyCamera
bash install.sh
```

`install.sh` installs `python3-picamera2`, `python3-flask`, `python3-pil`, `python3-numpy`, `ffmpeg`, and downloads MediaMTX.

### Run as a systemd service (auto-start on boot)

```bash
sudo cp piSkyCam.service /etc/systemd/system/
sudo systemctl enable --now piSkyCam
```

### Or run manually

```bash
python3 main.py
```

The terminal will print all URLs on startup.

---

## Accessing the camera

| URL | What it is |
|---|---|
| `http://<pi-ip>:8080` | Web control UI |
| `rtsp://<pi-ip>:8554/main` | Main RTSP stream |
| `rtsp://<pi-ip>:8554/sub` | Sub RTSP stream (1280×720) |
| `http://<pi-ip>:8080/onvif/device_service` | ONVIF device endpoint |
| `http://<pi-ip>:8080/snapshot.jpg` | Latest full-resolution JPEG |

---

## Adding to an NVR

### Auto-discovery
Most NVRs support ONVIF WS-Discovery. Run a camera scan — piSkyCam announces itself on the network via UDP multicast on port 3702.

### Manual add
- **Protocol**: ONVIF
- **IP**: `<pi-ip>`
- **Port**: `8080`
- **Username**: `admin`
- **Password**: `admin`

Default credentials can be changed in `camera_settings.json`.

---

## Web UI — Controls

### Presets

| Preset | Exposure | Gain | FPS | Best for |
|---|---|---|---|---|
| Day Auto | Auto | 1× | 10 | Normal daytime |
| Night Auto | Auto | 4× | 5 | Twilight / security |
| Planets | 50ms manual | 4× | 10 | Moon, planets |
| Deep Sky | 30s manual | 8× | 1 | Nebulae, galaxies |
| Star Trails | 15s manual | 4× | 1 | Star trail composites |
| Long Exp 120s | 120s manual | 16× | 1 | Faint deep-sky objects |

### Exposure

- **Auto mode**: AE controls shutter only; gain is fixed at the slider value (shutter-priority). Set gain to 1× for clean daytime images.
- **Manual mode**: Both shutter and gain are locked to the slider values.
- **Max FPS**: 10 fps at full 4056 × 3040 resolution.

### Sunrise/Sunset Scheduler

Enter your latitude and longitude, choose a day and night preset, and set offset minutes around sunset/sunrise. The scheduler switches presets automatically. Override buttons let you force a specific preset for 1 h, 4 h, 12 h, or until the next sunrise.

---

## Long Exposure Behavior

During exposures longer than 1 second the camera captures one frame per integration period. The RTSP stream stays alive by repeating the last frame at the configured stream FPS so the NVR never drops the connection. The web UI countdown bar shows how far through the current exposure the sensor is; the preview updates the moment a new frame arrives.

---

## File Layout

```
PiSkyCamera/
├── main.py              Entry point — starts all services
├── config.py            Thread-safe settings store (reads/writes camera_settings.json)
├── camera_service.py    picamera2 capture thread
├── rtsp_feeder.py       Pipes frames to FFmpeg → MediaMTX RTSP relay
├── app.py               Flask: web UI + ONVIF SOAP + SSE events + snapshot
├── discovery.py         WS-Discovery UDP multicast (NVR auto-detection)
├── scheduler.py         Sunrise/sunset preset switcher
├── templates/index.html Web control UI
├── static/style.css     Dark astrophotography theme
├── static/app.js        UI logic — presets, sliders, live preview, SSE
├── mediamtx.yml         MediaMTX RTSP server config (port 8554)
├── piSkyCam.service     systemd unit file
├── install.sh           Dependency installer
└── camera_settings.json Auto-created on first run — persists all settings
```

---

## Troubleshooting

**Camera not detected**
```bash
libcamera-hello --list-cameras   # should show imx477
```
On Pi 5, use the CAM0 or CAM1 connector and make sure the locking tab is fully closed.

**RTSP stream not connecting**
```bash
pgrep -a mediamtx        # should be running
# test with VLC:
vlc rtsp://<pi-ip>:8554/main
```

**NVR auto-discovery not working**
- Pi and NVR must be on the same subnet.
- Check that UDP port 3702 is not blocked by a firewall.
- Add the camera manually using the IP and port 8080.

**Permission denied on camera**
```bash
sudo usermod -aG video $USER
# log out and back in, or reboot
```
