"""Native presentation of protocol slots, never a second schedule owner."""

from copy import deepcopy
from datetime import time

from homeassistant.components.time import TimeEntity
from homeassistant.components.number import NumberEntity, RestoreNumber, NumberMode
from homeassistant.components.switch import SwitchEntity
from homeassistant.components.button import ButtonEntity
from homeassistant.components.sensor import SensorEntity
from homeassistant.exceptions import ServiceValidationError
from homeassistant.helpers.entity import Entity
from homeassistant.helpers import entity_registry as er

from .const import DOMAIN
from .core.ha_entities.pet_feeders import DAYS, MAX_MEALS


def check_result(result):
    """A button timestamp is not a command result."""
    if result.get("error") not in (None, "unchanged_no_write"):
        raise ServiceValidationError(result["error"])


class FeederEntity(Entity):
    """Capture the last published baseline before any action yields."""

    _attr_should_poll = False
    _attr_has_entity_name = True

    def __init__(self, device, key, slot=None):
        self.device = device
        self.capability = device.feeder
        self.slot = slot
        suffix = f"meal_slot_{slot + 1}_{key}" if slot is not None else key
        self._attr_unique_id = f"local_{device.id}_{suffix}"
        self._attr_translation_key = key
        if slot is not None:
            self._attr_translation_placeholders = {"slot": str(slot + 1)}
        self._capture()

    def _capture(self):
        c = self.capability
        self.view_revision = c.revision
        self.view_raw = c.raw
        self.view_source = c.snapshot_source
        self.view_generation = self.device.generation
        # Retain the last observed value on unknown/malformed reports, not availability.
        if c.meals is not None:
            self.view_meals = deepcopy(c.meals)
        elif not hasattr(self, "view_meals"):
            self.view_meals = []

    @property
    def device_info(self):
        info = {"identifiers": {(DOMAIN, f"local_{self.device.id}")}}
        if config := getattr(self.device, "_device_config", None):
            info.update(
                name=config.name,
                manufacturer="Tuya",
                model=f"{config.model} ({config.id})",
                sw_version=config.protocol_version,
            )
        return info

    @property
    def record(self):
        if self.slot is not None and self.slot < len(self.view_meals):
            return self.view_meals[self.slot]
        return None

    @property
    def available(self):
        c = self.capability
        return bool(
            c.ready
            and not c.lock.locked()
            and not c.feed_uncertain
            and not c.uncertain
            and not c.error
            and c.meals is not None
            and (self.slot is None or self.slot < len(c.meals))
        )

    async def async_added_to_hass(self):
        await super().async_added_to_hass()
        self.async_on_remove(self.capability.subscribe(self._published))
        self._published()

    def _published(self):
        self._capture()
        if self.slot is not None and self.capability.meals is not None:
            registry = er.async_get(self.hass)
            if entry := registry.async_get(self.entity_id):
                absent = self.slot >= len(self.capability.meals)
                if absent and entry.hidden_by is None:
                    registry.async_update_entity(
                        self.entity_id, hidden_by=er.RegistryEntryHider.INTEGRATION
                    )
                elif (
                    not absent and entry.hidden_by is er.RegistryEntryHider.INTEGRATION
                ):
                    registry.async_update_entity(self.entity_id, hidden_by=None)
        self.async_write_ha_state()

    def baseline(self):
        c = self.capability
        if (
            not self.available
            or self.view_revision != c.revision
            or self.view_raw != c.raw
            or self.view_source != c.snapshot_source
            or self.view_generation is not self.device.generation
        ):
            raise ServiceValidationError("No current safe displayed baseline")
        return self.view_revision

    async def edit(self, field, value):
        revision = self.baseline()
        record = deepcopy(self.record)
        if record is None:
            raise ServiceValidationError("Meal slot is absent")
        record[field] = value
        acknowledged = self.acknowledge_restored()
        result = await self.capability.meal_action(
            "update", revision, self.slot, record, acknowledged
        )
        check_result(result)

    def acknowledge_restored(self):
        if self.view_source == "restored":
            self.capability.consume_native_arm("restored")
            return True
        return False


class ScheduleEditEntity(FeederEntity):
    """Expose edits only when the controller can accept their provenance."""

    @property
    def available(self):
        c = self.capability
        return super().available and (
            c.snapshot_source == "received"
            or (c.snapshot_source == "restored" and c.native_armed("restored"))
        )


