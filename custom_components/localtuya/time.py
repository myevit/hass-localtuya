"""Capability-only time platform; not a configurable generic DP platform."""

from .feeder_entities import setup_native_platform


async def async_setup_entry(hass, entry, async_add_entities):
    """Set up reported native feeder slots only."""
    setup_native_platform("time", hass, entry, async_add_entities)
