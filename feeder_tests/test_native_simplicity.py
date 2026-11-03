"""Audit regressions: synthetic data, no device sockets or physical actions."""
import asyncio
import pytest
from homeassistant.exceptions import ServiceValidationError
from custom_components.localtuya.core.ha_entities.pet_feeders import encode
from custom_components.localtuya.feeder_entities import FeedButton, DeleteMeal, FeederError
from .test_profile import meal
from .test_state import state


@pytest.mark.asyncio
async def test_deliberate_feed_captures_quantity_once(tmp_path, monkeypatch):
    c, d = await state(tmp_path)
    d.feeder = c
    c.received({"1": encode([meal()]), "4": "standby", "13": 0, "101": "strong_power"}, d.generation)
    c.set_manual_portions(8)
    button = FeedButton(d)
    assert button.available
    original = c.save
    async def change_local_selection():
        await original()
        c.set_manual_portions(9)
    monkeypatch.setattr(c, "save", change_local_selection)
    task = asyncio.create_task(button.async_press())
    while not d.writes and not task.done():
        await asyncio.sleep(0)
    assert d.writes == [{"3": 8}]
    with pytest.raises(ServiceValidationError):
        await button.async_press()
    c.received({"4": "feeding"}, d.generation)
    c.received({"14": 8}, d.generation)
    await task
    await c.stop()


@pytest.mark.asyncio
async def test_delete_button_captures_slot_and_preserves_neighbors(tmp_path):
    c, d = await state(tmp_path)
    d.feeder = c
    meals = [meal("08:17"), meal("12:39"), meal("21:18")]
    c.received({"1": encode(meals)}, d.generation)
    button = DeleteMeal(d, 1)
    assert button.available
    assert not d.writes  # constructing/opening native controls never actuates
    task = asyncio.create_task(button.async_press())
    while not d.writes and not task.done():
        await asyncio.sleep(0)
    assert d.writes == [{"1": encode([meals[0], meals[2]])}]
    with pytest.raises(ServiceValidationError):
        await DeleteMeal(d, 0).async_press()
    c.received(d.writes[0], d.generation)
    await task
    await c.stop()


@pytest.mark.asyncio
async def test_missing_power_has_readable_readiness(tmp_path):
    from custom_components.localtuya.feeder_entities import FeederReadiness
    c, d = await state(tmp_path)
    d.feeder = c
    c.received({"1": encode([meal()]), "4": "standby", "13": 0}, d.generation)
    sensor = FeederReadiness(d)
    assert sensor.native_value == "waiting_for_power"
    assert not FeedButton(d).available
    c.received({"101": "battery_power"}, d.generation)
    assert sensor.native_value == "power_restricted"
    c.received({"101": "strong_power"}, d.generation)
    assert sensor.native_value == "ready"
    assert FeedButton(d).available
    assert not d.writes
    await c.stop()


@pytest.mark.parametrize("error", [{"bad": "value"}, [], 7, False])
@pytest.mark.asyncio
async def test_malformed_stored_error_fails_closed(tmp_path, error):
    c, d = await state(tmp_path)
    c.received({"1": encode([meal()])}, d.generation)
    await c.stop()
    saved = await c.store.async_load()
    saved["error"] = error
    await c.store.async_save(saved)
    restored = type(c)(d)
    d.feeder = restored
    await restored.start()
    assert restored._storage_blocked
    assert restored.uncertain and restored.feed_uncertain
    assert FeederError(d).native_value == "Invalid stored snapshot"
    assert not d.writes
    await restored.stop()


@pytest.mark.asyncio
async def test_owned_raw_number_unavailable_without_changing_registry(tmp_path):
    from custom_components.localtuya.number import LocalTuyaNumber
    from .test_device import device_config
    from types import SimpleNamespace
    config = device_config()
    from homeassistant.core import HomeAssistant
    device = SimpleNamespace(connected=True, hass=HomeAssistant(str(tmp_path)))
    entity = LocalTuyaNumber(device, config, "3")
    assert not entity.available
    assert entity.extra_state_attributes["blocked_reason"] == "native_feeder_owns_raw_control"
    assert entity.unique_id == "local_synthetic-device_3"
    config["feeder_profile"] = "none"
    assert LocalTuyaNumber(device, config, "3").available


@pytest.mark.parametrize("telemetry,reason", [
    ({}, "waiting_for_idle"),
    ({"4": "feeding"}, "not_idle"),
    ({"4": "standby"}, "waiting_for_fault"),
    ({"4": "standby", "13": False}, "waiting_for_fault"),
    ({"4": "standby", "13": 1}, "fault"),
    ({"4": "standby", "13": 0, "101": "unrecognized"}, "power_restricted"),
])
@pytest.mark.asyncio
async def test_readiness_preserves_strict_gates(tmp_path, telemetry, reason):
    c, d = await state(tmp_path)
    d.feeder = c
    c.received({"1": encode([meal()]), **telemetry}, d.generation)
    assert c.feed_blocked_reason == reason
    assert not c.feed_ready
    assert not FeedButton(d).available
    assert not d.writes
    await c.stop()


@pytest.mark.asyncio
async def test_restored_ack_expiry_and_unsupported_feed_delete_grants(tmp_path):
    c, d = await state(tmp_path)
    c.received({"1": encode([meal()])}, d.generation)
    await c.stop()
    c = type(c)(d)
    await c.start()
    c.native_arm_seconds = 0.001
    c.arm_native("restored")
    await asyncio.sleep(0.01)
    assert not c.native_armed("restored") and not c._native_arms
    for kind in ("feed", "delete"):
        with pytest.raises(ServiceValidationError):
            c.arm_native(kind)
    assert not d.writes
    await c.stop()


@pytest.mark.asyncio
async def test_feed_rechecks_readiness_after_persistent_reservation(tmp_path, monkeypatch):
    c, d = await state(tmp_path)
    d.feeder = c
    c.received({"1": encode([meal()]), "4": "standby", "13": 0, "101": "strong_power"}, d.generation)
    original = c.save
    async def lose_power_before_send():
        await original()
        c.received({"101": "battery_power"}, d.generation)
    monkeypatch.setattr(c, "save", lose_power_before_send)
    with pytest.raises(ServiceValidationError, match="changed_before_send_no_write"):
        await FeedButton(d).async_press()
    assert not d.writes
    await c.stop()
