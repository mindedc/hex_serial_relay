"""Hex Protocol Serial Relay (RS-232) integration."""
from __future__ import annotations

import logging

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant

from .const import DOMAIN
from .coordinator import SerialRelayCoordinator

_LOGGER = logging.getLogger(__name__)

PLATFORMS = ["switch", "button"]


async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Set up Serial Relay from a config entry."""
    # Merge entry.data and entry.options so the coordinator sees everything.
    # entry.options is populated by the OptionsFlow; entry.data by the ConfigFlow.
    config = {**entry.data, **entry.options}

    coordinator = SerialRelayCoordinator(hass, config)

    try:
        await coordinator.async_start()
    except Exception as exc:
        _LOGGER.error("Failed to start Serial Relay coordinator: %s", exc)
        return False

    hass.data.setdefault(DOMAIN, {})[entry.entry_id] = coordinator

    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)

    # Reload the entry whenever options are changed via the OptionsFlow
    entry.async_on_unload(entry.add_update_listener(_async_reload_entry))

    return True


async def _async_reload_entry(hass: HomeAssistant, entry: ConfigEntry) -> None:
    """Reload the config entry when options are updated."""
    await hass.config_entries.async_reload(entry.entry_id)


async def async_unload_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Unload a config entry."""
    unload_ok = await hass.config_entries.async_unload_platforms(entry, PLATFORMS)

    if unload_ok:
        coordinator: SerialRelayCoordinator = hass.data[DOMAIN].pop(entry.entry_id)
        await coordinator.async_stop()

    return unload_ok
