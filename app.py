import base64
import copy
import hashlib
import hmac
import io
import json
import re
import socket
import subprocess
import uuid
import logging
from datetime import datetime, timezone
from xml.etree import ElementTree as ET

from flask import Flask, request, Response, render_template, jsonify

from config import config
from camera_service import camera_service

logger = logging.getLogger(__name__)

app = Flask(__name__)

_DEVICE_UUID = str(uuid.uuid5(uuid.NAMESPACE_DNS, socket.gethostname()))


# ── Helpers ───────────────────────────────────────────────────────────────────

def local_ip():
    try:
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        s.connect(("8.8.8.8", 80))
        return s.getsockname()[0]
    except Exception:
        return "127.0.0.1"
    finally:
        s.close()


def parse_soap_action(data: bytes) -> str | None:
    """Extract the local action name from the first element in the SOAP Body."""
    try:
        root = ET.fromstring(data)
        for ns_uri in (
            "http://www.w3.org/2003/05/soap-envelope",
            "http://schemas.xmlsoap.org/soap/envelope/",
        ):
            body = root.find(f"{{{ns_uri}}}Body")
            if body is not None and len(body):
                tag = body[0].tag
                return tag.split("}")[1] if "}" in tag else tag
    except Exception:
        pass
    return None


def soap_wrap(body: str) -> str:
    return (
        '<?xml version="1.0" encoding="UTF-8"?>'
        '<s:Envelope xmlns:s="http://www.w3.org/2003/05/soap-envelope"'
        ' xmlns:tds="http://www.onvif.org/ver10/device/wsdl"'
        ' xmlns:trt="http://www.onvif.org/ver10/media/wsdl"'
        ' xmlns:tt="http://www.onvif.org/ver10/schema">'
        f"<s:Body>{body}</s:Body>"
        "</s:Envelope>"
    )


def soap_resp(body: str) -> Response:
    return Response(
        soap_wrap(body),
        mimetype="application/soap+xml; charset=utf-8",
    )


def soap_fault(msg: str) -> str:
    return soap_wrap(
        f"<s:Fault><s:Code><s:Value>s:Receiver</s:Value></s:Code>"
        f"<s:Reason><s:Text xml:lang='en'>{msg}</s:Text></s:Reason></s:Fault>"
    )


# ── ONVIF Device service ──────────────────────────────────────────────────────

def onvif_get_system_datetime():
    now = datetime.now(timezone.utc)
    return (
        "<tds:GetSystemDateAndTimeResponse>"
        "<tds:SystemDateAndTime>"
        "<tt:DateTimeType>NTP</tt:DateTimeType>"
        "<tt:DaylightSavings>false</tt:DaylightSavings>"
        "<tt:TimeZone><tt:TZ>UTC0</tt:TZ></tt:TimeZone>"
        "<tt:UTCDateTime>"
        f"<tt:Time><tt:Hour>{now.hour}</tt:Hour><tt:Minute>{now.minute}</tt:Minute><tt:Second>{now.second}</tt:Second></tt:Time>"
        f"<tt:Date><tt:Year>{now.year}</tt:Year><tt:Month>{now.month}</tt:Month><tt:Day>{now.day}</tt:Day></tt:Date>"
        "</tt:UTCDateTime>"
        "</tds:SystemDateAndTime>"
        "</tds:GetSystemDateAndTimeResponse>"
    )


def onvif_get_device_info():
    name = config.get("device_name", "piSkyCam")
    return (
        "<tds:GetDeviceInformationResponse>"
        "<tds:Manufacturer>RaspberryPi</tds:Manufacturer>"
        f"<tds:Model>{name}</tds:Model>"
        "<tds:FirmwareVersion>1.0.0</tds:FirmwareVersion>"
        "<tds:SerialNumber>PI5-HQ-001</tds:SerialNumber>"
        "<tds:HardwareId>Pi5-IMX477</tds:HardwareId>"
        "</tds:GetDeviceInformationResponse>"
    )


def onvif_get_capabilities():
    ip = local_ip()
    port = config.get("app_port", 8080)
    return (
        "<tds:GetCapabilitiesResponse><tds:Capabilities>"
        f"<tt:Device><tt:XAddr>http://{ip}:{port}/onvif/device_service</tt:XAddr>"
        "<tt:System><tt:DiscoveryResolve>false</tt:DiscoveryResolve>"
        "<tt:DiscoveryBye>false</tt:DiscoveryBye>"
        "<tt:RemoteDiscovery>false</tt:RemoteDiscovery>"
        "<tt:SystemBackup>false</tt:SystemBackup>"
        "<tt:SystemLogging>false</tt:SystemLogging>"
        "<tt:FirmwareUpgrade>false</tt:FirmwareUpgrade></tt:System></tt:Device>"
        f"<tt:Media><tt:XAddr>http://{ip}:{port}/onvif/media_service</tt:XAddr>"
        "<tt:StreamingCapabilities>"
        "<tt:RTPMulticast>false</tt:RTPMulticast>"
        "<tt:RTP_TCP>true</tt:RTP_TCP>"
        "<tt:RTP_RTSP_TCP>true</tt:RTP_RTSP_TCP>"
        "</tt:StreamingCapabilities>"
        "<tt:SnapshotUri>true</tt:SnapshotUri>"
        "</tt:Media>"
        "</tds:Capabilities></tds:GetCapabilitiesResponse>"
    )


