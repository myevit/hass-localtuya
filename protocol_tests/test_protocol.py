"""Raw protocol integrity regressions; all identities and keys are synthetic."""

import asyncio
import json
import struct

import pytest

from custom_components.localtuya.core import pytuya


KEY = b"0123456789abcdef"


class Transport:
    def write(self, data):
        raise AssertionError("Receive-only test must not send")

    def is_closing(self):
        return False


class Listener:
    wants_exact_status = True

    def __init__(self):
        self.frames = []
        self.statuses = []
        self.sub_devices = {}

    def status_received(self, dps, generation):
        self.frames.append(dps)

    def status_updated(self, dps):
        self.statuses.append(dps.copy())


def protocol(version=3.5, identity="synthetic-a"):
    listener = Listener()
    proto = pytuya.TuyaProtocol(identity, KEY.decode(), version, False, listener)
    proto.connection_made(Transport())
    return proto, listener


def packet(proto, dps=None, seqno=7):
    raw = json.dumps({"dps": dps or {"1": "synthetic"}}).encode()
    if proto.version == 3.3:
        raw = proto.version_header + pytuya.AESCipher(KEY).encrypt(
            raw, use_base64=False
        )
    elif proto.version == 3.4:
        raw = pytuya.AESCipher(KEY).encrypt(
            proto.version_header + raw, use_base64=False
        )
    elif proto.version == 3.5:
        raw = proto.version_header + raw
    prefix = (
        pytuya.Affix.prefix_6699 if proto.version == 3.5 else pytuya.Affix.prefix_55aa
    )
    if proto.version != 3.5:
        raw = struct.pack(">I", 0) + raw
    msg = pytuya.TuyaMessage(
        seqno, pytuya.CMDType.STATUS, 0, raw, 0, prefix=prefix.value
    )
    return pytuya.parser.pack_message(
        msg, hmac_key=KEY if proto.version >= 3.4 else None
    )


def chosen_plaintext_packet(proto):
    body = b"\0" * 4 + proto.version_header + b'{"dps":{"1":"forged"}}'
    header = struct.pack(
        pytuya.MessagesFormat.HEADER_6699,
        pytuya.Affix.prefix_6699.value,
        0,
        7,
        pytuya.CMDType.STATUS,
        len(body) + 12 + 16,
    )
    return header + b"012345678901" + body + b"\0" * 16 + pytuya.Affix.suffix_6699.bin


@pytest.mark.asyncio
@pytest.mark.parametrize("version", [3.1, 3.3, 3.4, 3.5])
async def test_invalid_integrity_cannot_publish_or_complete_waiter(version):
    proto, listener = protocol(version)
    valid = packet(proto)
    invalid = bytearray(valid)
    invalid[-5] ^= 1  # CRC, HMAC or GCM tag, never the framing suffix.
    waiter = asyncio.get_running_loop().create_future()
    proto.dispatcher.listeners[7] = waiter
    proto.dispatcher.listener_commands[7] = {pytuya.CMDType.STATUS}
    proto.data_received(bytes(invalid))
    assert not waiter.done()
    assert listener.frames == listener.statuses == []
    assert proto.dps_cache == {}
    proto.data_received(valid)
    assert waiter.done()
    assert listener.statuses == [{"1": "synthetic"}]
    assert listener.frames == [{"1": "synthetic"}]


@pytest.mark.asyncio
async def test_invalid_gcm_chosen_plaintext_rejected_and_next_packet_recovers():
    proto, listener = protocol()
    waiter = asyncio.get_running_loop().create_future()
    proto.dispatcher.listeners[7] = waiter
    proto.dispatcher.listener_commands[7] = {pytuya.CMDType.STATUS}
    proto.data_received(chosen_plaintext_packet(proto))
    assert not waiter.done()
    assert listener.frames == listener.statuses == []
    assert proto.dps_cache == {}
    proto.data_received(packet(proto))
    assert waiter.done()
    assert listener.frames == [{"1": "synthetic"}]


@pytest.mark.asyncio
async def test_parser_never_returns_unauthenticated_gcm_plaintext():
    proto, _ = protocol()
    with pytest.raises(pytuya.parser.DecodeError):
        pytuya.parser.unpack_message(chosen_plaintext_packet(proto), hmac_key=KEY)


def test_legacy_message_defaults_remain_valid():
    assert pytuya.TuyaMessage(1, pytuya.CMDType.STATUS, 0, b"{}", 0).crc_good


