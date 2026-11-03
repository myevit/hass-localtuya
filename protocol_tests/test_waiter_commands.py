"""Command correlation across the raw receive boundary."""

import asyncio
import struct

import pytest
from custom_components.localtuya.core import pytuya
from test_protocol import KEY, protocol
from test_status_validation import wire

C = pytuya.CMDType


@pytest.mark.asyncio
@pytest.mark.parametrize("version", [3.1, 3.3, 3.4, 3.5])
@pytest.mark.parametrize("dev_type", ["type_0a", "type_0d"])
async def test_actual_status_task_ignores_same_sequence_wrong_command(
    version, dev_type
):
    proto, listener = protocol(version)
    proto.dev_type = dev_type
    writes = []
    proto.transport.write = writes.append

    # Keep real status/exchange and real wire encoding; only skip session negotiation.
    async def negotiated():
        return True

    proto._negotiate_session_key = negotiated
    task = asyncio.create_task(proto.status())
    for _ in range(100):
        if proto.dispatcher.listeners or task.done():
            break
        await asyncio.sleep(0)
    assert proto.dispatcher.listeners
    request_cmd = pytuya.parser.parse_header(writes[0])
    proto.data_received(wire(proto, {}, C.HEART_BEAT, request_cmd.seqno))
    await asyncio.sleep(0)
    assert not task.done()
    proto.data_received(wire(proto, {"dps": {"14": 8}}, C.STATUS, request_cmd.seqno))
    assert await task == {"14": 8}
    assert listener.frames == [{"14": 8}]


@pytest.mark.asyncio
@pytest.mark.parametrize("version", [3.1, 3.3, 3.4, 3.5])
async def test_unsolicited_status_publishes_without_completing_control(version):
    proto, listener = protocol(version)
    task = asyncio.create_task(proto.dispatcher.wait_for(7, C.CONTROL, timeout=1))
    await asyncio.sleep(0)
    proto.data_received(wire(proto, {"dps": {"14": 8}}, C.STATUS))
    await asyncio.sleep(0)
    assert not task.done()
    assert listener.statuses == [{"14": 8}]
    proto.data_received(wire(proto, {}, C.CONTROL_NEW))
    assert (await task).cmd == C.CONTROL_NEW


@pytest.mark.asyncio
@pytest.mark.parametrize("version", [3.1, 3.3, 3.4, 3.5])
@pytest.mark.parametrize(
    "request_cmd,response,seq",
    [
        (C.DP_QUERY, C.STATUS, 7),
        (C.DP_QUERY, C.DP_QUERY, 7),
        (C.DP_QUERY_NEW, C.STATUS, 7),
        (C.DP_QUERY_NEW, C.DP_QUERY_NEW, 7),
        (C.CONTROL, C.CONTROL, 7),
        (C.CONTROL, C.CONTROL_NEW, 7),
        (C.CONTROL_NEW, C.CONTROL, 7),
        (C.CONTROL_NEW, C.CONTROL_NEW, 7),
        (C.HEART_BEAT, C.HEART_BEAT, -100),
        (C.UPDATEDPS, C.STATUS, -101),
        (C.UPDATEDPS, C.UPDATEDPS, -101),
        (C.SESS_KEY_NEG_START, C.SESS_KEY_NEG_RESP, -102),
        (C.LAN_EXT_STREAM, C.LAN_EXT_STREAM, -103),
    ],
)
async def test_allowed_response_commands_raw(version, request_cmd, response, seq):
    proto, _ = protocol(version)
    task = asyncio.create_task(proto.dispatcher.wait_for(seq, request_cmd, timeout=1))
    await asyncio.sleep(0)
    proto.data_received(
        wire(proto, {"dps": {"14": 8}} if response == C.STATUS else {}, response)
    )
    assert (await task).cmd == response
    assert not proto.dispatcher.listeners


@pytest.mark.asyncio
async def test_five_device_equal_sequence_futures_are_isolated():
    pairs = [protocol(3.5, identity=f"synthetic-{i}") for i in range(5)]
    tasks = [
        asyncio.create_task(p.dispatcher.wait_for(7, C.DP_QUERY, timeout=1))
        for p, _ in pairs
    ]
    await asyncio.sleep(0)
    for i, (proto, listener) in enumerate(pairs):
        proto.data_received(wire(proto, {}, C.HEART_BEAT))
        assert all(not t.done() for t in tasks[i:])
        frame = wire(proto, {"dps": {"14": i}})
        proto.data_received(frame[:23])
        proto.data_received(frame[23:])
        assert (await tasks[i]).seqno == 7
        assert listener.statuses == [{"14": i}]


@pytest.mark.asyncio
@pytest.mark.parametrize("version", [3.1, 3.3, 3.4, 3.5])
@pytest.mark.parametrize(
    "request_cmd,response,seq",
    [
        (C.CONTROL, C.CONTROL_NEW, 7),
        (C.CONTROL_NEW, C.CONTROL, 7),
        (C.HEART_BEAT, C.HEART_BEAT, -100),
        (C.UPDATEDPS, C.UPDATEDPS, -101),
    ],
)
async def test_allowed_zero_byte_ack(version, request_cmd, response, seq):
    proto, listener = protocol(version)
    task = asyncio.create_task(proto.dispatcher.wait_for(seq, request_cmd, timeout=1))
    await asyncio.sleep(0)
    raw = b"" if version == 3.5 else struct.pack(">I", 0)
    prefix = pytuya.Affix.prefix_6699 if version == 3.5 else pytuya.Affix.prefix_55aa
    msg = pytuya.TuyaMessage(7, response, 0, raw, 0, prefix=prefix.value)
    proto.data_received(
        pytuya.parser.pack_message(msg, hmac_key=KEY if version >= 3.4 else None)
    )
    assert (await task).payload == b""
    assert listener.frames == listener.statuses == []


@pytest.mark.asyncio
@pytest.mark.parametrize("version", [3.1, 3.3, 3.4, 3.5])
@pytest.mark.parametrize(
    "request_cmd,wrong,response,seq",
    [
        (C.HEART_BEAT, C.STATUS, C.HEART_BEAT, -100),
        (C.UPDATEDPS, C.HEART_BEAT, C.STATUS, -101),
        (C.SESS_KEY_NEG_START, C.STATUS, C.SESS_KEY_NEG_RESP, -102),
        (C.DP_QUERY, C.ENABLE_WIFI, C.STATUS, 7),
    ],
)
async def test_special_and_query_waiters_reject_other_commands(
    version, request_cmd, wrong, response, seq
):
    proto, _ = protocol(version)
    task = asyncio.create_task(proto.dispatcher.wait_for(seq, request_cmd, timeout=1))
    await asyncio.sleep(0)
    proto.data_received(wire(proto, {"dps": {"14": 7}}, wrong))
    # Inspect the real registered future, not merely the scheduling state of task.
    assert not proto.dispatcher.listeners[seq].done()
    proto.data_received(wire(proto, {"dps": {"14": 8}}, response))
    assert (await task).cmd == response
    assert not proto.dispatcher.listener_commands
