"""Native FT-11W state owned by TuyaDevice, independent of HA presentation.

Codec and conservative feeding semantics adapted from myevit/tuya-feeder
(MIT; see NOTICE-feeder). LocalTuya retains GPL-3.0. No external runtime adapter.
"""

import asyncio
from contextlib import asynccontextmanager
from copy import deepcopy

from homeassistant.exceptions import ServiceValidationError
from homeassistant.helpers.storage import Store
from homeassistant.util import dt as dt_util

from .core.ha_entities.pet_feeders import decode, encode


def strict_int(value):
    """Do not coerce booleans, floats or strings into physical quantities."""
    if type(value) is not int:
        import voluptuous as vol

        raise vol.Invalid("Expected integer")
    return value


def strict_bool(value):
    if type(value) is not bool:
        import voluptuous as vol

        raise vol.Invalid("Expected boolean")
    return value


def migrate_legacy_snapshot(schedule, feed):
    """Validate one archived legacy snapshot; preserve time, never invent receipt."""
    if type(schedule) is not dict:
        raise ValueError("Missing legacy schedule")
    decode(schedule.get("raw"))
    revision, stamp = schedule.get("revision"), schedule.get("last_received_at")
    parsed = dt_util.parse_datetime(stamp) if type(stamp) is str else None
    if (
        type(revision) is not int
        or revision < 1
        or parsed is None
        or parsed.tzinfo is None
        or parsed > dt_util.utcnow()
        or type(schedule.get("uncertain")) is not bool
    ):
        raise ValueError("Invalid legacy provenance")
    result = {
        key: schedule.get(key)
        for key in ("raw", "revision", "last_received_at", "uncertain", "error")
    }
    result.update(feed_uncertain=True, last_feed_at=None)
    if feed is not None:
        if type(feed) is not dict or type(feed.get("uncertain")) is not bool:
            raise ValueError("Invalid legacy feed guard")
        stamp = feed.get("last_feed_at")
        parsed = dt_util.parse_datetime(stamp) if type(stamp) is str else None
        if stamp is not None and (
            parsed is None or parsed.tzinfo is None or parsed > dt_util.utcnow()
        ):
            raise ValueError("Invalid legacy last-feed time")
        result.update(feed_uncertain=feed["uncertain"], last_feed_at=stamp)
    return result


def register_services(hass):
    """One integration-native API targeting exactly one loaded plan sensor."""
    import voluptuous as vol
    from homeassistant.core import SupportsResponse
    from homeassistant.helpers import config_validation as cv, entity_registry as er

    from .const import DOMAIN

    revision = vol.All(strict_int, vol.Range(min=0))
    common = {
        vol.Required("entity_id"): cv.entity_id,
        vol.Required("expected_revision"): revision,
        vol.Optional("acknowledge_restored_snapshot", default=False): strict_bool,
    }
    schemas = {
        "set_feeder_schedule": {**common, vol.Required("meals"): list},
        "add_feeder_meal": {**common, vol.Required("meal"): dict},
        "update_feeder_meal": {
            **common,
            vol.Required("meal_index"): revision,
            vol.Required("meal"): dict,
        },
        "delete_feeder_meal": {**common, vol.Required("meal_index"): revision},
        "feed": {
            vol.Required("entity_id"): cv.entity_id,
            vol.Required("portions"): vol.All(strict_int, vol.Range(min=1, max=60)),
            vol.Required("confirmed"): strict_bool,
        },
    }

    async def handle(call):
        fields = dict(call.data)
        entity_id = fields.pop("entity_id")
        record = er.async_get(hass).async_get(entity_id)
        if (
            record is None
            or record.platform != DOMAIN
            or not entity_id.startswith("sensor.")
            or record.disabled
        ):
            raise ServiceValidationError(
                "Target must be one enabled native LocalTuya meal-plan sensor"
            )
        matches = [
            dev.feeder
            for data in hass.data.get(DOMAIN, {}).values()
            if hasattr(data, "devices")
            for dev in data.devices.values()
            if getattr(dev, "feeder", None)
            and getattr(dev.feeder, "entity_id", None) == entity_id
            and not dev.feeder.closed
            and record.unique_id == f"local_{dev.id}_1"
            and record.config_entry_id
            == getattr(getattr(dev, "_entry", None), "entry_id", None)
        ]
        if len(matches) != 1:
            raise ServiceValidationError("Native meal-plan sensor is not loaded")
        capability = matches[0]
        if call.service == "set_feeder_schedule":
            result = await capability.set_schedule(**fields)
        elif call.service == "feed":
            result = await capability.feed(**fields)
        else:
            action = call.service.split("_", 1)[0]
            result = await capability.meal_action(action, **fields)
        if call.return_response:
            return result
        if result["error"] not in (None, "unchanged_no_write"):
            raise ServiceValidationError(result["error"])
        return None

    for name, schema in schemas.items():
        if not hass.services.has_service(DOMAIN, name):
            hass.services.async_register(
                DOMAIN,
                name,
                handle,
                vol.Schema(schema),
                supports_response=SupportsResponse.OPTIONAL,
            )