@pytest.mark.asyncio
async def test_legacy_default_integrity_message_still_dispatches():
    proto, listener = protocol(3.1)
    proto.dispatcher._dispatch(
        pytuya.TuyaMessage(1, pytuya.CMDType.STATUS, 0, b'{"dps":{"1":true}}', 0)
    )
    assert listener.frames == listener.statuses == [{"1": True}]


@pytest.mark.asyncio
@pytest.mark.parametrize("version", [3.1, 3.3, 3.4, 3.5])
async def test_every_split_preserves_real_frame(version):
    source, _ = protocol(version)
    wire = packet(source)
    for split in range(1, len(wire)):
        proto, listener = protocol(version)
        proto.data_received(wire[:split])
        assert listener.frames == []
        assert proto.dispatcher.buffer == wire[:split]
        proto.data_received(wire[split:])
        assert listener.frames == [{"1": "synthetic"}], split
        assert proto.dispatcher.buffer == b""


@pytest.mark.asyncio
@pytest.mark.parametrize("version", [3.1, 3.3, 3.4, 3.5])
async def test_bytewise_and_coalesced_partial_frames(version):
    proto, listener = protocol(version)
    first = packet(proto)
    second = packet(proto, {"4": "ready"}, 8)
    for byte in first:
        proto.data_received(bytes([byte]))
    assert listener.frames == [{"1": "synthetic"}]
    proto.data_received(second + first[:23])
    assert listener.frames == [{"1": "synthetic"}, {"4": "ready"}]
    assert proto.dispatcher.buffer == first[:23]
    proto.data_received(first[23:] + second)
    assert listener.frames == [
        {"1": "synthetic"},
        {"4": "ready"},
        {"1": "synthetic"},
        {"4": "ready"},
    ]
    assert proto.dispatcher.buffer == b""


@pytest.mark.asyncio
@pytest.mark.parametrize("version", [3.1, 3.3, 3.4, 3.5])
@pytest.mark.parametrize("length", [0, 1, 7, 27, 2001, 0xFFFFFFFF])
async def test_invalid_lengths_resync_and_bound_buffer(version, length):
    proto, listener = protocol(version)
    wire = packet(proto)
    fmt = (
        pytuya.MessagesFormat.HEADER_6699
        if version == 3.5
        else pytuya.MessagesFormat.HEADER_55AA
    )
    header_len = struct.calcsize(fmt)
    fields = list(struct.unpack(fmt, wire[:header_len]))
    fields[-1] = length
    invalid = struct.pack(fmt, *fields)
    # A finite plausible length is allowed to wait for its declared body.
    proto.data_received(invalid + b"x" * 2100)
    assert len(proto.dispatcher.buffer) <= 3
    assert listener.frames == []
    proto.data_received(wire)
    assert listener.frames == [{"1": "synthetic"}]
    assert proto.dispatcher.buffer == b""


@pytest.mark.asyncio
@pytest.mark.parametrize("version", [3.3, 3.5])
async def test_noise_split_magic_and_bad_suffix_recover(version):
    proto, listener = protocol(version)
    wire = packet(proto)
    proto.data_received(b"noise" * 10000 + wire[:3])
    assert proto.dispatcher.buffer == wire[:3]
    proto.data_received(wire[3:])
    assert listener.frames == [{"1": "synthetic"}]
    broken = wire[:-4] + b"bad!"
    proto.data_received(broken + wire)
    assert listener.frames == [{"1": "synthetic"}] * 2
    assert proto.dispatcher.buffer == b""


@pytest.mark.asyncio
async def test_equal_sequence_numbers_remain_per_device():
    a, la = protocol(identity="synthetic-a")
    b, lb = protocol(identity="synthetic-b")
    wa = asyncio.get_running_loop().create_future()
    wb = asyncio.get_running_loop().create_future()
    a.dispatcher.listeners[7] = wa
    b.dispatcher.listeners[7] = wb
    a.dispatcher.listener_commands[7] = {pytuya.CMDType.STATUS}
    b.dispatcher.listener_commands[7] = {pytuya.CMDType.STATUS}
    wire = packet(a, {"1": "a"})
    a.data_received(wire[:23])
    b.data_received(packet(b, {"1": "b"}))
    assert wb.done() and not wa.done()
    assert lb.frames == [{"1": "b"}] and la.frames == []
    a.data_received(wire[23:])
    assert wa.done() and la.frames == [{"1": "a"}]


