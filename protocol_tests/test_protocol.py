"""Raw protocol integrity regressions; all identities and keys are synthetic."""

import asyncio
import json
import struct

import pytest

from custom_components.localtuya.core import pytuya


KEY = b"0123456789abcdef"


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