def onvif_get_scopes():
    name = config.get("device_name", "piSkyCam")
    def scope(item):
        return (
            "<tds:Scopes>"
            "<tt:ScopeDef>Fixed</tt:ScopeDef>"
            f"<tt:ScopeItem>{item}</tt:ScopeItem>"
            "</tds:Scopes>"
        )
    items = [
        "onvif://www.onvif.org/type/video_encoder",
        "onvif://www.onvif.org/Profile/Streaming",
        "onvif://www.onvif.org/Profile/S",
        f"onvif://www.onvif.org/name/{name}",
        "onvif://www.onvif.org/hardware/Pi5-IMX477",
    ]
    if config.get("fisheye_lens", False):
        items.append("onvif://www.onvif.org/type/fisheye")
    return "<tds:GetScopesResponse>" + "".join(scope(i) for i in items) + "</tds:GetScopesResponse>"


def onvif_get_hostname():
    return (
        "<tds:GetHostnameResponse><tds:HostnameInformation>"
        "<tt:FromDHCP>false</tt:FromDHCP>"
        f"<tt:Name>{socket.gethostname()}</tt:Name>"
        "</tds:HostnameInformation></tds:GetHostnameResponse>"
    )


def onvif_get_network_interfaces():
    return (
        "<tds:GetNetworkInterfacesResponse>"
        "<tds:NetworkInterfaces token='eth0'>"
        "<tt:Enabled>true</tt:Enabled>"
        "<tt:IPv4><tt:Enabled>true</tt:Enabled>"
        "<tt:Config><tt:DHCP>true</tt:DHCP></tt:Config>"
        "</tt:IPv4>"
        "</tds:NetworkInterfaces>"
        "</tds:GetNetworkInterfacesResponse>"
    )


# ── ONVIF helpers ────────────────────────────────────────────────────────────

def _get_stream_token():
    """Extract ProfileToken or ConfigurationToken from the current SOAP request."""
    try:
        root = ET.fromstring(request.data)
        for elem in root.iter():
            local = elem.tag.split('}')[1] if '}' in elem.tag else elem.tag
            if local in ('ProfileToken', 'ConfigurationToken'):
                return elem.text or ''
    except Exception:
        pass
    return ''


def _check_ws_security() -> bool:
    """
    Validate WS-UsernameToken in the current SOAP request.
    Returns True only when a valid digest matches the configured credentials.
    Missing token, malformed XML, or comparison failure all return False — auth
    must be opt-in for protected actions, not opt-out.
    """
    try:
        root = ET.fromstring(request.data)
    except ET.ParseError:
        return False
    try:
        token_elem = None
        for elem in root.iter():
            local = elem.tag.split('}')[1] if '}' in elem.tag else elem.tag
            if local == 'UsernameToken':
                token_elem = elem
                break
        if token_elem is None:
            return False

        username = password = nonce_b64 = created = ''
        is_digest = True
        for child in token_elem:
            local = child.tag.split('}')[1] if '}' in child.tag else child.tag
            if local == 'Username':
                username = child.text or ''
            elif local == 'Password':
                password = child.text or ''
                is_digest = 'PasswordDigest' in child.get('Type', '')
            elif local == 'Nonce':
                nonce_b64 = child.text or ''
            elif local == 'Created':
                created = child.text or ''

        cfg_user = config.get("onvif_username", "admin")
        cfg_pass = config.get("onvif_password", "admin")
        if not hmac.compare_digest(username, cfg_user):
            return False
        if not is_digest:
            return hmac.compare_digest(password, cfg_pass)
        # Digest = Base64(SHA1(nonce_bytes + created_utf8 + password_utf8))
        nonce_bytes = base64.b64decode(nonce_b64)
        digest = base64.b64encode(
            hashlib.sha1(nonce_bytes + created.encode() + cfg_pass.encode()).digest()
        ).decode()
        return hmac.compare_digest(digest, password)
    except Exception as e:
        logger.warning("WS-Security check error: %s", e)
        return False


# ONVIF Profile S allows these actions without authentication so a discovery
# probe can identify the device before credentials are exchanged.  Everything
# else (especially media actions like GetStreamUri/GetSnapshotUri) requires a
# valid WS-UsernameToken digest.
_UNAUTH_ONVIF_ACTIONS = frozenset({
    'GetSystemDateAndTime',
    'GetServices',
    'GetCapabilities',
    'GetServiceCapabilities',
    'GetWsdlUrl',
    'GetEndpointReference',
})


def _require_auth(action: str | None) -> Response | None:
    """Return a 401 SOAP fault if credentials are wrong, else None."""
    if action in _UNAUTH_ONVIF_ACTIONS:
        return None
    if not _check_ws_security():
        return Response(
            soap_fault("Not Authorized"),
            status=401,
            mimetype="application/soap+xml; charset=utf-8",
        )
    return None


# ── ONVIF Media service ───────────────────────────────────────────────────────