@pytest.mark.asyncio
@pytest.mark.parametrize("version", [3.1, 3.3, 3.4, 3.5])
@pytest.mark.parametrize("maximum", [False, True])
async def test_empty_response_and_existing_maximum_length(version, maximum):
    proto, listener = protocol(version)
    is_6699 = version == 3.5
    overhead = 32 if is_6699 else 40 if version == 3.4 else 12
    payload = b"x" * (2000 - overhead) if maximum else b""
    if not is_6699:
        payload = struct.pack(">I", 0) + payload
    msg = pytuya.TuyaMessage(
        7,
        pytuya.CMDType.HEART_BEAT,
        0,
        payload,
        0,
        prefix=(
            pytuya.Affix.prefix_6699 if is_6699 else pytuya.Affix.prefix_55aa
        ).value,
    )
    wire = pytuya.parser.pack_message(msg, hmac_key=KEY if version >= 3.4 else None)
    header = pytuya.parser.parse_header(wire)
    assert header.length == (2000 if maximum else overhead)
    waiter = asyncio.get_running_loop().create_future()
    proto.dispatcher.listeners[7] = waiter
    proto.dispatcher.listener_commands[7] = {pytuya.CMDType.HEART_BEAT}
    proto.data_received(wire[:-1])
    assert not waiter.done()
    assert len(proto.dispatcher.buffer) < 2022
    proto.data_received(wire[-1:])
    assert waiter.done() and waiter.result().crc_good
    assert listener.frames == listener.statuses == []
    assert proto.dispatcher.buffer == b""


@pytest.mark.asyncio
@pytest.mark.parametrize("version", [3.1, 3.3, 3.4, 3.5])
async def test_corrupt_length_does_not_consume_following_authenticated_frame(version):
    proto, listener = protocol(version)
    bad = packet(proto, {"1": "bad"}, 6)
    good = packet(proto, {"1": "good"}, 7)
    fmt = (
        pytuya.MessagesFormat.HEADER_6699
        if version == 3.5
        else pytuya.MessagesFormat.HEADER_55AA
    )
    size = struct.calcsize(fmt)
    fields = list(struct.unpack(fmt, bad[:size]))
    fields[-1] = len(bad) + len(good) - size - (4 if version == 3.5 else 0)
    corrupt = struct.pack(fmt, *fields) + bad[size:]
    proto.data_received(corrupt + good)
    assert listener.frames == [{"1": "good"}]
    assert listener.statuses == [{"1": "good"}]
    assert proto.dispatcher.buffer == b""

    # Wait for a complete candidate before rejecting its untrusted length.
    proto, listener = protocol(version)
    proto.data_received(corrupt + good[:-1])
    assert listener.frames == []
    assert proto.dispatcher.buffer == corrupt + good[:-1]
    # After rejection, recover the embedded frame and retain a partial sibling.
    sibling = packet(proto, {"4": "later"}, 8)
    proto.data_received(good[-1:] + sibling[:23])
    assert listener.frames == [{"1": "good"}]
    assert proto.dispatcher.buffer == sibling[:23]
    proto.data_received(sibling[23:])
    assert listener.frames == [{"1": "good"}, {"4": "later"}]
    assert proto.dispatcher.buffer == b""


@pytest.mark.asyncio
async def test_coalesced_bad_tag_then_valid_frame():
    proto, listener = protocol()
    waiter = asyncio.get_running_loop().create_future()
    proto.dispatcher.listeners[7] = waiter
    proto.dispatcher.listener_commands[7] = {pytuya.CMDType.STATUS}
    proto.data_received(chosen_plaintext_packet(proto) + packet(proto))
    assert waiter.done() and waiter.result().crc_good
    assert listener.frames == listener.statuses == [{"1": "synthetic"}]
    assert proto.dispatcher.buffer == b""


@pytest.mark.asyncio
@pytest.mark.parametrize("version", [3.1, 3.3, 3.4, 3.5])
async def test_coalesced_invalid_integrity_then_valid_frame(version):
    proto, listener = protocol(version)
    waiter = asyncio.get_running_loop().create_future()
    proto.dispatcher.listeners[7] = waiter
    proto.dispatcher.listener_commands[7] = {pytuya.CMDType.STATUS}
    invalid = bytearray(packet(proto, {"1": "forged"}, 6))
    invalid[-5] ^= 1
    proto.data_received(bytes(invalid) + packet(proto))
    assert waiter.done() and waiter.result().crc_good
    assert listener.frames == listener.statuses == [{"1": "synthetic"}]
    assert proto.dispatcher.buffer == b""
