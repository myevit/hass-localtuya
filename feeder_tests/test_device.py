"""Device lifecycle boundaries use real native coordinator, no sockets."""

import asyncio
from types import SimpleNamespace
import pytest
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import ServiceValidationError
from custom_components.localtuya.coordinator import TuyaDevice, HassLocalTuyaData
from .test_profile import config
from .test_protocol import Transport
from custom_components.localtuya.core.pytuya import TuyaProtocol


def device_config():
    return {
        **config(),
        "device_id": "synthetic-device",
        "host": "192.0.2.1",
        "local_key": "0123456789abcdef",
        "friendly_name": "Synthetic feeder",
    }


def device(hass, conf=None):
    entry = SimpleNamespace(entry_id="synthetic-entry")
    hass.data["localtuya"] = {entry.entry_id: HassLocalTuyaData(None, {})}
    return TuyaDevice(hass, entry, conf or device_config())


@pytest.mark.asyncio
async def test_device_owns_capability_and_blocks_raw_actuation_bypass(tmp_path):
    hass = HomeAssistant(str(tmp_path))
    dev = device(hass)
    assert (
        getattr(dev, "feeder", None) is not None
    ), "native capability not attached to TuyaDevice"
    await dev.feeder.start()
    proto = TuyaProtocol(dev.id, dev.local_key, 3.5, False, dev)
    proto.connection_made(Transport())
    proto.local_key = b"fedcba9876543210"
    dev._interface = proto
    assert dev.feeder_ready
    for dp in ("1", "2", "3"):
        with pytest.raises(ServiceValidationError):
            await dev.set_dp(1, dp)
        with pytest.raises(ServiceValidationError):
            await dev.set_dps({dp: 1})
    dev.status_updated({"1": ""})
    assert dev.feeder.revision == 0
    dev.status_received({"1": ""}, proto.connection_generation)
    assert dev.feeder.revision == 1
    await dev.close()
    assert dev.feeder.closed and not dev.feeder._saves


@pytest.mark.asyncio
async def test_close_cleans_up_when_feeder_store_fails(tmp_path, monkeypatch):
    hass = HomeAssistant(str(tmp_path))
    dev = device(hass)
    await dev.feeder.start()
    # A persisted reservation must stay fail-closed across a failed shutdown save.
    dev.feeder.feed_uncertain = True
    await dev.feeder.save()
    proto = TuyaProtocol(dev.id, dev.local_key, 3.5, False, dev)
    transport = Transport()
    proto.connection_made(transport)
    dev._interface = proto
    tasks = [asyncio.create_task(asyncio.Event().wait()) for _ in range(3)]
    dev._task_shutdown_entities, dev._task_reconnect, dev._task_connect = tasks
    await asyncio.sleep(0)
    failure = OSError("synthetic full disk: feeder uncertainty not saved")

    async def fail(_):
        raise failure

    monkeypatch.setattr(dev.feeder.store, "async_save", fail)
    try:
        with pytest.raises(OSError) as caught:
            await dev.close()
        assert caught.value is failure
        assert all(task.cancelled() for task in tasks)
        assert dev.is_closing and dev.feeder.closed and dev.feeder.feed_uncertain
        assert transport.closing and dev._interface is None
        await dev.close()
        assert transport.closing and dev._interface is None and not transport.writes
        restored = type(dev.feeder)(dev)
        await restored.start()
        assert restored.feed_uncertain
        with pytest.raises(ServiceValidationError):
            await restored.feed(8, True)
        await restored.stop()
    finally:
        for task in tasks:
            task.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)
        transport.close()


@pytest.mark.asyncio
async def test_four_unrelated_devices_have_no_capability_or_behavior_change(tmp_path):
    hass = HomeAssistant(str(tmp_path))
    for i in range(4):
        conf = device_config()
        conf.pop("feeder_profile")
        conf["device_id"] = f"synthetic-{i}"
        dev = device(hass, conf)
        assert hasattr(dev, "feeder"), "native optional capability field missing"
        assert dev.feeder is None
        # Existing disconnected non-sleeping set_dp remains a no-op.
        await dev.set_dp(3, "3")
        assert dev._pending_status == {}
        await dev.close()
