"""Button platform for Serial Relay integration."""
from __future__ import annotations

import logging
from enum import Enum

from homeassistant.components.button import ButtonEntity
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity import DeviceInfo
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from .const import DOMAIN, cmd_cycle, cmd_toggle
from .coordinator import SerialRelayCoordinator

_LOGGER = logging.getLogger(__name__)


class RelayAction(str, Enum):
    CYCLE  = "cycle"
    TOGGLE = "toggle"


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up cycle and toggle button entities for each detected channel."""
    coordinator: SerialRelayCoordinator = hass.data[DOMAIN][entry.entry_id]

    entities: list[RelayButton] = []
    for channel in range(1, coordinator.channel_count + 1):
        entities.append(RelayButton(coordinator, entry, channel, RelayAction.CYCLE))
        entities.append(RelayButton(coordinator, entry, channel, RelayAction.TOGGLE))

    async_add_entities(entities)


class RelayButton(ButtonEntity):
    """
    A button that sends a cycle or toggle command to a single relay channel.

    Both cycle and toggle buttons are disabled by default.  The user can
    enable individual buttons from the entity registry at any time.
    """

    _attr_has_entity_name = True
    _attr_should_poll = False
    # Disabled by default — user opts in per entity
    _attr_entity_registry_enabled_default = False

    def __init__(
        self,
        coordinator: SerialRelayCoordinator,
        entry: ConfigEntry,
        channel: int,
        action: RelayAction,
    ) -> None:
        self._coordinator = coordinator
        self._channel = channel
        self._action = action

        serial_port = entry.data["serial_port"]
        self._attr_unique_id = f"{entry.entry_id}_relay_{channel}_{action.value}"

        if action == RelayAction.CYCLE:
            self._attr_name = f"Relay {channel} Cycle"
            self._cmd_fn = cmd_cycle
        else:
            self._attr_name = f"Relay {channel} Toggle"
            self._cmd_fn = cmd_toggle

        self._attr_device_info = DeviceInfo(
            identifiers={(DOMAIN, entry.entry_id)},
            name=f"Hex Proto Serial Relay ({serial_port})",
            manufacturer="Generic",
            model="RS-232 Relay Controller",
            sw_version=f"{coordinator.channel_count}-channel",
        )

    async def async_press(self) -> None:
        """Send the cycle or toggle command."""
        _LOGGER.debug(
            "Channel %d: %s", self._channel, self._action.value.upper()
        )
        await self._coordinator.async_send_command(self._cmd_fn(self._channel))
        # The device will respond with the resulting open/close state packet,
        # which the coordinator will dispatch to the corresponding switch entity.
