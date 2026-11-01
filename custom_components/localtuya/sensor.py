"""Platform to present any Tuya DP as a sensor."""

import logging
import base64
from functools import partial
from .config_flow import col_to_select

import voluptuous as vol
from homeassistant.components.sensor import (
    DEVICE_CLASSES_SCHEMA,
    DOMAIN,
    STATE_CLASSES_SCHEMA,
    SensorDeviceClass,
    SensorEntity,
    SensorStateClass,
)
from homeassistant.const import (
    CONF_DEVICE_CLASS,
    CONF_UNIT_OF_MEASUREMENT,
    Platform,
    STATE_UNKNOWN,
    UnitOfElectricCurrent,
    UnitOfElectricPotential,
    UnitOfPower,
)
from homeassistant.helpers import entity_registry as er

from .entity import LocalTuyaEntity, async_setup_entry
from .const import CONF_SCALING, CONF_OFFSET, CONF_STATE_CLASS

_LOGGER = logging.getLogger(__name__)

DEFAULT_PRECISION = 2

ATTR_POWER = "power"
ATTR_VOLTAGE = "voltage"
ATTR_CURRENT = "current"
MAP_UOM = {
    ATTR_CURRENT: UnitOfElectricCurrent.AMPERE,
    ATTR_VOLTAGE: UnitOfElectricPotential.VOLT,
    ATTR_POWER: UnitOfPower.KILO_WATT,
}


def flow_schema(dps):
    """Return schema used in config flow."""
    return {
        vol.Optional(CONF_UNIT_OF_MEASUREMENT): str,
        vol.Optional(CONF_DEVICE_CLASS): DEVICE_CLASSES_SCHEMA,
        vol.Optional(CONF_STATE_CLASS): col_to_select(
            [sc.value for sc in SensorStateClass]
        ),
        vol.Optional(CONF_SCALING): vol.All(
            vol.Coerce(float), vol.Range(min=-1000000.0, max=1000000.0)
        ),
        vol.Optional(CONF_OFFSET): vol.All(
            vol.Coerce(float), vol.Range(min=-1000000.0, max=1000000.0)
        ),
    }


class LocalTuyaSensor(LocalTuyaEntity, SensorEntity):
    """Representation of a Tuya sensor."""

    def __init__(
        self,
        device,
        config_entry,
        sensorid,
        **kwargs,
    ):
        """Initialize the Tuya sensor."""
        super().__init__(device, config_entry, sensorid, _LOGGER, **kwargs)
        self._state = None

        self._has_sub_entities = False
        self._attr_device_class = self._config.get(CONF_DEVICE_CLASS)

    @property
    def native_value(self):
        """Return sensor state."""
        return self._state

    @property
    def state_class(self) -> str | None:
        """Return state class."""
        return getattr(self, "_attr_state_class", self._config.get(CONF_STATE_CLASS))

    @property
    def native_unit_of_measurement(self):
        """Return the unit of measurement of this entity, if any."""
        return getattr(
            self,
            "_attr_native_unit_of_measurement",
            self._config.get(CONF_UNIT_OF_MEASUREMENT),
        )

    def status_updated(self):
        """Device status was updated."""

        state = self.dp_value(self._dp_id)

        if self.is_base64(state):
            if not self._has_sub_entities:
                self.hass.loop.call_soon_threadsafe(
                    self.hass.async_create_task, self.__create_sub_sensors()
                )

            if None not in (
                sub_sensor := getattr(self, "_attr_sub_sensor", None),
                sub_sensor_state := self.decode_base64(state).get(sub_sensor),
            ):
                self._state = sub_sensor_state
            else:
                self._state = state
        else:
            self._state = self.scale(state)

    def status_restored(self, stored_state) -> None:
        super().status_restored(stored_state)

        if (last_state := self._last_state) and self.is_base64(last_state):
            self._status.update({self._dp_id: last_state})

    # No need to restore state for a sensor
    async def restore_state_when_connected(self):
        """Do nothing for a sensor."""
        return

    def is_base64(self, data):
        """Return if the data is valid Tuya raw Base64 encoded data."""
        return (
            (data and isinstance(data, str))
            and len(data) >= 12
            and len(data) % 2 == 0
            and data.endswith("=")
        )

    def decode_base64(self, data):
        """Decode data base64 such as DPS phase_a."""
        buf = base64.b64decode(data)
        voltage = (buf[1] | buf[0] << 8) / 10
        current = (buf[4] | buf[3] << 8) / 1000
        power = (buf[7] | buf[6] << 8) / 1000
        return {ATTR_VOLTAGE: voltage, ATTR_CURRENT: current, ATTR_POWER: power}

    async def __create_sub_sensors(self):
        """Create sub entities for voltage, current and power and hide this parent sensor."""
        sub_entities = []

        for sensor in (ATTR_CURRENT, ATTR_POWER, ATTR_VOLTAGE):
            sub_entity = LocalTuyaSensor(
                self._device, self._device_config.as_dict(), self._dp_id
            )
            setattr(sub_entity, "_attr_sub_sensor", sensor)
            setattr(sub_entity, "_attr_unique_id", f"{self.unique_id}_{sensor}")
            setattr(sub_entity, "_attr_name", f"{self.name} {sensor.capitalize()}")
            setattr(sub_entity, "_attr_device_class", SensorDeviceClass(sensor))
            setattr(sub_entity, "_attr_state_class", SensorStateClass.MEASUREMENT)
            setattr(sub_entity, "_attr_native_unit_of_measurement", MAP_UOM[sensor])
            sub_entities.append(sub_entity)

        # Sub entities shouldn't have add entities attr.
        if sub_entities and self.componet_add_entities:
            self._has_sub_entities = True
            self.componet_add_entities(sub_entities)
            er.async_get(self.hass).async_update_entity(
                self.entity_id, hidden_by=er.RegistryEntryHider.INTEGRATION
            )


