"""Device lifecycle boundaries use real native coordinator, no sockets."""

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
