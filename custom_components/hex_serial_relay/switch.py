"""Switch platform for Serial Relay integration."""
from __future__ import annotations

import logging
from typing import Any

from homeassistant.components.switch import SwitchEntity
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers.dispatcher import async_dispatcher_connect
from homeassistant.helpers.entity import DeviceInfo
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from .const import (
    DOMAIN,
    cmd_open,
    cmd_close,
)
from .coordinator import SerialRelayCoordinator, SIGNAL_RELAY_UPDATE

_LOGGER = logging.getLogger(__name__)


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up one switch entity per detected relay channel."""
    coordinator: SerialRelayCoordinator = hass.data[DOMAIN][entry.entry_id]

    entities = [
        RelaySwitch(coordinator, entry, channel)
        for channel in range(1, coordinator.channel_count + 1)
    ]
    async_add_entities(entities)


class RelaySwitch(SwitchEntity):
    """
    Represents a single relay channel as a HA switch.

    - State is ONLY updated when the device sends a confirmed response packet.
    - Turn-on sends the physical CLOSE command; turn-off sends OPEN.
      When invert_state is True these are swapped so the UI label matches
      the user's expectation of what "on" means for their hardware.
    - The entity remains unavailable until at least one device response is
      received for its channel.
    """

    _attr_has_entity_name = True
    _attr_should_poll = False

    def __init__(
        self,
        coordinator: SerialRelayCoordinator,
        entry: ConfigEntry,
        channel: int,
    ) -> None:
        self._coordinator = coordinator
        self._channel = channel
        self._is_on: bool | None = None

        serial_port = entry.data["serial_port"]
        self._attr_unique_id = f"{entry.entry_id}_relay_{channel}"
        self._attr_name = f"Relay {channel}"
        self._attr_device_info = DeviceInfo(
            identifiers={(DOMAIN, entry.entry_id)},
            name=f"Hex Proto Serial Relay ({serial_port})",
            manufacturer="Generic",
            model="RS-232 Relay Controller",
            sw_version=f"{coordinator.channel_count}-channel",
        )

    async def async_added_to_hass(self) -> None:
        """Subscribe to state updates from the coordinator."""
        self.async_on_remove(
            async_dispatcher_connect(
                self.hass,
                f"{SIGNAL_RELAY_UPDATE}_{self._channel}",
                self._handle_state_update,
            )
        )

    # ------------------------------------------------------------------
    # State
    # ------------------------------------------------------------------

    @property
    def is_on(self) -> bool | None:
        """Return the logical on/off state (already invert-adjusted by coordinator)."""
        return self._is_on

    @property
    def available(self) -> bool:
        """Unavailable until we have received at least one confirmed response."""
        return self._is_on is not None

    # ------------------------------------------------------------------
    # Commands — invert_state swaps which physical command means "turn on"
    # ------------------------------------------------------------------

    async def async_turn_on(self, **kwargs: Any) -> None:
        """Turn the relay on (respects invert_state)."""
        if self._coordinator.invert_state:
            # Logical ON = physical OPEN
            _LOGGER.debug("Channel %d: logical ON -> physical OPEN (inverted)", self._channel)
            await self._coordinator.async_send_command(cmd_open(self._channel))
        else:
            # Logical ON = physical CLOSE
            _LOGGER.debug("Channel %d: logical ON -> physical CLOSE", self._channel)
            await self._coordinator.async_send_command(cmd_close(self._channel))

    async def async_turn_off(self, **kwargs: Any) -> None:
        """Turn the relay off (respects invert_state)."""
        if self._coordinator.invert_state:
            # Logical OFF = physical CLOSE
            _LOGGER.debug("Channel %d: logical OFF -> physical CLOSE (inverted)", self._channel)
            await self._coordinator.async_send_command(cmd_close(self._channel))
        else:
            # Logical OFF = physical OPEN
            _LOGGER.debug("Channel %d: logical OFF -> physical OPEN", self._channel)
            await self._coordinator.async_send_command(cmd_open(self._channel))

    # ------------------------------------------------------------------
    # Internal
    # ------------------------------------------------------------------

    @callback
    def _handle_state_update(self, logical_on: bool) -> None:
        """Receive a confirmed (and invert-adjusted) state from the coordinator."""
        self._is_on = logical_on
        self.async_write_ha_state()
