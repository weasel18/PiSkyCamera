"""WS-Discovery (ONVIF camera auto-discovery over UDP multicast)."""
import socket
import struct
import threading
import uuid
import logging

logger = logging.getLogger(__name__)

MCAST_ADDR = "239.255.255.250"
MCAST_PORT = 3702

_DEVICE_UUID = None


def _device_uuid():
    global _DEVICE_UUID
    if _DEVICE_UUID is None:
        _DEVICE_UUID = str(uuid.uuid5(uuid.NAMESPACE_DNS, socket.gethostname()))
    return _DEVICE_UUID


def _local_ip():
    try:
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        s.connect(("8.8.8.8", 80))
        return s.getsockname()[0]
    except Exception:
        return "127.0.0.1"
    finally:
        s.close()


def _build_scopes(name):
    scopes = (
        "onvif://www.onvif.org/type/video_encoder "
        "onvif://www.onvif.org/Profile/Streaming "
        f"onvif://www.onvif.org/name/{name}"
    )
    from config import config
    if config.get("fisheye_lens", False):
        scopes += " onvif://www.onvif.org/type/fisheye"
    return scopes


def _probe_match(message_id):
    from config import config
    ip = _local_ip()
    port = config.get("app_port", 8080)
    name = config.get("device_name", "piSkyCam")
    xaddr = f"http://{ip}:{port}/onvif/device_service"
    scopes = _build_scopes(name)
    return f"""<?xml version="1.0" encoding="UTF-8"?>
<s:Envelope xmlns:s="http://www.w3.org/2003/05/soap-envelope"
            xmlns:a="http://schemas.xmlsoap.org/ws/2004/08/addressing"
            xmlns:d="http://schemas.xmlsoap.org/ws/2005/04/discovery"
            xmlns:dn="http://www.onvif.org/ver10/network/wsdl">
  <s:Header>
    <a:Action>http://schemas.xmlsoap.org/ws/2005/04/discovery/ProbeMatches</a:Action>
    <a:MessageID>uuid:{uuid.uuid4()}</a:MessageID>
    <a:RelatesTo>{message_id}</a:RelatesTo>
    <a:To>http://schemas.xmlsoap.org/ws/2004/08/addressing/role/anonymous</a:To>
  </s:Header>
  <s:Body>
    <d:ProbeMatches>
      <d:ProbeMatch>
        <a:EndpointReference>
          <a:Address>urn:uuid:{_device_uuid()}</a:Address>
        </a:EndpointReference>
        <d:Types>dn:NetworkVideoTransmitter</d:Types>
        <d:Scopes>{scopes}</d:Scopes>
        <d:XAddrs>{xaddr}</d:XAddrs>
        <d:MetadataVersion>1</d:MetadataVersion>
      </d:ProbeMatch>
    </d:ProbeMatches>
  </s:Body>
</s:Envelope>""".encode("utf-8")


def _hello():
    from config import config
    ip = _local_ip()
    port = config.get("app_port", 8080)
    name = config.get("device_name", "piSkyCam")
    xaddr = f"http://{ip}:{port}/onvif/device_service"
    scopes = _build_scopes(name)
    return f"""<?xml version="1.0" encoding="UTF-8"?>
<s:Envelope xmlns:s="http://www.w3.org/2003/05/soap-envelope"
            xmlns:a="http://schemas.xmlsoap.org/ws/2004/08/addressing"
            xmlns:d="http://schemas.xmlsoap.org/ws/2005/04/discovery"
            xmlns:dn="http://www.onvif.org/ver10/network/wsdl">
  <s:Header>
    <a:Action>http://schemas.xmlsoap.org/ws/2005/04/discovery/Hello</a:Action>
    <a:MessageID>uuid:{uuid.uuid4()}</a:MessageID>
    <a:To>urn:schemas-xmlsoap-org:ws:2005:04:discovery</a:To>
  </s:Header>
  <s:Body>
    <d:Hello>
      <a:EndpointReference>
        <a:Address>urn:uuid:{_device_uuid()}</a:Address>
      </a:EndpointReference>
      <d:Types>dn:NetworkVideoTransmitter</d:Types>
      <d:Scopes>{scopes}</d:Scopes>
      <d:XAddrs>{xaddr}</d:XAddrs>
      <d:MetadataVersion>1</d:MetadataVersion>
    </d:Hello>
  </s:Body>
</s:Envelope>""".encode("utf-8")


def _handle(sock, data, addr):
    if b"Probe" not in data:
        return
    # Extract MessageID from probe
    msg_id = ""
    try:
        idx = data.index(b"<a:MessageID>")
        end = data.index(b"</a:MessageID>", idx)
        msg_id = data[idx + 13:end].decode()
    except (ValueError, UnicodeDecodeError):
        pass

    try:
        sock.sendto(_probe_match(msg_id), addr)
        logger.debug("Sent ProbeMatch to %s", addr)
    except Exception as e:
        logger.warning("ProbeMatch send failed: %s", e)


def start_discovery():
    def run():
        try:
            sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM, socket.IPPROTO_UDP)
            sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
            try:
                sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEPORT, 1)
            except (AttributeError, OSError):
                pass
            sock.bind(("", MCAST_PORT))
            mreq = struct.pack("4sL", socket.inet_aton(MCAST_ADDR), socket.INADDR_ANY)
            sock.setsockopt(socket.IPPROTO_IP, socket.IP_ADD_MEMBERSHIP, mreq)
            sock.settimeout(1.0)

            # Announce ourselves on startup
            try:
                hello_sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
                hello_sock.setsockopt(socket.IPPROTO_IP, socket.IP_MULTICAST_TTL, 2)
                hello_sock.sendto(_hello(), (MCAST_ADDR, MCAST_PORT))
                hello_sock.close()
            except Exception as e:
                logger.warning("Hello send failed: %s", e)

            logger.info("WS-Discovery listening on UDP %d", MCAST_PORT)
            while True:
                try:
                    data, addr = sock.recvfrom(65535)
                    _handle(sock, data, addr)
                except socket.timeout:
                    pass
                except Exception as e:
                    logger.debug("Discovery recv: %s", e)
        except Exception as e:
            logger.error("WS-Discovery failed: %s", e)

    t = threading.Thread(target=run, daemon=True, name="discovery")
    t.start()
    return t