class MealTime(ScheduleEditEntity, TimeEntity):
    """Minute resolution in feeder-local time, without clock management."""

    def __init__(self, device, slot):
        super().__init__(device, "time", slot)

    @property
    def native_value(self):
        return time.fromisoformat(self.record["time"]) if self.record else None

    async def async_set_value(self, value):
        if (
            not isinstance(value, time)
            or value.tzinfo is not None
            or value.second
            or value.microsecond
        ):
            raise ServiceValidationError("Use whole minutes in feeder-local time")
        await self.edit("time", value.strftime("%H:%M"))


class MealPortions(ScheduleEditEntity, NumberEntity):
    """Edit only the selected slot's quantity."""

    _attr_native_min_value = 1
    _attr_native_max_value = 60
    _attr_native_step = 1
    _attr_mode = NumberMode.BOX

    def __init__(self, device, slot):
        super().__init__(device, "portions", slot)

    @property
    def native_value(self):
        return self.record["portions"] if self.record else None

    async def async_set_native_value(self, value):
        if (
            type(value) not in (int, float)
            or not 1 <= value <= 60
            or int(value) != value
        ):
            raise ServiceValidationError("Use whole portions from 1 to 60")
        await self.edit("portions", int(value))


class MealEnabled(ScheduleEditEntity, SwitchEntity):
    """Edit only the selected slot's enable byte."""

    def __init__(self, device, slot):
        super().__init__(device, "enabled", slot)

    @property
    def is_on(self):
        return self.record["enabled"] if self.record else None

    async def async_turn_on(self, **kwargs):
        await self.edit("enabled", True)

    async def async_turn_off(self, **kwargs):
        await self.edit("enabled", False)


class ManualPortions(FeederEntity, RestoreNumber):
    """Persist a local selection, not a command or a physical DP."""

    _attr_native_min_value = 1
    _attr_native_max_value = 60
    _attr_native_step = 1
    _attr_mode = NumberMode.BOX

    def __init__(self, device):
        super().__init__(device, "manual_portions")

    @property
    def available(self):
        return not self.capability.closed

    @property
    def native_value(self):
        return self.capability.manual_portions

    async def async_added_to_hass(self):
        await super().async_added_to_hass()
        if data := await self.async_get_last_number_data():
            try:
                self.capability.set_manual_portions(data.native_value)
            except ServiceValidationError:
                pass

    async def async_set_native_value(self, value):
        self.capability.set_manual_portions(value)


class FeedButton(FeederEntity, ButtonEntity):
    """A confirmation button, not a raw DP3 number."""

    def __init__(self, device):
        super().__init__(device, "feed")

    @property
    def available(self):
        return super().available and self.capability.feed_ready

    async def async_press(self):
        portions = self.capability.manual_portions
        revision = self.baseline()
        check_result(
            await self.capability.feed(portions, True, expected_revision=revision)
        )


class DeleteMeal(ScheduleEditEntity, ButtonEntity):
    """A deliberate press deletes exactly this captured positional slot."""

    def __init__(self, device, slot):
        super().__init__(device, "delete", slot)

    @property
    def available(self):
        return super().available and len(self.view_meals) > 1

    async def async_press(self):
        revision = self.baseline()
        meals = deepcopy(self.view_meals)
        del meals[self.slot]
        acknowledged = self.acknowledge_restored()
        check_result(await self.capability.set_schedule(revision, meals, acknowledged))


class AddMeal(ScheduleEditEntity, ButtonEntity):
    """Append one disabled every-day slot at the first unused minute."""

    def __init__(self, device):
        super().__init__(device, "add_meal")

    @property
    def available(self):
        return super().available and len(self.view_meals) < MAX_MEALS

    async def async_press(self):
        revision = self.baseline()
        used = {record["time"] for record in self.view_meals}
        candidate = next(
            f"{i // 60:02d}:{i % 60:02d}"
            for i in range(1440)
            if f"{i // 60:02d}:{i % 60:02d}" not in used
        )
        record = {
            "days": list(DAYS),
            "time": candidate,
            "portions": 1,
            "enabled": False,
        }
        acknowledged = self.acknowledge_restored()
        check_result(
            await self.capability.meal_action(
                "add", revision, meal=record, acknowledge_restored_snapshot=acknowledged
            )
        )


