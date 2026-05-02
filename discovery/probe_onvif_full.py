"""Full ONVIF capability probe for XM camera at 192.168.178.49"""
import requests
import xml.etree.ElementTree as ET
import hashlib, base64, datetime, os, re

IP = "192.168.178.49"
PORT = 8899
USER = "admin"
PASS = ""


def wsse_header(user, passwd):
    nonce = os.urandom(16)
    created = datetime.datetime.utcnow().strftime("%Y-%m-%dT%H:%M:%SZ")
    digest = base64.b64encode(
        hashlib.sha1(nonce + created.encode() + passwd.encode()).digest()
    ).decode()
    nonce_b64 = base64.b64encode(nonce).decode()
    return (
        '<wsse:Security xmlns:wsse="http://docs.oasis-open.org/wss/2004/01/oasis-200401-wss-wssecurity-secext-1.0.xsd">'
        "<wsse:UsernameToken>"
        f"<wsse:Username>{user}</wsse:Username>"
        f'<wsse:Password Type="http://docs.oasis-open.org/wss/2004/01/oasis-200401-wss-username-token-profile-1.0#PasswordDigest">{digest}</wsse:Password>'
        f'<wsse:Nonce EncodingType="http://docs.oasis-open.org/wss/2004/01/oasis-200401-wss-wssecurity-secext-1.0.xsd#Base64Binary">{nonce_b64}</wsse:Nonce>'
        f'<wsu:Created xmlns:wsu="http://docs.oasis-open.org/wss/2004/01/oasis-200401-wss-wssecurity-utility-1.0.xsd">{created}</wsu:Created>'
        "</wsse:UsernameToken>"
        "</wsse:Security>"
    )


def onvif_request(service_path, body, use_auth=True):
    url = f"http://{IP}:{PORT}{service_path}"
    auth = wsse_header(USER, PASS) if use_auth else ""
    envelope = (
        '<?xml version="1.0" encoding="utf-8"?>'
        '<s:Envelope xmlns:s="http://www.w3.org/2003/05/soap-envelope"'
        ' xmlns:tds="http://www.onvif.org/ver10/device/wsdl"'
        ' xmlns:trt="http://www.onvif.org/ver10/media/wsdl"'
        ' xmlns:tptz="http://www.onvif.org/ver20/ptz/wsdl"'
        ' xmlns:tptz2="http://www.onvif.org/ver10/ptz/wsdl"'
        ' xmlns:tt="http://www.onvif.org/ver10/schema"'
        ' xmlns:timg="http://www.onvif.org/ver20/imaging/wsdl"'
        ' xmlns:tev="http://www.onvif.org/ver10/events/wsdl"'
        ' xmlns:xsd="http://www.w3.org/2001/XMLSchema">'
        f"<s:Header>{auth}</s:Header>"
        f"<s:Body>{body}</s:Body>"
        "</s:Envelope>"
    )
    try:
        r = requests.post(
            url,
            data=envelope.encode("utf-8"),
            headers={"Content-Type": "application/soap+xml; charset=utf-8"},
            timeout=5,
        )
        return r.status_code, r.text
    except Exception as e:
        return 0, str(e)


def pretty_xml(text):
    try:
        root = ET.fromstring(text)
        return ET.tostring(root, encoding="unicode")
    except:
        return text


def extract_text_values(xml_text, max_chars=4000):
    """Extract meaningful text values from XML response"""
    try:
        # Remove namespaces for readability
        clean = re.sub(r' xmlns[^"]*"[^"]*"', '', xml_text)
        clean = re.sub(r'<[^/][^>]*:([^>]+)>', r'<\1>', clean)
        clean = re.sub(r'</[^>]+:([^>]+)>', r'</\1>', clean)
        return clean[:max_chars]
    except:
        return xml_text[:max_chars]


print("=" * 60)
print("ONVIF Full Capability Probe")
print(f"Target: {IP}:{PORT}")
print("=" * 60)

# --- GetCapabilities ---
print("\n### GetCapabilities ###")
s, t = onvif_request("/onvif/device_service",
    "<tds:GetCapabilities><tds:Category>All</tds:Category></tds:GetCapabilities>",
    use_auth=False)
print(f"Status: {s}")
print(extract_text_values(t, 5000))

# --- GetDeviceInformation ---
print("\n### GetDeviceInformation ###")
s, t = onvif_request("/onvif/device_service",
    "<tds:GetDeviceInformation/>", use_auth=False)
print(f"Status: {s}")
print(extract_text_values(t, 2000))

# --- GetSystemDateAndTime ---
print("\n### GetSystemDateAndTime ###")
s, t = onvif_request("/onvif/device_service",
    "<tds:GetSystemDateAndTime/>", use_auth=False)
print(f"Status: {s}")
print(extract_text_values(t, 1000))

# --- GetNetworkInterfaces ---
print("\n### GetNetworkInterfaces ###")
s, t = onvif_request("/onvif/device_service",
    "<tds:GetNetworkInterfaces/>", use_auth=True)
print(f"Status: {s}")
print(extract_text_values(t, 2000))

# --- GetServices ---
print("\n### GetServices ###")
s, t = onvif_request("/onvif/device_service",
    "<tds:GetServices><tds:IncludeCapability>true</tds:IncludeCapability></tds:GetServices>",
    use_auth=False)