def _enc_config_inner(w, h, fps, profile='Main', bitrate_kbps=8192):
    """Inner body shared by profile and standalone encoder-config responses."""
    return (
        "<tt:Name>VideoEncoder</tt:Name><tt:UseCount>1</tt:UseCount>"
        "<tt:Encoding>H264</tt:Encoding>"
        f"<tt:Resolution><tt:Width>{w}</tt:Width><tt:Height>{h}</tt:Height></tt:Resolution>"
        "<tt:Quality>6</tt:Quality>"
        "<tt:RateControl>"
        f"<tt:FrameRateLimit>{fps}</tt:FrameRateLimit>"
        "<tt:EncodingInterval>1</tt:EncodingInterval>"
        f"<tt:BitrateLimit>{bitrate_kbps}</tt:BitrateLimit>"
        "</tt:RateControl>"
        f"<tt:H264><tt:GovLength>{fps}</tt:GovLength>"
        f"<tt:H264Profile>{profile}</tt:H264Profile></tt:H264>"
    )


def _enc_config_xml(token, w, h, fps, profile='Main', bitrate_kbps=8192):
    """Wraps encoder config in <trt:Configurations> for GetVideoEncoderConfigurations."""
    return (
        f"<trt:Configurations token='{token}'>"
        + _enc_config_inner(w, h, fps, profile, bitrate_kbps)
        + "</trt:Configurations>"
    )


def _profile_xml(p_token, p_name, enc_token, w, h, fps, profile='Main', bitrate_kbps=8192):
    """Builds a full <trt:Profiles> element for GetProfiles/GetProfile responses."""
    return (
        f"<trt:Profiles token='{p_token}' fixed='true'>"
        f"<tt:Name>{p_name}</tt:Name>"
        "<tt:VideoSourceConfiguration token='vsconf'>"
        "<tt:Name>VideoSource</tt:Name><tt:UseCount>1</tt:UseCount>"
        "<tt:SourceToken>vsrc</tt:SourceToken>"
        "<tt:Bounds x='0' y='0' width='4056' height='3040'/>"
        "</tt:VideoSourceConfiguration>"
        # Inside Profiles the encoder config uses tt:VideoEncoderConfiguration,
        # NOT trt:Configurations (which is only for the standalone list response).
        f"<tt:VideoEncoderConfiguration token='{enc_token}'>"
        + _enc_config_inner(w, h, fps, profile, bitrate_kbps)
        + "</tt:VideoEncoderConfiguration>"
        "</trt:Profiles>"
    )


def _all_profiles_xml():
    cfg = config.all()
    mw, mh = cfg["resolution"]
    mfps = max(1, int(cfg.get("stream_fps", 30)))
    sw, sh = cfg.get("sub_resolution", [1280, 720])
    sfps = max(1, int(cfg.get("sub_fps", 15)))
    return (
        _profile_xml("profile_main", "MainStream", "veconf_main", mw, mh, mfps) +
        _profile_xml("profile_sub",  "SubStream",  "veconf_sub",  sw, sh, sfps,
                     profile='Baseline', bitrate_kbps=2048)
    )


def onvif_get_profiles():
    return f"<trt:GetProfilesResponse>{_all_profiles_xml()}</trt:GetProfilesResponse>"


def onvif_get_profile():
    token = _get_stream_token()
    cfg = config.all()
    if token == "profile_sub":
        sw, sh = cfg.get("sub_resolution", [1280, 720])
        sfps = max(1, int(cfg.get("sub_fps", 15)))
        xml = _profile_xml("profile_sub", "SubStream", "veconf_sub", sw, sh, sfps,
                           profile='Baseline', bitrate_kbps=2048)
    else:
        mw, mh = cfg["resolution"]
        mfps = max(1, int(cfg.get("stream_fps", 30)))
        xml = _profile_xml("profile_main", "MainStream", "veconf_main", mw, mh, mfps)
    return f"<trt:GetProfileResponse>{xml}</trt:GetProfileResponse>"


def onvif_get_stream_uri():
    ip = local_ip()
    rtsp_port = config.get("rtsp_port", 8554)
    token = _get_stream_token()
    path = "/sub" if token == "profile_sub" else "/main"
    return (
        "<trt:GetStreamUriResponse><trt:MediaUri>"
        f"<tt:Uri>rtsp://{ip}:{rtsp_port}{path}</tt:Uri>"
        "<tt:InvalidAfterConnect>false</tt:InvalidAfterConnect>"
        "<tt:InvalidAfterReboot>false</tt:InvalidAfterReboot>"
        "<tt:Timeout>PT0S</tt:Timeout>"
        "</trt:MediaUri></trt:GetStreamUriResponse>"
    )


def onvif_get_snapshot_uri():
    ip = local_ip()
    port = config.get("app_port", 8080)
    return (
        "<trt:GetSnapshotUriResponse><trt:MediaUri>"
        f"<tt:Uri>http://{ip}:{port}/snapshot.jpg</tt:Uri>"
        "<tt:InvalidAfterConnect>false</tt:InvalidAfterConnect>"
        "<tt:InvalidAfterReboot>false</tt:InvalidAfterReboot>"
        "<tt:Timeout>PT0S</tt:Timeout>"
        "</trt:MediaUri></trt:GetSnapshotUriResponse>"
    )


def onvif_get_video_sources():
    cfg = config.all()
    return (
        "<trt:GetVideoSourcesResponse>"
        "<trt:VideoSources token='vsrc'>"
        f"<tt:Framerate>{max(1, int(cfg.get('stream_fps', 10)))}</tt:Framerate>"
        "<tt:Resolution><tt:Width>4056</tt:Width><tt:Height>3040</tt:Height></tt:Resolution>"
        f"<tt:ImagingSettings><tt:Brightness>{cfg.get('brightness', 0)}</tt:Brightness>"
        f"<tt:Saturation>{cfg.get('saturation', 1)}</tt:Saturation>"
        f"<tt:Sharpness>{cfg.get('sharpness', 1)}</tt:Sharpness></tt:ImagingSettings>"
        "</trt:VideoSources>"
        "</trt:GetVideoSourcesResponse>"
    )


