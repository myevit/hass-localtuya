"""Native sensor selection never invokes electrical Base64 heuristics."""

import pytest
from homeassistant.core import HomeAssistant
from custom_components.localtuya import sensor
from custom_components.localtuya.core.ha_entities.pet_feeders import encode
from .test_device import device, device_config
from .test_profile import meal


@pytest.mark.asyncio
async def test_native_sensor_uses_owned_state_and_standard_timestamp(tmp_path):
    hass = HomeAssistant(str(tmp_path))
    conf = device_config()
    conf["entities"].append(
        {"id": "1", "platform": "sensor", "friendly_name": "Meal plan"}
    )
    dev = device(hass, conf)
    await dev.feeder.start()
    assert hasattr(sensor, "make_sensor"), "native sensor selection missing"
    children = []
    entity = sensor.make_sensor(dev, conf, "1", add_entites_callback=children.extend)
    assert entity.unique_id == "local_synthetic-device_1"
    # This payload meets generic electrical decoder's dangerous heuristic.
    raw = encode([meal(), meal("22:00")])
    from types import SimpleNamespace

    dev._interface = SimpleNamespace(
        connection_generation=object(), is_connected=True, can_write_once=True
    )
    dev.feeder.received({"1": raw}, dev.generation)
    entity._status = {"1": raw}
    entity.status_updated()
    assert entity.native_value == 2
    assert len(children) == 1 and children[0].device_class == "timestamp"
    assert children[0].unique_id == "local_synthetic-device_last_feed"
    assert children[0].device_info["identifiers"] == {
        ("localtuya", "local_synthetic-device")
    }
    attrs = entity.extra_state_attributes
    assert attrs["native_feeder_profile"] == "ft11w" and attrs["meals"] == [
        meal(),
        meal("22:00"),
    ]
    assert not attrs["pending"]
    dev._interface = None
    await dev.close()