print(f"Status: {s}")
print(extract_text_values(t, 4000))

# --- GetProfiles ---
print("\n### GetProfiles ###")
s, t = onvif_request("/onvif/media_service",
    "<trt:GetProfiles/>", use_auth=True)
print(f"Status: {s}")
print(extract_text_values(t, 4000))

# --- GetVideoEncoderConfigurations ---
print("\n### GetVideoEncoderConfigurations ###")
s, t = onvif_request("/onvif/media_service",
    "<trt:GetVideoEncoderConfigurations/>", use_auth=True)
print(f"Status: {s}")
print(extract_text_values(t, 3000))

# --- GetVideoSources ---
print("\n### GetVideoSources ###")
s, t = onvif_request("/onvif/media_service",
    "<trt:GetVideoSources/>", use_auth=True)
print(f"Status: {s}")
print(extract_text_values(t, 2000))

# --- GetAudioSources ---
print("\n### GetAudioSources ###")
s, t = onvif_request("/onvif/media_service",
    "<trt:GetAudioSources/>", use_auth=True)
print(f"Status: {s}")
print(extract_text_values(t, 1000))

# --- PTZ GetNodes ---
print("\n### PTZ GetNodes ###")
s, t = onvif_request("/onvif/ptz_service",
    "<tptz:GetNodes/>", use_auth=True)
print(f"Status: {s}")
print(extract_text_values(t, 3000))

# --- PTZ GetConfigurations ---
print("\n### PTZ GetConfigurations ###")
s, t = onvif_request("/onvif/ptz_service",
    "<tptz:GetConfigurations/>", use_auth=True)
print(f"Status: {s}")
print(extract_text_values(t, 3000))

# --- Imaging GetImagingSettings ---
print("\n### Imaging GetImagingSettings (Profile 000) ###")
s, t = onvif_request("/onvif/imaging_service",
    "<timg:GetImagingSettings><timg:VideoSourceToken>000</timg:VideoSourceToken></timg:GetImagingSettings>",
    use_auth=True)
print(f"Status: {s}")
print(extract_text_values(t, 3000))

# --- Imaging GetOptions ---
print("\n### Imaging GetOptions (Profile 000) ###")
s, t = onvif_request("/onvif/imaging_service",
    "<timg:GetOptions><timg:VideoSourceToken>000</timg:VideoSourceToken></timg:GetOptions>",
    use_auth=True)
print(f"Status: {s}")
print(extract_text_values(t, 3000))

# --- GetNTP ---
print("\n### GetNTP ###")
s, t = onvif_request("/onvif/device_service",
    "<tds:GetNTP/>", use_auth=True)
print(f"Status: {s}")
print(extract_text_values(t, 1000))

# --- GetDNS ---
print("\n### GetDNS ###")
s, t = onvif_request("/onvif/device_service",
    "<tds:GetDNS/>", use_auth=True)
print(f"Status: {s}")
print(extract_text_values(t, 1000))

# --- GetHostname ---
print("\n### GetHostname ###")
s, t = onvif_request("/onvif/device_service",
    "<tds:GetHostname/>", use_auth=False)
print(f"Status: {s}")
print(extract_text_values(t, 500))

# --- GetUsers ---
print("\n### GetUsers ###")
s, t = onvif_request("/onvif/device_service",
    "<tds:GetUsers/>", use_auth=True)
print(f"Status: {s}")
print(extract_text_values(t, 1000))

# --- Events GetEventProperties ---
print("\n### Events GetEventProperties ###")
s, t = onvif_request("/onvif/event_service",
    "<tev:GetEventProperties/>", use_auth=True)
print(f"Status: {s}")
print(extract_text_values(t, 4000))

# --- GetStreamUri for all profiles ---
print("\n### Stream URIs ###")
for token in ["000", "001", "002"]:
    s, t = onvif_request("/onvif/media_service",
        f"<trt:GetStreamUri><trt:StreamSetup><tt:Stream>RTP-Unicast</tt:Stream><tt:Transport><tt:Protocol>RTSP</tt:Protocol></tt:Transport></trt:StreamSetup><trt:ProfileToken>{token}</trt:ProfileToken></trt:GetStreamUri>",
        use_auth=True)
    print(f"  Profile {token} -> Status {s}")
    # Extract URI
    match = re.search(r'<[^>]*Uri[^>]*>([^<]+)</[^>]*Uri>', t)
    if match:
        print(f"  URI: {match.group(1)}")
    else:
        print(f"  Response: {t[:200]}")

# --- GetSnapshotUri ---
print("\n### Snapshot URIs ###")
for token in ["000", "001", "002"]:
    s, t = onvif_request("/onvif/media_service",
        f"<trt:GetSnapshotUri><trt:ProfileToken>{token}</trt:ProfileToken></trt:GetSnapshotUri>",
        use_auth=True)
    print(f"  Profile {token} -> Status {s}")
    match = re.search(r'<[^>]*Uri[^>]*>([^<]+)</[^>]*Uri>', t)
    if match:
        print(f"  URI: {match.group(1)}")
    else:
        print(f"  Response: {t[:200]}")

print("\n=== Probe abgeschlossen ===")