def _lens_description_xml():
    """ONVIF LensDescription for equidistant fisheye. Nested in Extension/Extension."""
    fov = config.get("fisheye_fov", 180)
    pts = "".join(
        f"<tt:Projection><tt:Angle>{fov/4*i:.1f}</tt:Angle>"
        f"<tt:Radius>{i/4:.3f}</tt:Radius></tt:Projection>"
        for i in range(5)
    )
    return (
        "<tt:Extension>"
        "<tt:Rotate><tt:Mode>OFF</tt:Mode></tt:Rotate>"
        "<tt:Extension>"
        "<tt:LensDescription FocalLength='1.8'>"
        "<tt:Offset x='0.0' y='0.0'/>"
        f"{pts}"
        "<tt:XFactor>1.0</tt:XFactor>"
        "</tt:LensDescription>"
        "</tt:Extension>"
        "</tt:Extension>"
    )


def onvif_get_video_source_configs():
    cfg = config.all()
    ext = _lens_description_xml() if cfg.get("fisheye_lens", False) else ""
    return (
        "<trt:GetVideoSourceConfigurationsResponse>"
        "<trt:Configurations token='vsconf'>"
        "<tt:Name>VideoSource</tt:Name><tt:UseCount>1</tt:UseCount>"
        "<tt:SourceToken>vsrc</tt:SourceToken>"
        "<tt:Bounds x='0' y='0' width='4056' height='3040'/>"
        f"{ext}"
        "</trt:Configurations>"
        "</trt:GetVideoSourceConfigurationsResponse>"
    )


def onvif_get_video_encoder_configs():
    cfg = config.all()
    mw, mh = cfg["resolution"]
    mfps = max(1, int(cfg.get("stream_fps", 30)))
    sw, sh = cfg.get("sub_resolution", [1280, 720])
    sfps = max(1, int(cfg.get("sub_fps", 15)))
    return (
        "<trt:GetVideoEncoderConfigurationsResponse>"
        + _enc_config_xml("veconf_main", mw, mh, mfps)
        + _enc_config_xml("veconf_sub",  sw, sh, sfps, profile='Baseline', bitrate_kbps=2048)
        + "</trt:GetVideoEncoderConfigurationsResponse>"
    )


def onvif_get_video_encoder_config():
    cfg = config.all()
    token = _get_stream_token()
    if token in ("veconf_sub", "profile_sub"):
        sw, sh = cfg.get("sub_resolution", [1280, 720])
        sfps = max(1, int(cfg.get("sub_fps", 15)))
        enc_token, inner = "veconf_sub", _enc_config_inner(sw, sh, sfps, 'Baseline')
    else:
        mw, mh = cfg["resolution"]
        mfps = max(1, int(cfg.get("stream_fps", 30)))
        enc_token, inner = "veconf_main", _enc_config_inner(mw, mh, mfps)
    return (
        "<trt:GetVideoEncoderConfigurationResponse>"
        f"<trt:Configuration token='{enc_token}'>{inner}</trt:Configuration>"
        "</trt:GetVideoEncoderConfigurationResponse>"
    )


def onvif_get_services():
    ip = local_ip()
    port = config.get("app_port", 8080)
    return (
        "<tds:GetServicesResponse>"
        "<tds:Service>"
        "<tds:Namespace>http://www.onvif.org/ver10/device/wsdl</tds:Namespace>"
        f"<tds:XAddr>http://{ip}:{port}/onvif/device_service</tds:XAddr>"
        "<tds:Version><tt:Major>2</tt:Major><tt:Minor>21</tt:Minor></tds:Version>"
        "</tds:Service>"
        "<tds:Service>"
        "<tds:Namespace>http://www.onvif.org/ver10/media/wsdl</tds:Namespace>"
        f"<tds:XAddr>http://{ip}:{port}/onvif/media_service</tds:XAddr>"
        "<tds:Version><tt:Major>2</tt:Major><tt:Minor>6</tt:Minor></tds:Version>"
        "</tds:Service>"
        "</tds:GetServicesResponse>"
    )


# ── Dispatch tables ───────────────────────────────────────────────────────────

DEVICE_ACTIONS = {
    "GetSystemDateAndTime": onvif_get_system_datetime,
    "GetDeviceInformation": onvif_get_device_info,
    "GetCapabilities": onvif_get_capabilities,
    "GetScopes": onvif_get_scopes,
    "GetHostname": onvif_get_hostname,
    "GetNetworkInterfaces": onvif_get_network_interfaces,
    # Many NVRs call GetWsdlUrl; return a minimal response
    "GetWsdlUrl": lambda: "<tds:GetWsdlUrlResponse><tds:WsdlUrl></tds:WsdlUrl></tds:GetWsdlUrlResponse>",
    "GetServices": onvif_get_services,
    "GetServiceCapabilities": lambda: (
        "<tds:GetServiceCapabilitiesResponse><tds:Capabilities>"
        "<tt:Network ZeroConfiguration='false' IPVersion6='false' DynDNS='false'/>"
        "<tt:Security UsernameToken='true' HttpDigest='false' TLS10='false' TLS11='false' TLS12='false' OnboardKeyGeneration='false' AccessPolicyConfig='false' DefaultAccessPolicy='false'/>"
        "<tt:System DiscoveryResolve='false' DiscoveryBye='false' RemoteDiscovery='false' SystemBackup='false' SystemLogging='false' FirmwareUpgrade='false'/>"
        "</tds:Capabilities></tds:GetServiceCapabilitiesResponse>"
    ),
}