class AcknowledgeRestored(FeederEntity, ButtonEntity):
    """One mutation of exactly this restored baseline, never global trust."""

    def __init__(self, device):
        super().__init__(device, "acknowledge_restored")

    @property
    def available(self):
        return super().available and self.view_source == "restored"

    async def async_press(self):
        self.baseline()
        self.capability.arm_native("restored")


class FeederStatus(FeederEntity, SensorEntity):
    """Readable transaction status and errors without duplicating the plan."""

    _attr_device_class = "enum"
    _attr_options = [
        "idle",
        "schedule_pending",
        "command_pending",
        "feeding",
        "reported",
        "unknown",
        "unavailable",
    ]

    def __init__(self, device):
        super().__init__(device, "feeder_status")

    @property
    def available(self):
        return True

    @property
    def native_value(self):
        c = self.capability
        if not c.ready:
            return "unavailable"
        if c.lock.locked() and c._kind == "schedule":
            return "schedule_pending"
        if c.uncertain or c.feed_uncertain:
            return "unknown" if not c.lock.locked() else c.feeding_state
        return c.feeding_state

    @property
    def extra_state_attributes(self):
        c = self.capability
        return {
            "pending": c.lock.locked(),
            "error": c.error,
            "feeding_error": c.feeding_error,
            "snapshot_source": c.snapshot_source,
            "revision": c.revision,
            "restored_acknowledged": c.native_armed("restored"),
            "acknowledgement_seconds": c.native_arm_seconds,
            "blocked_reason": c.feed_blocked_reason,
        }


class FeederReadiness(FeederEntity, SensorEntity):
    """Explain conservative feed gates without calling a blocked state an error."""

    _attr_device_class = "enum"
    _attr_options = [
        "ready",
        "unavailable",
        "busy",
        "unknown_outcome",
        "schedule_blocked",
        "waiting_for_idle",
        "not_idle",
        "waiting_for_fault",
        "fault",
        "waiting_for_power",
        "power_restricted",
    ]

    def __init__(self, device):
        super().__init__(device, "feeder_readiness")

    @property
    def available(self):
        return True

    @property
    def native_value(self):
        return self.capability.feed_blocked_reason or "ready"


class FeederError(FeederEntity, SensorEntity):
    """Human-readable local error, retaining the exact backend code as metadata."""

    def __init__(self, device):
        super().__init__(device, "feeder_error")

    @property
    def available(self):
        return True

    @property
    def native_value(self):
        error = self.capability.error or self.capability.feeding_error
        return error.replace("_", " ").capitalize() if error else "No reported error"

    @property
    def extra_state_attributes(self):
        return {
            "error": self.capability.error,
            "feeding_error": self.capability.feeding_error,
        }


def setup_native_platform(domain, hass, entry, add_entities):
    """Register each device once per platform, then discover only observed slots."""
    factories = {
        "time": ([], MealTime),
        "number": ([ManualPortions], MealPortions),
        "switch": ([], MealEnabled),
        "button": ([FeedButton, AddMeal, AcknowledgeRestored], DeleteMeal),
        "sensor": ([FeederStatus, FeederError, FeederReadiness], None),
    }
    if domain not in factories:
        return
    common, slot_factory = factories[domain]
    for device in hass.data[DOMAIN][entry.entry_id].devices.values():
        if device.feeder is None:
            continue
        # Retire only exact integration-owned obsolete controls for this selected device.
        registry = er.async_get(hass)
        for old_domain, key in (("switch", "arm_feed"), ("select", "delete_selection")):
            entity_id = registry.async_get_entity_id(
                old_domain, DOMAIN, f"local_{device.id}_{key}"
            )
            if (
                entity_id
                and registry.async_get(entity_id).config_entry_id == entry.entry_id
            ):
                registry.async_remove(entity_id)
        add_entities([factory(device) for factory in common])
        if slot_factory is not None:
            # Bind loop variables explicitly: two feeders must never share a callback.
            def discover(device=device, factory=slot_factory, seen=set()):
                meals = device.feeder.meals
                if meals is None:
                    return
                slots = set(range(len(meals))) - seen
                seen.update(slots)
                if slots:
                    add_entities([factory(device, slot) for slot in sorted(slots)])

            entry.async_on_unload(device.feeder.subscribe(discover))
            discover()
