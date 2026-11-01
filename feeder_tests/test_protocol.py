"""Native protocol tests: never open a network socket."""

import asyncio
import json
from types import SimpleNamespace

import pytest
from custom_components.localtuya.core import pytuya


class Listener:
    wants_exact_status = True

    def __init__(self):
        self.frames = []
        self.statuses = []
        self.sub_devices = {}

    def status_received(self, dps, generation):
        self.frames.append((dps, generation))

    def status_updated(self, dps):
        self.statuses.append(dps.copy())


class Transport:
    def __init__(self):
        self.writes = []
        self.closing = False

    def write(self, data):
        self.writes.append(data)

    def is_closing(self):
        return self.closing

    def close(self):
        self.closing = True


def protocol():
    listener = Listener()
    proto = pytuya.TuyaProtocol(
        "synthetic-device", "0123456789abcdef", 3.5, False, listener
    )
    proto.connection_made(Transport())
    proto.local_key = b"fedcba9876543210"
    proto.dispatcher.local_key = proto.local_key
    return proto, listener


@pytest.mark.asyncio
async def test_one_shot_is_explicit_no_retry_and_requires_negotiated_connection():
    proto, _ = protocol()
    assert hasattr(proto, "set_dps_once"), "native one-shot API is missing"
    await proto.set_dps_once({"3": 8})
    assert len(proto.transport.writes) == 1
    frame = pytuya.parser.unpack_message(
        proto.transport.writes[0], hmac_key=proto.local_key, no_retcode=True
    )
    assert frame.cmd == pytuya.CMDType.CONTROL_NEW
    assert json.loads(frame.payload[15:])["data"]["dps"] == {"3": 8}
    proto.local_key = proto.real_local_key
    with pytest.raises(ConnectionError):
        await proto.set_dps_once({"3": 8})
    assert len(proto.transport.writes) == 1


@pytest.mark.asyncio
async def test_exact_frame_hook_does_not_replay_merged_dp1():
    proto, listener = protocol()
    # Feed the real dispatcher callback, retaining the real decoder.
    for dps in ({"1": ""}, {"4": "standby", "13": 0}, {"1": ""}):
        payload = json.dumps({"dps": dps}).encode()
        proto.dispatcher._dispatch(
            SimpleNamespace(seqno=0, cmd=pytuya.CMDType.STATUS, payload=payload)
        )
    assert [frame[0] for frame in listener.frames] == [
        {"1": ""},
        {"4": "standby", "13": 0},
        {"1": ""},
    ]
    assert listener.statuses[-2] == {"1": "", "4": "standby", "13": 0}
    assert listener.frames[0][1] is proto.connection_generation


def incoming(proto, dps, cmd=pytuya.CMDType.STATUS, seqno=0):
    payload = proto.version_header + json.dumps({"dps": dps}).encode()
    msg = pytuya.TuyaMessage(
        seqno, cmd, 0, payload, 0, True, pytuya.Affix.prefix_6699.value, True
    )
    return pytuya.parser.pack_message(msg, hmac_key=proto.local_key)


@pytest.mark.asyncio
async def test_real_encrypted_frames_exact_subset_and_ack_not_receipt():
    proto, listener = protocol()
    wire = incoming(proto, {"1": ""})
    proto.data_received(wire)
    proto.data_received(incoming(proto, {"4": "standby", "14": 8}))
    proto.data_received(
        incoming(proto, {"1": "fake_ack"}, pytuya.CMDType.CONTROL_NEW, 1)
    )
    assert [x[0] for x in listener.frames] == [{"1": ""}, {"4": "standby", "14": 8}]
    assert listener.statuses[-1] == {"1": "", "4": "standby", "14": 8}


@pytest.mark.asyncio
async def test_actual_query_response_is_receipt_and_no_dp1_query_never_replays_it():
    proto, listener = protocol()
    proto.data_received(incoming(proto, {"1": ""}, pytuya.CMDType.DP_QUERY_NEW, 7))
    proto.data_received(
        incoming(proto, {"4": "standby"}, pytuya.CMDType.DP_QUERY_NEW, 8)
    )
    assert [x[0] for x in listener.frames] == [{"1": ""}, {"4": "standby"}]


@pytest.mark.asyncio
async def test_status_consumed_by_query_waiter_is_still_one_exact_receipt():
    proto, listener = protocol()
    waiter = asyncio.get_running_loop().create_future()
    proto.dispatcher.listeners[proto.dispatcher.RESET_SEQNO] = waiter
    proto.data_received(incoming(proto, {"1": ""}))
    assert waiter.done() and [x[0] for x in listener.frames] == [{"1": ""}]
    assert listener.statuses == []  # legacy waiter behavior is unchanged


@pytest.mark.asyncio
async def test_generic_listener_keeps_legacy_merged_cache_and_no_new_frame_decode():
    proto, listener = protocol()
    listener.wants_exact_status = False
    proto.data_received(incoming(proto, {"1": ""}))
    proto.data_received(incoming(proto, {"4": "standby"}))
    assert not listener.frames and listener.statuses[-1] == {"1": "", "4": "standby"}


@pytest.mark.asyncio
async def test_one_shot_error_does_not_retry_or_enter_exchange_on_type_change():
    proto, _ = protocol()

    def fail(data):
        proto.transport.writes.append(data)
        proto.dev_type = "type_0d"
        raise OSError("synthetic write error")

    proto.transport.write = fail
    attempted = []
    with pytest.raises(OSError):
        await proto.set_dps_once({"3": 8}, lambda: attempted.append(True))
    assert attempted == [True] and len(proto.transport.writes) == 1