MEDIA_ACTIONS = {
    "GetProfiles": onvif_get_profiles,
    "GetProfile": onvif_get_profile,
    "GetStreamUri": onvif_get_stream_uri,
    "GetSnapshotUri": onvif_get_snapshot_uri,
    "GetVideoSources": onvif_get_video_sources,
    "GetVideoSourceConfigurations": onvif_get_video_source_configs,
    "GetVideoSourceConfiguration": onvif_get_video_source_configs,
    "GetVideoEncoderConfigurations": onvif_get_video_encoder_configs,
    "GetVideoEncoderConfiguration": onvif_get_video_encoder_config,
    "GetAudioSources": lambda: "<trt:GetAudioSourcesResponse/>",
    "GetAudioSourceConfigurations": lambda: "<trt:GetAudioSourceConfigurationsResponse/>",
    "GetAudioEncoderConfigurations": lambda: "<trt:GetAudioEncoderConfigurationsResponse/>",
    "GetVideoEncoderConfigurationOptions": lambda: (
        "<trt:GetVideoEncoderConfigurationOptionsResponse>"
        "<trt:Options token='veconf_main'>"
        "<tt:QualityRange><tt:Min>1</tt:Min><tt:Max>6</tt:Max></tt:QualityRange>"
        "<tt:H264>"
        "<tt:ResolutionsAvailable><tt:Width>3840</tt:Width><tt:Height>2160</tt:Height></tt:ResolutionsAvailable>"
        "<tt:ResolutionsAvailable><tt:Width>2560</tt:Width><tt:Height>1440</tt:Height></tt:ResolutionsAvailable>"
        "<tt:ResolutionsAvailable><tt:Width>1920</tt:Width><tt:Height>1080</tt:Height></tt:ResolutionsAvailable>"
        "<tt:ResolutionsAvailable><tt:Width>1280</tt:Width><tt:Height>720</tt:Height></tt:ResolutionsAvailable>"
        "<tt:GovLengthRange><tt:Min>1</tt:Min><tt:Max>300</tt:Max></tt:GovLengthRange>"
        "<tt:FrameRateRange><tt:Min>1</tt:Min><tt:Max>10</tt:Max></tt:FrameRateRange>"
        "<tt:EncodingIntervalRange><tt:Min>1</tt:Min><tt:Max>1</tt:Max></tt:EncodingIntervalRange>"
        "<tt:H264ProfilesSupported>Main</tt:H264ProfilesSupported>"
        "</tt:H264></trt:Options>"
        "<trt:Options token='veconf_sub'>"
        "<tt:QualityRange><tt:Min>1</tt:Min><tt:Max>6</tt:Max></tt:QualityRange>"
        "<tt:H264>"
        "<tt:ResolutionsAvailable><tt:Width>1280</tt:Width><tt:Height>720</tt:Height></tt:ResolutionsAvailable>"
        "<tt:ResolutionsAvailable><tt:Width>640</tt:Width><tt:Height>360</tt:Height></tt:ResolutionsAvailable>"
        "<tt:GovLengthRange><tt:Min>1</tt:Min><tt:Max>300</tt:Max></tt:GovLengthRange>"
        "<tt:FrameRateRange><tt:Min>1</tt:Min><tt:Max>10</tt:Max></tt:FrameRateRange>"
        "<tt:EncodingIntervalRange><tt:Min>1</tt:Min><tt:Max>1</tt:Max></tt:EncodingIntervalRange>"
        "<tt:H264ProfilesSupported>Baseline</tt:H264ProfilesSupported>"
        "</tt:H264></trt:Options>"
        "</trt:GetVideoEncoderConfigurationOptionsResponse>"
    ),
    "GetServiceCapabilities": lambda: (
        "<trt:GetServiceCapabilitiesResponse>"
        "<trt:Capabilities SnapshotUri='true' Rotation='false' VideoSourceMode='false' OSD='false' EXICompression='false'>"
        "<tt:ProfileCapabilities MaximumNumberOfProfiles='2'/>"
        "<tt:StreamingCapabilities RTSPStreaming='true' RTPMulticast='false' RTP_TCP='true' RTP_RTSP_TCP='true' NonAggregateControl='false' NoRTSPStreaming='false'/>"
        "</trt:Capabilities>"
        "</trt:GetServiceCapabilitiesResponse>"
    ),
}


# ── SOAP routes ───────────────────────────────────────────────────────────────

@app.route("/onvif/device_service", methods=["GET", "POST"])
def device_service():
    if request.method == "GET":
        return Response("<DeviceService/>", mimetype="text/xml")
    action = parse_soap_action(request.data)
    logger.debug("Device action: %s", action)
    err = _require_auth(action)
    if err:
        return err
    fn = DEVICE_ACTIONS.get(action)
    if fn:
        return soap_resp(fn())
    logger.warning("Unknown device action '%s', returning device info", action)
    return soap_resp(onvif_get_device_info())