class FeederMealPlanSensor(LocalTuyaEntity, SensorEntity):
    """Presentation only: the owning TuyaDevice holds all feeder state."""

    def __init__(self, device, config_entry, sensorid, **kwargs):
        super().__init__(device, config_entry, sensorid, _LOGGER, **kwargs)
        self.capability = device.feeder
        self._attr_icon = "mdi:calendar-clock"

    @property
    def available(self):
        """A last-reported snapshot remains readable when LAN is disconnected."""
        return True

    async def async_added_to_hass(self):
        await super().async_added_to_hass()
        self.capability.entity_id = self.entity_id
        self.async_on_remove(self.capability.subscribe(self.async_write_ha_state))

    async def async_will_remove_from_hass(self):
        self.capability.entity_id = None
        await super().async_will_remove_from_hass()

    def status_updated(self):
        """Merged cache is deliberately not an observation source."""

    def status_restored(self, stored_state):
        """Native Store restores provenance; entity state is not a receipt."""

    async def restore_state_when_connected(self):
        """Never replay an actuator value."""

    @property
    def native_value(self):
        meals = self.capability.meals
        return len(meals) if meals is not None else None

    @property
    def extra_state_attributes(self):
        from copy import deepcopy

        c = self.capability
        safe = (
            c.ready
            and not c.lock.locked()
            and not c.feed_uncertain
            and not c.uncertain
            and not c.error
        )
        return {
            "native_feeder_profile": "ft11w",
            "meals": deepcopy(c.meals) if c.meals is not None else [],
            "revision": c.revision,
            "last_received_at": c.last_received_at,
            "snapshot_source": c.snapshot_source,
            "schedule_valid": c.meals is not None,
            "observation_kind": "last locally reported snapshot; not an active schedule query",
            "requires_restored_snapshot_acknowledgement": c.snapshot_source
            == "restored",
            "supports_restored_schedule_write": bool(
                safe and c.meals is not None and c.snapshot_source == "restored"
            ),
            "supports_schedule_write": bool(
                safe and c.meals is not None and c.snapshot_source == "received"
            ),
            "pending": c.lock.locked(),
            "error": c.error,
            "manual_feed_capability": True,
            "supports_manual_feed": bool(c.feed_ready and safe),
            "feeding_pending": c.lock.locked() and c._kind == "feed",
            "feeding_state": c.feeding_state,
            "feeding_error": c.feeding_error,
            "feeding_requested_at": c.requested_at,
            "feeding_observed_at": c.observed_at,
            "feeding_reported_at": c.reported_at,
            "feeding_portions": c.portions,
            "last_feed_at": c.last_feed_at,
            "last_feed_source": "local_dp4_feeding_edge" if c.last_feed_at else None,
            "supported_days_mode": "every_day",
            "max_meals": 10,
            "max_portions": 60,
            "warning": "Ordinary LAN GET may omit DP1. Unreported external edits are not protected by revision. Feeder-local clock/timezone is not managed.",
        }


class FeederLastFeedSensor(SensorEntity):
    """Genuine observed feeding start, not dispensing completion."""

    _attr_should_poll = False
    _attr_has_entity_name = True
    _attr_name = "Last feed"
    _attr_device_class = SensorDeviceClass.TIMESTAMP

    def __init__(self, plan):
        self.plan = plan
        self.capability = plan.capability
        self._attr_unique_id = f"local_{plan._device.id}_last_feed"

    @property
    def device_info(self):
        return self.plan.device_info

    @property
    def native_value(self):
        from homeassistant.util import dt as dt_util

        return (
            dt_util.parse_datetime(self.capability.last_feed_at)
            if self.capability.last_feed_at
            else None
        )

    async def async_added_to_hass(self):
        await super().async_added_to_hass()
        self.async_on_remove(self.capability.subscribe(self.async_write_ha_state))


def make_sensor(device, config_entry, sensorid, **kwargs):
    """Exact native profile DP1 bypasses generic electrical RAW decoding."""
    if device.feeder and str(sensorid) == "1":
        entity = FeederMealPlanSensor(device, config_entry, sensorid, **kwargs)
        if add := kwargs.get("add_entites_callback"):
            add([FeederLastFeedSensor(entity)])
        return entity
    return LocalTuyaSensor(device, config_entry, sensorid, **kwargs)


async_setup_entry = partial(async_setup_entry, DOMAIN, make_sensor, flow_schema)
