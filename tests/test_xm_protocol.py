import os
import socket
import struct
import sys

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))

from custom_components.wjg_camera.coordinator import (
    XM_FILELIST_REQ,
    XM_KEEPALIVE_REQ,
    XM_LOGIN_REQ,
    XM_MOTION_REQ,
    XM_RECORD_START,
    XM_RECORD_STOP,
    XMClient,
    xm_packet,
    xm_parse,
)
from tests_helpers import private_name as _private_name


def test_xm_packet_contains_payload_and_header():
    data = {"foo": "bar", "n": 1}
    packet = xm_packet(0x1234, 7, 0x0601, data)

    assert isinstance(packet, bytes)
    assert len(packet) > 20
    assert packet[0] == 0xFF
    assert packet[1] == 0x01


def test_xm_parse_returns_msg_id_and_body():
    original = {"Ret": 100, "SessionID": "0xABCD"}
    packet = xm_packet(0x1, 1, XM_LOGIN_REQ, original)

    msg_id, body = xm_parse(packet)

    assert msg_id == XM_LOGIN_REQ
    assert body["Ret"] == 100
    assert body["SessionID"] == "0xABCD"


def test_xm_parse_handles_short_data():
    msg_id, body = xm_parse(b"abc")
    assert msg_id == 0
    assert body == {}


def test_xm_parse_handles_invalid_json_body():
    payload = b"{invalid-json"
    header = struct.pack("<BBHIIBBHI", 0xFF, 0x01, 0x0000, 0x1, 1, 0x00, 0x00, XM_LOGIN_REQ, len(payload))
    msg_id, body = xm_parse(header + payload)

    assert msg_id == XM_LOGIN_REQ
    assert body == {}


def test_xmclient_high_level_commands_use_expected_message_ids(monkeypatch):
    client = XMClient("192.168.1.10", 34567, "admin", "")
    seen = []

    def fake_send_recv(msg_id, data, recv_size=2048):
        seen.append((msg_id, data, recv_size))
        if msg_id == XM_FILELIST_REQ:
            return {"Found": [{"name": "file.h264"}]}
        return {"Ret": 100}

    monkeypatch.setattr(client, "_send_recv", fake_send_recv)

    assert client.keepalive() is True
    assert client.start_recording() is True
    assert client.stop_recording() is True
    assert client.get_motion_state() is True
    assert client.ptz_command(0x10, speed=4) is True
    assert client.get_file_list() == [{"name": "file.h264"}]

    assert seen[0][0] == XM_KEEPALIVE_REQ
    assert seen[1][0] == XM_RECORD_START
    assert seen[2][0] == XM_RECORD_STOP
    assert seen[3][0] == XM_MOTION_REQ
    assert seen[4][0] == 0x0601
    assert seen[5][0] == XM_FILELIST_REQ


def test_xmclient_send_recv_returns_empty_when_no_socket():
    client = XMClient("192.168.1.10", 34567, "admin", "")
    assert getattr(client, _private_name("send_recv"))(XM_KEEPALIVE_REQ, {"Type": "KeepAlive"}) == {}


def test_xmclient_login_success_sets_session(monkeypatch):
    client = XMClient("192.168.1.10", 34567, "admin", "pass")
    monkeypatch.setattr(
        client,
        _private_name("send_recv"),
        lambda msg_id, data, recv_size=2048: {"Ret": 100, "SessionID": "0x2A"},
    )

    assert getattr(client, _private_name("login"))() is True
    assert getattr(client, _private_name("session_id")) == 0x2A


def test_xmclient_login_failure_returns_false(monkeypatch):
    client = XMClient("192.168.1.10", 34567, "admin", "pass")
    monkeypatch.setattr(
        client,
        _private_name("send_recv"),
        lambda msg_id, data, recv_size=2048: {"Ret": 500},
    )

    assert getattr(client, _private_name("login"))() is False


def test_xmclient_connect_handles_socket_error(monkeypatch):
    class FailingSocket:
        def settimeout(self, timeout):
            _ = timeout

        def connect(self, address):
            _ = address
            raise OSError("connect failed")

    monkeypatch.setattr(socket, "socket", lambda *args, **kwargs: FailingSocket())
    client = XMClient("192.168.1.10", 34567, "admin", "")

    assert client.connect() is False


def test_xmclient_connect_success_calls_login(monkeypatch):
    class OkSocket:
        def settimeout(self, timeout):
            _ = timeout

        def connect(self, address):
            _ = address
            return None

    monkeypatch.setattr(socket, "socket", lambda *args, **kwargs: OkSocket())
    client = XMClient("192.168.1.10", 34567, "admin", "")
    monkeypatch.setattr(client, _private_name("login"), lambda: True)

    assert client.connect() is True


def test_xmclient_send_recv_parses_response_when_socket_exists():
    class DummySocket:
        def __init__(self):
            self.sent = b""

        def sendall(self, payload):
            self.sent = payload

        def recv(self, recv_size):
            _ = recv_size
            return xm_packet(0x1, 1, XM_KEEPALIVE_REQ, {"Ret": 100})

    client = XMClient("192.168.1.10", 34567, "admin", "")
    setattr(client, _private_name("sock"), DummySocket())

    body = getattr(client, _private_name("send_recv"))(XM_KEEPALIVE_REQ, {"Type": "KeepAlive"})

    assert body == {"Ret": 100}


def test_xmclient_disconnect_ignores_close_error_and_clears_socket():
    class BrokenCloseSocket:
        def close(self):
            raise OSError("already closed")

    client = XMClient("192.168.1.10", 34567, "admin", "")
    setattr(client, _private_name("sock"), BrokenCloseSocket())

    client.disconnect()

    assert getattr(client, _private_name("sock")) is None