@app.route("/onvif/media_service", methods=["GET", "POST"])
def media_service():
    if request.method == "GET":
        return Response("<MediaService/>", mimetype="text/xml")
    action = parse_soap_action(request.data)
    logger.debug("Media action: %s", action)
    err = _require_auth(action)
    if err:
        return err
    fn = MEDIA_ACTIONS.get(action)
    if fn:
        return soap_resp(fn())
    logger.warning("Unknown media action: %s", action)
    return Response(soap_fault(f"Unknown action: {action}"), status=400,
                    mimetype="application/soap+xml")


# ── Web UI routes ─────────────────────────────────────────────────────────────

@app.route("/")
def index():
    return render_template("index.html")


@app.route("/snapshot.jpg")
def snapshot():
    from PIL import Image as PILImage
    frame, _ = camera_service.peek_frame()
    if frame is None:
        img = PILImage.new("RGB", (4056, 3040), (15, 15, 15))
    else:
        img = PILImage.fromarray(frame)
    buf = io.BytesIO()
    img.save(buf, format="JPEG", quality=85)
    buf.seek(0)
    return Response(
        buf.read(),
        mimetype="image/jpeg",
        headers={"Cache-Control": "no-store, no-cache"},
    )


@app.route("/api/events")
def sse_events():
    """Server-Sent Events: push a notification whenever a new frame arrives."""
    def generate():
        last_ts = 0.0
        while camera_service._running:
            try:
                # Short timeout so this thread exits promptly on service shutdown
                # rather than holding the Flask worker thread open for 30 s and
                # delaying the process exit during systemctl restart.
                frame, ts = camera_service.wait_for_frame(last_ts=last_ts, timeout=3.0)
                if not camera_service._running:
                    return
                if ts > last_ts and frame is not None:
                    last_ts = ts
                    meta = camera_service.get_metadata()
                    exp_us = meta.get("ExposureTime", 0)
                    payload = json.dumps({
                        "ts": ts,
                        "time": datetime.fromtimestamp(ts).strftime("%H:%M:%S"),
                        "exposure_us": exp_us,
                        "exposure_fmt": _fmt_exposure(exp_us),
                        "gain": round(float(meta.get("AnalogueGain", 1.0)), 2),
                        "lux": round(float(meta.get("Lux", 0)), 1),
                        "colour_gains": list(meta.get("ColourGains", (1.0, 1.0))),
                    })
                    yield f"data: {payload}\n\n"
                else:
                    yield ": ping\n\n"
            except GeneratorExit:
                return
            except Exception:
                yield ": error\n\n"

    return Response(
        generate(),
        mimetype="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )


def _fmt_exposure(us: int) -> str:
    if us <= 0:
        return "—"
    s = us / 1_000_000
    if s >= 1:
        val = s
        return f'{val:.0f}"' if val >= 10 else f'{val:.1f}"'
    # Express as 1/N fraction
    denom = round(1 / s)
    return f"1/{denom}"


_VALID_RESOLUTIONS = {(3840, 2160), (2560, 1440), (1920, 1080), (1280, 720)}

# Keys a preset is allowed to set.  These are the camera-state knobs the user
# may want to bake into a preset; settings outside this set (ports, resolution,
# schedule, etc.) are intentionally rejected to keep presets focused on the
# imaging pipeline.
_PRESETABLE_KEYS = frozenset({
    "exposure_mode", "exposure_time", "analogue_gain", "exposure_value",
    "ae_constraint_mode", "awb_mode", "colour_gain_r", "colour_gain_b",
    "noise_reduction_mode", "stream_fps",
    "brightness", "contrast", "saturation", "sharpness",
})

_PRESET_NAME_RE = re.compile(r"^[a-zA-Z0-9_-]{1,32}$")


def _validate_settings(data: dict):
    """Coerce and clamp settings values. Returns cleaned dict or raises ValueError."""
    out = {}
    for k, v in data.items():
        try:
            if k == "exposure_mode":
                if v not in ("auto", "manual"):
                    raise ValueError(f"exposure_mode must be auto or manual")
                out[k] = v
            elif k == "exposure_time":
                out[k] = max(100, min(120_000_000, int(v)))
            elif k == "analogue_gain":
                out[k] = max(1.0, min(16.0, float(v)))
            elif k == "exposure_value":
                out[k] = max(-8.0, min(8.0, float(v)))
            elif k == "ae_constraint_mode":
                if int(v) not in (0, 1, 2):
                    raise ValueError(f"ae_constraint_mode must be 0, 1, or 2")
                out[k] = int(v)
            elif k == "awb_mode":
                if v not in ("auto", "manual"):
                    raise ValueError(f"awb_mode must be auto or manual")
                out[k] = v
            elif k in ("colour_gain_r", "colour_gain_b"):
                out[k] = max(0.5, min(4.0, float(v)))
            elif k == "noise_reduction_mode":
                if int(v) not in (0, 1, 2, 3):
                    raise ValueError(f"noise_reduction_mode must be 0-3")
                out[k] = int(v)
            elif k == "brightness":
                out[k] = max(-1.0, min(1.0, float(v)))
            elif k == "contrast":
                out[k] = max(0.0, min(3.0, float(v)))
            elif k == "saturation":
                out[k] = max(0.0, min(2.0, float(v)))
            elif k == "sharpness":
                out[k] = max(0.0, min(2.0, float(v)))
            elif k == "resolution":
                w, h = int(v[0]), int(v[1])
                if (w, h) not in _VALID_RESOLUTIONS:
                    raise ValueError(f"unsupported resolution {w}x{h}")
                out[k] = [w, h]
            elif k == "sub_resolution":
                out[k] = [max(320, int(v[0])), max(240, int(v[1]))]
            elif k in ("hflip", "vflip", "fisheye_lens", "schedule_enabled"):
                out[k] = bool(v)
            elif k in ("stream_fps", "sub_fps"):
                out[k] = max(1, min(10, int(v)))
            elif k == "fisheye_fov":
                out[k] = max(120, min(220, int(v)))
            elif k == "latitude":
                out[k] = max(-90.0, min(90.0, float(v)))
            elif k == "longitude":
                out[k] = max(-180.0, min(180.0, float(v)))
            elif k in ("schedule_day_preset", "schedule_night_preset"):
                valid = set(config.get("presets", {}).keys())
                if v not in valid:
                    raise ValueError(f"{k} must be one of {sorted(valid)}")
                out[k] = v
            elif k in ("schedule_sunset_offset", "schedule_sunrise_offset"):
                out[k] = max(-120, min(120, int(v)))
            else:
                out[k] = v
        except (TypeError, IndexError) as e:
            raise ValueError(f"invalid value for {k}: {e}")
    return out


def _needs_camera_restart(changed: dict) -> bool:
    """Return True when a setting change requires a full camera restart."""
    if "exposure_mode" in changed:
        return True
    if "exposure_time" in changed:
        # Only restart for long exposures (>1s); small shutter tweaks settle via set_controls
        return int(changed["exposure_time"]) > 1_000_000
    return False


@app.route("/api/settings", methods=["GET"])
def get_settings():
    return jsonify(config.all())


@app.route("/api/settings", methods=["POST"])
def post_settings():
    data = request.json
    if not data:
        return jsonify({"error": "no data"}), 400

    ALLOWED = {
        "exposure_mode", "exposure_time", "analogue_gain", "exposure_value", "ae_constraint_mode",
        "awb_mode", "colour_gain_r", "colour_gain_b",
        "noise_reduction_mode",
        "brightness", "contrast", "saturation", "sharpness",
        "resolution", "hflip", "vflip", "stream_fps",
        "sub_resolution", "sub_fps",
        "fisheye_lens", "fisheye_fov",
        "schedule_enabled", "latitude", "longitude",
        "schedule_day_preset", "schedule_night_preset",
        "schedule_sunset_offset", "schedule_sunrise_offset",
    }
    filtered = {k: v for k, v in data.items() if k in ALLOWED}
    if not filtered:
        return jsonify({"error": "no valid keys"}), 400

    try:
        filtered = _validate_settings(filtered)
    except ValueError as e:
        return jsonify({"error": str(e)}), 400

    config.update(filtered)

    if any(k in ("hflip", "vflip") for k in filtered):
        camera_service.restart()
    elif any(k in ("resolution", "stream_fps", "sub_resolution", "sub_fps", "rtsp_port") for k in filtered):
        from rtsp_feeder import rtsp_feeder
        rtsp_feeder.restart()
        camera_service.restart()
    elif _needs_camera_restart(filtered):
        # Changing frame duration requires a full camera restart — set_controls()
        # alone doesn't reliably flush the existing frame buffer, causing old fast
        # frames to keep publishing and resetting the long-exposure countdown.
        camera_service.restart()
    else:
        camera_service.apply_settings()

    # Echo the post-validation values so the UI can sync to clamped/canonical
    # values (e.g. fps clipped to 1-10) without an extra GET round-trip.
    return jsonify({"ok": True, "applied": filtered})


@app.route("/api/schedule")
def get_schedule():
    from scheduler import scheduler
    return jsonify(scheduler.status())


@app.route("/api/schedule/override", methods=["POST"])
def post_schedule_override():
    data = request.json or {}
    action = data.get("action", "")
    from scheduler import scheduler
    if action == "set":
        hours = float(data.get("hours", 1))
        scheduler.set_override(hours)
    elif action == "until_sunrise":
        scheduler.set_override_until_sunrise()
    elif action == "clear":
        scheduler.clear_override()
    else:
        return jsonify({"error": "invalid action"}), 400
    return jsonify({"ok": True})


@app.route("/api/presets", methods=["GET"])
def get_presets():
    return jsonify(config.get("presets", {}))


@app.route("/api/presets/<name>", methods=["PUT"])
def put_preset(name):
    if not _PRESET_NAME_RE.match(name):
        return jsonify({"error": "name must match [a-zA-Z0-9_-]{1,32}"}), 400
    data = request.json
    if not isinstance(data, dict):
        return jsonify({"error": "body must be a JSON object"}), 400

    label = data.get("label", name)
    if not isinstance(label, str) or not (1 <= len(label) <= 40):
        return jsonify({"error": "label must be a string of 1-40 chars"}), 400

    values = data.get("values", {})
    if not isinstance(values, dict):
        return jsonify({"error": "values must be a JSON object"}), 400

    bad = [k for k in values if k not in _PRESETABLE_KEYS]
    if bad:
        return jsonify({"error": f"keys not allowed in preset: {bad}"}), 400

    try:
        clean_values = _validate_settings(values)
    except ValueError as e:
        return jsonify({"error": str(e)}), 400

    # Deep-copy so we don't mutate the DEFAULTS-shared dict on first run.
    presets = copy.deepcopy(config.get("presets", {}))
    presets[name] = {"label": label, "values": clean_values}
    config.set("presets", presets)
    return jsonify({"ok": True, "name": name, "preset": presets[name]})


@app.route("/api/presets/<name>", methods=["DELETE"])
def delete_preset(name):
    presets = copy.deepcopy(config.get("presets", {}))
    if name not in presets:
        return jsonify({"error": "preset not found"}), 404
    # Refuse to delete a preset the scheduler currently references — would
    # leave the schedule pointing at a missing preset name.
    day = config.get("schedule_day_preset")
    night = config.get("schedule_night_preset")
    if name in (day, night):
        which = "day" if name == day else "night"
        return jsonify({"error": f"preset is in use as the {which} schedule preset"}), 409
    if len(presets) <= 1:
        return jsonify({"error": "cannot delete the last preset"}), 409
    del presets[name]
    config.set("presets", presets)
    return jsonify({"ok": True})


@app.route("/api/stats")
def get_stats():
    import shutil
    import subprocess

    stats = {}

    try:
        with open("/sys/class/thermal/thermal_zone0/temp") as f:
            stats["cpu_temp_c"] = round(int(f.read().strip()) / 1000, 1)
    except Exception:
        stats["cpu_temp_c"] = None

    try:
        with open("/proc/loadavg") as f:
            parts = f.read().split()
            stats["load_1m"] = float(parts[0])
            stats["load_5m"] = float(parts[1])
    except Exception:
        stats["load_1m"] = None
        stats["load_5m"] = None

    try:
        mem = {}
        with open("/proc/meminfo") as f:
            for line in f:
                key, val = line.split(":")
                mem[key.strip()] = int(val.split()[0])
        stats["mem_total_mb"] = round(mem["MemTotal"] / 1024)
        stats["mem_avail_mb"] = round(mem["MemAvailable"] / 1024)
        stats["mem_used_pct"] = round((1 - mem["MemAvailable"] / mem["MemTotal"]) * 100)
    except Exception:
        stats["mem_total_mb"] = None
        stats["mem_avail_mb"] = None
        stats["mem_used_pct"] = None

    try:
        usage = shutil.disk_usage("/")
        stats["disk_total_gb"] = round(usage.total / 1e9, 1)
        stats["disk_free_gb"] = round(usage.free / 1e9, 1)
        stats["disk_used_pct"] = round(usage.used / usage.total * 100)
    except Exception:
        stats["disk_total_gb"] = None
        stats["disk_free_gb"] = None
        stats["disk_used_pct"] = None

    try:
        with open("/proc/uptime") as f:
            secs = float(f.read().split()[0])
        days = int(secs // 86400)
        hrs = int((secs % 86400) // 3600)
        mins = int((secs % 3600) // 60)
        if days:
            stats["uptime"] = f"{days}d {hrs}h {mins}m"
        elif hrs:
            stats["uptime"] = f"{hrs}h {mins}m"
        else:
            stats["uptime"] = f"{mins}m"
    except Exception:
        stats["uptime"] = None

    try:
        result = subprocess.run(
            ["vcgencmd", "get_throttled"],
            capture_output=True, text=True, timeout=2,
        )
        val = int(result.stdout.strip().split("=")[1], 16)
        # Bits 0-3: currently active conditions.  Bits 16-19: sticky "occurred
        # since boot" flags that never self-clear — don't treat those as an
        # active warning or the badge will stay up permanently after any
        # throttle event even when the Pi has cooled down.
        current = val & 0x0F
        current_names = {0: "undervoltage", 1: "freq_cap",
                         2: "throttled",    3: "soft_temp_limit"}
        past_names    = {0: "undervoltage", 1: "freq_cap",
                         2: "throttled",    3: "soft_temp_limit"}
        stats["throttled"]      = current != 0
        stats["throttled_hex"]  = hex(val)
        stats["throttle_flags"] = [name for bit, name in current_names.items()
                                   if current & (1 << bit)]
        past = (val >> 16) & 0x0F
        stats["throttle_past_flags"] = [f"{name}_occurred"
                                        for bit, name in past_names.items()
                                        if past & (1 << bit)]
    except Exception:
        stats["throttled"] = None
        stats["throttle_flags"] = []

    return jsonify(stats)


@app.route("/api/info")
def get_info():
    ip = local_ip()
    rtsp_port = config.get("rtsp_port", 8554)
    app_port = config.get("app_port", 8080)
    _, frame_ts = camera_service.peek_frame()
    return jsonify({
        "ip": ip,
        "hostname": socket.gethostname(),
        "rtsp_url":     f"rtsp://{ip}:{rtsp_port}/main",
        "rtsp_sub_url": f"rtsp://{ip}:{rtsp_port}/sub",
        "onvif_url": f"http://{ip}:{app_port}/onvif/device_service",
        "snapshot_url": f"http://{ip}:{app_port}/snapshot.jpg",
        "web_url": f"http://{ip}:{app_port}",
        "last_frame_ts": frame_ts,
    })
