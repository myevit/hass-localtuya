"""Exact-model gating and strict packet semantics."""

import base64
from copy import deepcopy
from importlib import import_module
from importlib.util import find_spec
import pytest

MODULE = "custom_components.localtuya.core.ha_entities.pet_feeders"


def profile():
    assert find_spec(MODULE), "native exact-model descriptor missing"
    return import_module(MODULE)


def config():
    return {
        "feeder_profile": "ft11w",
        "product_key": "gh0lmtxb5fumm0ad",
        "model": "M&C Smart Feeder Titan WIFI",
        "protocol_version": "3.5",
        "dps_strings": [
            f"{dp} ( code: {code} , value: synthetic )"
            for dp, code in {
                1: "meal_plan",
                2: "quick_feed",
                3: "manual_feed",
                4: "feed_state",
                13: "fault",
                14: "feed_report",
                101: "power_mode",
            }.items()
        ],
        "entities": [
            {
                "id": "3",
                "platform": "number",
                "min_value": 1,
                "max_value": 60,
                "scaling": 1,
                "step_size": 1,
                "restore_on_reconnect": False,
            }
        ],
    }


def meal(time="12:34", portions=3, enabled=True):
    return dict(
        days=[
            "monday",
            "tuesday",
            "wednesday",
            "thursday",
            "friday",
            "saturday",
            "sunday",
        ],
        time=time,
        portions=portions,
        enabled=enabled,
    )


def test_exact_profile_gating():
    p = profile()
    assert p.supported(config())
    alias = config()
    alias.pop("product_key")
    assert p.supported(alias)
    for key, value in [
        ("product_key", "other"),
        ("protocol_version", "3.4"),
        ("node_id", "child"),
        ("feeder_profile", None),
        ("model", "other"),
    ]:
        c = config()
        c[key] = value
        assert not p.supported(c)
    c = config()
    c["entities"][0]["max_value"] = 61
    assert not p.supported(c)
    c = config()
    c["dps_strings"][0] = "1 ( code: other , value: synthetic )"
    assert not p.supported(c)


def test_codec_preserves_complete_records():
    p = profile()
    meals = [meal(), meal("20:56", 60, False)]
    raw = p.encode(meals)
    assert p.decode(raw) == meals
    assert base64.b64decode(raw) == bytes([127, 12, 34, 3, 1, 127, 20, 56, 60, 0])
    for bad in [
        "!",
        "AAAA",
        base64.b64encode(bytes([1, 12, 34, 3, 1])).decode(),
        base64.b64encode(bytes([127, 24, 34, 3, 1])).decode(),
    ]:
        with pytest.raises(ValueError):
            p.decode(bad)
    for k, v in [
        ("portions", True),
        ("portions", 61),
        ("enabled", 1),
        ("time", "1:00"),
        ("days", ["monday"]),
    ]:
        m = meal()
        m[k] = v
        with pytest.raises(ValueError):
            p.encode([m])
    with pytest.raises(ValueError):
        p.encode([meal()] * 11)


def test_auto_setup_requires_exact_profile_and_preserves_existing_entities():
    from custom_components.localtuya.core.ha_entities import gen_localtuya_entities

    c = config()
    c["friendly_name"] = "Synthetic feeder"
    from custom_components.localtuya.core.ha_entities.pet_feeders import (
        with_native_entities,
    )

    result = with_native_entities(c)
    assert result[0] == c["entities"][0]
    assert result[-1]["id"] == "1" and result[-1]["platform"] == "sensor"
    assert c["entities"] == [result[0]]
    assert gen_localtuya_entities(c, "cwwsq") == result
    other = dict(c, product_key="other")
    assert gen_localtuya_entities(other, "cwwsq") != result