class FeederCapability:
    """Observed schedule and crash-safe serialized one-shot actions."""

    def __init__(self, device):
        self.device = device
        self.hass = device.hass
        self.store = Store(
            self.hass,
            1,
            f"localtuya.feeder.{device.id}",
            private=True,
            atomic_writes=True,
        )
        self.raw = self.meals = None
        self.revision = 0
        self.last_received_at = None
        self.snapshot_source = "unknown"
        self.uncertain = False
        self.error = None
        self.feed_uncertain = False
        self.last_feed_at = None
        self.feeding_state = "idle"
        self.feeding_error = None
        self.portions = None
        self.requested_at = self.observed_at = self.reported_at = None
        self.lock = asyncio.Lock()
        self._save_lock = asyncio.Lock()
        self._listeners = set()
        self._saves = set()
        self._operation = None
        self._waiter = None
        self._target = None
        self._attempted = False
        self._kind = None
        self._generation = None
        self._activity = None
        self._activity_generation = None
        self._telemetry = {}
        self.closed = False
        self._storage_blocked = False
        self._receipt_sequence = 0
        self._observed_receipt = None
        self.confirm_timeout = 15
        self.feed_timeout = 120
        self.cooldown = 2

    async def start(self):
        from pathlib import Path

        entries = self.hass.config_entries
        legacy_id = None
        if entries:
            for entry in entries.async_entries("tuya_feeder"):
                if (
                    entry.data.get("device_id") == self.device.id
                    and not entry.disabled_by
                ):
                    self._storage_blocked = self.closed = True
                    self.error = "duplicate_legacy_owner_disable_legacy_entry"
                    return
        config = getattr(self.device, "_device_config", None)
        if config:
            legacy_id = config.as_dict().get("feeder_legacy_entry_id")
        existed = await self.hass.async_add_executor_job(Path(self.store.path).exists)
        try:
            saved = await self.store.async_load()
            if existed and saved is None:
                raise ValueError("Corrupt destination")
            if saved is None and legacy_id:
                if not entries:
                    raise ValueError("Missing legacy configuration")
                old = entries.async_get_entry(legacy_id)
                if (
                    old is None
                    or old.domain != "tuya_feeder"
                    or not old.disabled_by
                    or old.data.get("device_id") != self.device.id
                ):
                    raise ValueError("Legacy entry must be disabled and match device")
                schedule = await Store(
                    self.hass, 1, f"tuya_feeder.{legacy_id}"
                ).async_load()
                feed = await Store(
                    self.hass, 1, f"tuya_feeder.feed.{legacy_id}"
                ).async_load()
                saved = migrate_legacy_snapshot(schedule, feed)
                # The destination was absent; this is a bounded one-time copy.
                await self.store.async_save(saved)
        except Exception:
            self._storage_blocked = True
            self.uncertain = self.feed_uncertain = True
            self.error = "invalid_or_unmigratable_store"
            return
        if saved is None:
            return
        try:
            if type(saved) is not dict:
                raise ValueError("Invalid stored snapshot")
            raw = saved["raw"]
            meals = decode(raw) if raw is not None else None
            revision = saved["revision"]
            stamp = saved["last_received_at"]
            parsed = dt_util.parse_datetime(stamp) if type(stamp) is str else None
            if (
                type(revision) is not int
                or revision < 0
                or (revision == 0 and (raw is not None or stamp is not None))
                or (
                    revision
                    and (
                        parsed is None
                        or parsed.tzinfo is None
                        or parsed > dt_util.utcnow()
                    )
                )
            ):
                raise ValueError("Invalid receipt metadata")
            self.raw, self.meals, self.revision = raw, meals, revision
            self.last_received_at = stamp
            self.snapshot_source = "restored" if revision else "unknown"
            self.uncertain = saved.get("uncertain", True) is not False
            self.feed_uncertain = saved.get("feed_uncertain", True) is not False
            self.error = saved.get("error")
            stamp = saved.get("last_feed_at")
            parsed = dt_util.parse_datetime(stamp) if type(stamp) is str else None
            if stamp is not None and (
                parsed is None or parsed.tzinfo is None or parsed > dt_util.utcnow()
            ):
                raise ValueError("Invalid last-feed time")
            self.last_feed_at = stamp
            self.feeding_state = "unknown" if self.feed_uncertain else "idle"
            if self.feed_uncertain:
                self.feeding_error = "restored_unknown_outcome_check_feeder"
        except (KeyError, TypeError, ValueError):
            self.raw = self.meals = None
            self.uncertain = self.feed_uncertain = True
            self._storage_blocked = True
            self.error = "invalid_stored_snapshot"

    async def save(self):
        if self._storage_blocked:
            return
        async with self._save_lock:
            await self.store.async_save(
                {
                    "raw": self.raw,
                    "revision": self.revision,
                    "last_received_at": self.last_received_at,
                    "uncertain": self.uncertain,
                    "feed_uncertain": self.feed_uncertain,
                    "last_feed_at": self.last_feed_at,
                    "error": self.error,
                }
            )

    def subscribe(self, listener):
        self._listeners.add(listener)
        return lambda: self._listeners.discard(listener)

    def publish(self):
        for listener in tuple(self._listeners):
            listener()

    def _persist_report(self):
        task = self.hass.async_create_task(self._save_report(), "localtuya-feeder-save")
        self._saves.add(task)
        task.add_done_callback(self._saves.discard)

    async def _save_report(self):
        try:
            await self.save()
        except Exception:
            self._storage_blocked = True
            self.error = "snapshot_persistence_failed"
            self.publish()

    def received(self, dps, generation):
        """Exact frame only; never called for merged cache, ACK or restores."""
        if (
            self.closed
            or generation is None
            or generation is not self.device.generation
        ):
            return
        now = dt_util.utcnow().isoformat()
        self._receipt_sequence += 1
        if generation is not self._activity_generation:
            self._activity_generation = generation
            self._activity = None
            self._telemetry = {}
        self._telemetry.update(dps)
        persist = "1" in dps
        if "1" in dps:
            self.revision += 1
            self.last_received_at = now
            self.snapshot_source = "received"
            try:
                self.meals = decode(dps["1"])
                self.raw = dps["1"]
                self.error = None
                # A later genuine snapshot reconciles schedule uncertainty only.
                self.uncertain = False
            except ValueError:
                self.raw = self.meals = None
                self.error = "unsupported_or_malformed_report"
                self.uncertain = True
            if (
                self._kind == "schedule"
                and self._attempted
                and generation is self._generation
            ):
                if self._waiter and not self._waiter.done():
                    self._waiter.set_result(
                        self.raw == self._target and not self.uncertain
                    )
        if "4" in dps:
            previous = self._activity
            self._activity = dps["4"]
            if dps["4"] == "feeding" and previous in ("standby", "done"):
                self.last_feed_at = now
                persist = True
                if (
                    self._kind == "feed"
                    and self._attempted
                    and generation is self._generation
                ):
                    self.observed_at = now
                    self._observed_receipt = self._receipt_sequence
                    self.feeding_state = "feeding"
        # A report in the start frame itself is not post-start evidence.
        if (
            self._kind == "feed"
            and self._attempted
            and generation is self._generation
            and self.observed_at
            and self._observed_receipt < self._receipt_sequence
            and type(dps.get("14")) is int
            and dps["14"] == self.portions
        ):
            self.reported_at = now
            self.feeding_state = "reported"
            self.feed_uncertain = False
            self.feeding_error = None
            persist = True
            if self._waiter and not self._waiter.done():
                self._waiter.set_result(True)
            if self._operation is None:
                self._kind = None
                self._attempted = False
        if persist:
            self._persist_report()
        self.publish()

    def disconnected(self):
        self._activity_generation = self._activity = None
        self._telemetry = {}
        if self._waiter and not self._waiter.done():
            self._waiter.set_result(False)
        self.publish()

    @property
    def ready(self):
        entries = self.hass.config_entries
        legacy_active = entries and any(
            entry.data.get("device_id") == self.device.id and not entry.disabled_by
            for entry in entries.async_entries("tuya_feeder")
        )
        return bool(
            not self.closed
            and not self._storage_blocked
            and self.device.feeder_ready
            and not legacy_active
        )

    @property
    def feed_ready(self):
        return (
            self.ready
            and self._telemetry.get("4") in ("standby", "done")
            and type(self._telemetry.get("13")) is int
            and self._telemetry["13"] == 0
            and self._telemetry.get("101") == "strong_power"
        )

    def _validate_action(self):
        if self.closed or self.lock.locked() or self.feed_uncertain:
            raise ServiceValidationError(
                "Feeder busy or feeding result unknown; do not retry"
            )
        if not self.ready:
            raise ServiceValidationError("No ready native feeder connection")

    async def set_schedule(
        self, expected_revision, meals, acknowledge_restored_snapshot=False
    ):
        async with self.action():
            return await self._set_schedule(
                expected_revision, meals, acknowledge_restored_snapshot
            )

    async def meal_action(
        self,
        action,
        expected_revision,
        meal_index=None,
        meal=None,
        acknowledge_restored_snapshot=False,
    ):
        async with self.action():
            self._validate_revision(expected_revision)
            if self.meals is None:
                raise ServiceValidationError("No valid baseline")
            meals = deepcopy(self.meals)
            if action in ("update", "delete"):
                if type(meal_index) is not int or not 0 <= meal_index < len(meals):
                    raise ServiceValidationError("Invalid meal_index")
            if action == "add":
                meals.append(meal)
            elif action == "update":
                meals[meal_index] = meal
            elif action == "delete":
                if len(meals) == 1:
                    raise ServiceValidationError(
                        "Disable the final meal instead of deleting it"
                    )
                del meals[meal_index]
            else:
                raise ServiceValidationError("Unknown meal action")
            return await self._set_schedule(
                expected_revision, meals, acknowledge_restored_snapshot
            )

    def _validate_revision(self, expected_revision):
        if type(expected_revision) is not int or expected_revision != self.revision:
            raise ServiceValidationError(
                "Stale expected_revision; reload reported plan"
            )

    async def _set_schedule(self, revision, meals, acknowledged):
        self._validate_revision(revision)
        try:
            target = encode(meals)
        except ValueError as err:
            raise ServiceValidationError(str(err)) from err
        if (
            self.meals is None
            or self.uncertain
            or self.error
            or self.snapshot_source not in ("received", "restored")
            or (self.snapshot_source == "restored" and acknowledged is not True)
        ):
            raise ServiceValidationError(
                "No safe baseline or restored snapshot acknowledgement"
            )
        if self.meals and not meals:
            raise ServiceValidationError(
                "Disable the final meal instead of deleting it"
            )
        if target == self.raw:
            return {
                "confirmed": False,
                "revision": self.revision,
                "error": "unchanged_no_write",
            }
        self._target = target
        self.uncertain = True
        return await self._execute("schedule", {"1": target}, revision)

    async def feed(self, portions, confirmed=False):
        """Quantity is the immutable confirmed selection; DP3 alone actuates."""
        if (
            type(portions) is not int
            or not 1 <= portions <= 60
            or confirmed is not True
        ):
            raise ServiceValidationError("Confirm an integer quantity from 1 to 60")
        self._validate_action()
        if not self.feed_ready or self.uncertain:
            raise ServiceValidationError("Feeder not ready or schedule result unknown")
        async with self.action():
            self.feed_uncertain = True
            self.feeding_state = "command_pending"
            self.portions = portions
            self.requested_at = self.observed_at = self.reported_at = None
            self.feeding_error = None
            return await self._execute("feed", {"3": portions})

    @asynccontextmanager
    async def action(self):
        self._validate_action()
        try:
            async with self.lock:
                yield
        finally:
            # Publish after releasing the lock, including validation failures.
            self.publish()

    def _on_attempt(self):
        self._attempted = True
        if self._kind == "feed":
            self.requested_at = dt_util.utcnow().isoformat()

    async def _execute(self, kind, dps, revision=None):
        self._kind = kind
        self._attempted = False
        self._operation = asyncio.current_task()
        self._generation = self.device.generation
        self._waiter = self.hass.loop.create_future()
        self.publish()
        confirmed = False
        error = None
        try:
            # Persist a reservation before any possible transport invocation.
            await self.save()
            if (
                not self.ready
                or self.device.generation is not self._generation
                or (revision is not None and revision != self.revision)
                or (kind == "feed" and not self.feed_ready)
            ):
                error = "changed_before_send_no_write"
            else:
                async with asyncio.timeout(
                    self.confirm_timeout if kind == "schedule" else self.feed_timeout
                ):
                    await self.device.send_feeder_once(
                        dps, self._generation, self._on_attempt
                    )
                    if kind == "feed":
                        await asyncio.sleep(self.cooldown)
                    confirmed = await self._waiter
                if not confirmed:
                    error = "unconfirmed_conflict_or_disconnect"
        except ConnectionError:
            error = "unconfirmed_transport_error" if self._attempted else "not_sent"
        except TimeoutError:
            error = "unconfirmed_timeout"
        except asyncio.CancelledError:
            error = "unconfirmed_cancelled"
            raise
        except Exception:
            error = "unconfirmed_transport_error"
        finally:
            if kind == "schedule":
                self.uncertain = self.meals is None or (
                    self._attempted and not confirmed
                )
                self.error = error
            else:
                self.feed_uncertain = self._attempted and not confirmed
                self.feeding_state = (
                    "reported"
                    if confirmed
                    else "unknown" if self._attempted else "idle"
                )
                self.feeding_error = error
            if self._waiter and not self._waiter.done():
                self._waiter.cancel()
            self._waiter = None
            self._operation = None
            # Keep late feed evidence correlated in this generation, never resend.
            if kind != "feed" or confirmed or not self._attempted:
                self._kind = None
                self._attempted = False
            await self.save()
            self.publish()
        if kind == "schedule":
            return {"confirmed": confirmed, "revision": self.revision, "error": error}
        return {
            "command_attempted": self._attempted or confirmed,
            "reported": confirmed,
            "state": self.feeding_state,
            "error": error,
            "requested_at": self.requested_at,
            "reported_at": self.reported_at,
        }

    async def stop(self):
        self.closed = True
        if self._operation and self._operation is not asyncio.current_task():
            self._operation.cancel()
            await asyncio.gather(self._operation, return_exceptions=True)
        if self._saves:
            await asyncio.gather(*self._saves)
        await self.save()
        self._listeners.clear()
