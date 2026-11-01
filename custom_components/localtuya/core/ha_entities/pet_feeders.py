"""Strict FT-11W DP1 codec; weekday bit order is deliberately unsupported."""

import base64
import binascii
import re

DAYS = ("monday", "tuesday", "wednesday", "thursday", "friday", "saturday", "sunday")
MAX_MEALS = 10
MAX_PORTIONS = 60


def encode(meals):
    """Validate the complete replacement, with no coercion or dropped fields."""
    if type(meals) is not list or len(meals) > MAX_MEALS:
        raise ValueError("meals must be a list of at most ten complete records")
    records = bytearray()
    for meal in meals:
        if type(meal) is not dict or set(meal) != {
            "days",
            "time",
            "portions",
            "enabled",
        }:
            raise ValueError("Each meal requires exactly days, time, portions, enabled")
        days, time, portions, enabled = (
            meal[key] for key in ("days", "time", "portions", "enabled")
        )
        if (
            type(days) is not list
            or len(days) != 7
            or any(type(day) is not str for day in days)
            or set(days) != set(DAYS)
        ):
            raise ValueError(
                "Only every-day schedules are supported; supply all seven lowercase weekdays"
            )
        if (
            type(time) is not str
            or re.fullmatch(r"(?:[01][0-9]|2[0-3]):[0-5][0-9]", time) is None
        ):
            raise ValueError("time must be HH:MM in feeder-local time")
        if type(portions) is not int or not 1 <= portions <= MAX_PORTIONS:
            raise ValueError("portions must be an integer from 1 to 60")
        if type(enabled) is not bool:
            raise ValueError("enabled must be boolean")
        hour, minute = map(int, time.split(":"))
        records.extend((127, hour, minute, portions, int(enabled)))
    return base64.b64encode(records).decode("ascii")


def decode(raw):
    """Reject the entire snapshot if any unknown or malformed record exists."""
    if type(raw) is not str or len(raw) > 68:
        raise ValueError("Invalid DP1 payload")
    try:
        data = base64.b64decode(raw, validate=True)
    except (binascii.Error, ValueError) as err:
        raise ValueError("Invalid DP1 Base64") from err
    if base64.b64encode(data).decode("ascii") != raw or len(data) % 5 or len(data) > 50:
        raise ValueError("Invalid DP1 length or noncanonical Base64")
    meals = []
    for index in range(0, len(data), 5):
        mask, hour, minute, portions, enabled = data[index : index + 5]
        if (
            mask != 127
            or hour > 23
            or minute > 59
            or not 1 <= portions <= MAX_PORTIONS
            or enabled not in (0, 1)
        ):
            raise ValueError(
                "Unsupported or malformed DP1 record; refusing partial plan"
            )
        meals.append(
            {
                "days": list(DAYS),
                "time": f"{hour:02d}:{minute:02d}",
                "portions": portions,
                "enabled": bool(enabled),
            }
        )
    return meals


# Codec adapted from myevit/tuya-feeder (MIT; see NOTICE-feeder).
PROFILE = "ft11w"
PRODUCT = "gh0lmtxb5fumm0ad"
MODELS = {"FT-11W", "M&C Smart Feeder Titan WIFI"}
CODES = {
    1: "meal_plan",
    2: "quick_feed",
    3: "manual_feed",
    4: "feed_state",
    13: "fault",
    14: "feed_report",
    101: "power_mode",
}


def supported(config):
    """Explicit opt-in with exact model, mapping and unscaled quantity schema."""
    if (
        config.get("feeder_profile") != PROFILE
        or config.get("product_key") not in (None, "", PRODUCT)
        or config.get("model") not in MODELS
        or str(config.get("protocol_version")) != "3.5"
        or config.get("node_id")
    ):
        return False
    codes = {}
    for line in config.get("dps_strings", []):
        if type(line) is str and (
            match := re.match(r"^(\d+) \( code: ([a-z_]+) ,", line)
        ):
            codes[int(match[1])] = match[2]
    if any(codes.get(dp) != code for dp, code in CODES.items()):
        return False
    quantity_schema = any(
        str(e.get("id")) == "3"
        and e.get("platform") == "number"
        and e.get("min_value") == 1
        and e.get("max_value") == 60
        and e.get("scaling") == 1
        and e.get("step_size") == 1
        and all(
            type(e.get(k)) in (int, float)
            for k in ("min_value", "max_value", "scaling", "step_size")
        )
        and e.get("restore_on_reconnect", False) is False
        for e in config.get("entities", [])
    )
    raw_entities = [e for e in config.get("entities", []) if str(e.get("id")) == "1"]
    return quantity_schema and (
        not raw_entities
        or (
            len(raw_entities) == 1
            and raw_entities[0].get("platform") == "sensor"
            and not raw_entities[0].get("device_class")
            and not raw_entities[0].get("state_class")
            and not raw_entities[0].get("unit_of_measurement")
        )
    )


def with_native_entities(config):
    """Retain existing physical entities and DP1 unique ID; add only if absent."""
    from copy import deepcopy

    if not supported(config):
        raise ValueError("Unsupported exact feeder profile")
    entities = deepcopy(config.get("entities", []))
    if not any(str(entity.get("id")) == "1" for entity in entities):
        entities.append({"id": "1", "platform": "sensor", "friendly_name": "Meal plan"})
    return entities
