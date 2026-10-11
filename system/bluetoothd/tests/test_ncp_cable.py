"""The cable NCP transport against a loopback socket and a fake session. Pure-python."""
import socket
import time

import pytest

from openpilot.system.bluetoothd import protocol
from openpilot.system.bluetoothd.ncp_cable import CABLE_ADDRESS, CABLE_SUBNET, NCP_CABLE_PORT, CableNCP


class FakeSession:
    def __init__(self):
        self.transports = {}
        self.frames = []

    def add_transport(self, name, send_fn):
        self.transports[name] = send_fn

    def remove_transport(self, name):
        self.transports.pop(name, None)

    def handle_frame(self, frame):
        self.frames.append(frame)
        return protocol.make_ack(frame.msg_type)


def wait_for(cond, timeout=3.0):
    end = time.monotonic() + timeout
    while time.monotonic() < end:
        if cond():
            return True
        time.sleep(0.01)
    return False


_open: list[socket.socket] = []


@pytest.fixture(autouse=True)
def close_clients():
    yield
    while _open:
        _open.pop().close()


@pytest.fixture
def cable():
    session = FakeSession()
    server = CableNCP(session, bind_addr='127.0.0.1', port=0, allowed_prefix='127.')
    server.start()
    assert wait_for(lambda: server.bound_port is not None)
    yield server, session
    server.stop()


def connect(server):
    sock = socket.create_connection(('127.0.0.1', server.bound_port), timeout=3)
    _open.append(sock)
    return sock


def read_frame(sock):
    buf = b''
    while True:
        buf += sock.recv(4096)
        frame, consumed = protocol.Frame.decode(buf)
        if frame is not None or consumed:
            return frame


def test_defaults_are_the_comma_end_of_the_usb_network_and_never_all_interfaces():
    assert CABLE_ADDRESS == '192.168.60.1' and CABLE_SUBNET == '192.168.60.' and NCP_CABLE_PORT == 5598
    assert CableNCP(FakeSession()).bind_addr != '0.0.0.0'


def test_a_phone_is_registered_as_the_cable_transport_and_removed_when_it_leaves(cable):
    server, session = cable
    c = connect(server)
    assert wait_for(lambda: 'cable' in session.transports)
    c.close()
    assert wait_for(lambda: 'cable' not in session.transports)


def test_a_request_is_answered_with_the_sessions_response(cable):
    server, session = cable
    c = connect(server)
    c.sendall(protocol.Frame.from_json(protocol.MessageType.DEVICE_CAPABILITIES, {}).encode())
    reply = read_frame(c)
    assert reply.msg_type == protocol.make_ack(protocol.MessageType.DEVICE_CAPABILITIES).msg_type
    assert session.frames[0].msg_type == protocol.MessageType.DEVICE_CAPABILITIES


def test_session_pushes_reach_the_phone_over_the_registered_transport(cable):
    server, session = cable
    c = connect(server)
    assert wait_for(lambda: 'cable' in session.transports)
    session.transports['cable'](protocol.Frame.from_json(protocol.MessageType.TELEMETRY_SYSTEM, {'ok': 1}).encode())
    assert read_frame(c).msg_type == protocol.MessageType.TELEMETRY_SYSTEM


def test_a_frame_split_across_reads_and_two_in_one_read_both_work(cable):
    server, session = cable
    c = connect(server)
    a = protocol.Frame.from_json(protocol.MessageType.CMD_CANCEL_NAV, {}).encode()
    b = protocol.Frame.from_json(protocol.MessageType.CMD_NAVIGATE, {'lat': 1.0}).encode()
    c.sendall(a[:3])
    time.sleep(0.05)
    c.sendall(a[3:] + b)
    assert wait_for(lambda: len(session.frames) == 2)
    assert [f.msg_type for f in session.frames] == [protocol.MessageType.CMD_CANCEL_NAV, protocol.MessageType.CMD_NAVIGATE]


def test_a_corrupt_frame_is_skipped_and_the_next_one_is_served(cable):
    server, session = cable
    c = connect(server)
    good = protocol.Frame.from_json(protocol.MessageType.CMD_CANCEL_NAV, {}).encode()
    bad = bytearray(protocol.Frame.from_json(protocol.MessageType.CMD_NAVIGATE, {'x': 1}).encode())
    bad[-1] ^= 0xFF
    c.sendall(bytes(bad) + good)
    assert wait_for(lambda: len(session.frames) == 1)
    assert session.frames[0].msg_type == protocol.MessageType.CMD_CANCEL_NAV


def test_a_peer_that_is_not_speaking_ncp_is_cut_off(cable):
    server, session = cable
    c = connect(server)
    assert wait_for(lambda: 'cable' in session.transports)
    c.sendall(b'\x00\x00\x00\x00' + b'GET / HTTP/1.1\r\n')   # a length no frame can have
    assert wait_for(lambda: 'cable' not in session.transports)
    c.settimeout(3)
    assert c.recv(1) == b''


def test_a_new_phone_replaces_the_old_one_and_the_old_one_does_not_unregister_the_new(cable):
    server, session = cable
    first = connect(server)
    assert wait_for(lambda: 'cable' in session.transports)
    second = connect(server)
    first.settimeout(3)
    assert wait_for(lambda: first.recv(1) == b'')   # the old link was closed
    assert 'cable' in session.transports            # the new one still owns the transport
    second.sendall(protocol.Frame.from_json(protocol.MessageType.CMD_CANCEL_NAV, {}).encode())
    assert read_frame(second) is not None


def test_a_peer_outside_the_cable_subnet_is_dropped():
    session = FakeSession()
    server = CableNCP(session, bind_addr='127.0.0.1', port=0, allowed_prefix='192.168.60.')
    server.start()
    try:
        assert wait_for(lambda: server.bound_port is not None)
        c = connect(server)
        c.settimeout(3)
        assert c.recv(1) == b''
        assert 'cable' not in session.transports
    finally:
        server.stop()


def test_it_keeps_trying_to_bind_until_the_network_interface_exists():
    session = FakeSession()
    server = CableNCP(session, bind_addr='203.0.113.9', port=0)   # not an address of this machine
    server.start()
    try:
        time.sleep(0.3)
        assert server.bound_port is None   # not bound, not crashed
    finally:
        server.stop()
