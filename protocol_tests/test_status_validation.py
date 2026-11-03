"""Receive-boundary regressions using authenticated synthetic wire frames."""
import asyncio
import json
import struct

import pytest
from custom_components.localtuya.core import pytuya
from test_protocol import KEY, protocol


def wire(proto, obj, cmd=pytuya.CMDType.STATUS, seq=7):
    raw = json.dumps(obj).encode()
    if proto.version == 3.3:
        raw = proto.version_header + pytuya.AESCipher(KEY).encrypt(raw, use_base64=False)
    elif proto.version == 3.4:
        raw = pytuya.AESCipher(KEY).encrypt(proto.version_header + raw, use_base64=False)
    elif proto.version == 3.5:
        raw = proto.version_header + raw
    if proto.version != 3.5:
        raw = struct.pack('>I', 0) + raw
    prefix = pytuya.Affix.prefix_6699 if proto.version == 3.5 else pytuya.Affix.prefix_55aa
    msg = pytuya.TuyaMessage(seq, cmd, 0, raw, 0, prefix=prefix.value)
    return pytuya.parser.pack_message(msg, hmac_key=KEY if proto.version >= 3.4 else None)


@pytest.mark.asyncio
@pytest.mark.parametrize('version', [3.1, 3.3, 3.4, 3.5])
@pytest.mark.parametrize('exact', [True, False])
@pytest.mark.parametrize('obj', [{'data': None}, {'dps': 42}, None, [],
                                 {'data': {'dps': []}}, {'dps': {}, 'data': 42}])
async def test_malformed_status_does_not_strand_valid_coalesced_frame(version, exact, obj):
    proto, listener = protocol(version)
    listener.wants_exact_status = exact
    proto.data_received(wire(proto, {'dps': {'14': 6}}))
    listener.frames.clear()
    listener.statuses.clear()
    proto.data_received(wire(proto, obj) + wire(proto, {'dps': {'14': 8}}, seq=8))
    assert listener.statuses == [{'14': 8}]
    assert listener.frames == ([{'14': 8}] if exact else [])
    assert proto.dps_cache == {'parent': {'14': 8}}
    assert proto.dispatched_dps == {'14': 8}
    assert proto.dispatcher.buffer == b''


@pytest.mark.asyncio
@pytest.mark.parametrize('version', [3.1, 3.3, 3.4, 3.5])
@pytest.mark.parametrize('exact', [True, False])
async def test_invalid_individual_dp_keeps_valid_siblings_and_cached_value(version, exact):
    proto, listener = protocol(version)
    listener.wants_exact_status = exact
    proto.data_received(wire(proto, {'dps': {'14': 6}}))
    listener.frames.clear()
    proto.data_received(wire(proto, {'data': {'dps': {
        '14': None, '15': [], '16': {}, '17': float('nan'),
        'unknown': 'opaque', '4': 'standby', '18': True, '19': 2.5,
    }}}))
    good = {'unknown': 'opaque', '4': 'standby', '18': True, '19': 2.5}
    assert listener.frames == ([good] if exact else [])
    assert listener.statuses[-1] == {'14': 6, **good}
    assert proto.dispatched_dps == good
