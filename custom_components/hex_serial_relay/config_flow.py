"""Config flow for the Hex Proto Serial Relay integration."""
from __future__ import annotations

import logging
import serial
import serial.tools.list_ports
from typing import Any

import voluptuous as vol

from homeassistant import config_entries
from homeassistant.data_entry_flow import FlowResult

from .const import (
    DOMAIN,
    CONF_SERIAL_PORT,
    CONF_INVERT_STATE,
    CONF_CHANNEL_COUNT,
    CONF_BAUD_RATE,
    CONF_BYTESIZE,
    CONF_STOPBITS,
    CONF_PARITY,
    DEFAULT_BAUD_RATE,
    DEFAULT_BYTESIZE,
    DEFAULT_STOPBITS,
    DEFAULT_PARITY,
    BAUD_RATE_OPTIONS,
    BYTESIZE_OPTIONS,
    STOPBITS_OPTIONS,
    PARITY_OPTIONS,
    QUERY_CHANNEL_COUNT_CMD,
    RESPONSE_LENGTH,
    decode_count_response,
)

_LOGGER = logging.getLogger(__name__)

CONNECT_TIMEOUT = 3.0


def _list_serial_ports() -> list[str]:
    """Return a sorted list of available serial port device paths."""
    return sorted(port.device for port in serial.tools.list_ports.comports())


def _probe_device(
    serial_port: str,
    baud: int,
    bytesize: int,
    stopbits: int,
    parity: str,
) -> int | None:
    """
    Open *serial_port* with the given parameters, send the channel-count query,
    and return the channel count, or ``None`` on any failure.
    """
    try:
        ser = serial.Serial(
            port=serial_port,
            baudrate=baud,
            bytesize=bytesize,
            stopbits=stopbits,
            parity=parity,
            timeout=CONNECT_TIMEOUT,
        )
    except Exception as exc:
        _LOGGER.debug("Could not open %s: %s", serial_port, exc)
        return None

    try:
        ser.write(QUERY_CHANNEL_COUNT_CMD)
        ser.flush()
        response = ser.read(RESPONSE_LENGTH)
        count = decode_count_response(response)
        if count:
            _LOGGER.debug("Probe OK on %s — %d channels", serial_port, count)
        return count
    except Exception as exc:
        _LOGGER.debug("Probe error on %s: %s", serial_port, exc)
        return None
    finally:
        try:
            ser.close()
        except Exception:
            pass


def _full_schema(
    ports: list[str],
    port: str = "",
    baud: int = DEFAULT_BAUD_RATE,
    bytesize: int = DEFAULT_BYTESIZE,
    stopbits: int = DEFAULT_STOPBITS,
    parity: str = DEFAULT_PARITY,
    invert: bool = False,
) -> vol.Schema:
    """Single-page schema covering every configurable field."""
    return vol.Schema(
        {
            vol.Required(CONF_SERIAL_PORT, default=port or vol.UNDEFINED): vol.In(ports),
            vol.Required(CONF_BAUD_RATE,  default=baud):     vol.In(BAUD_RATE_OPTIONS),
            vol.Required(CONF_BYTESIZE,   default=bytesize): vol.In(BYTESIZE_OPTIONS),
            vol.Required(CONF_STOPBITS,   default=stopbits): vol.In(STOPBITS_OPTIONS),
            vol.Required(CONF_PARITY,     default=parity):   vol.In(list(PARITY_OPTIONS.keys())),
            vol.Required(CONF_INVERT_STATE, default=invert): bool,
        }
    )


# ---------------------------------------------------------------------------
# Config flow
# ---------------------------------------------------------------------------

class SerialRelayConfigFlow(config_entries.ConfigFlow, domain=DOMAIN):
    """
    Single-page config flow — all settings on one form, device probed on submit.
    Reconfigure mirrors the same form pre-populated with existing values.
    """

    VERSION = 1

    async def async_step_user(
        self, user_input: dict[str, Any] | None = None
    ) -> FlowResult:
        """Single setup step: port + serial params + invert → probe → create entry."""
        errors: dict[str, str] = {}

        ports = await self.hass.async_add_executor_job(_list_serial_ports)
        if not ports:
            return self.async_abort(reason="no_ports")

        if user_input is not None:
            serial_port = user_input[CONF_SERIAL_PORT]

            await self.async_set_unique_id(serial_port)
            self._abort_if_unique_id_configured()

            try:
                count = await self.hass.async_add_executor_job(
                    _probe_device,
                    serial_port,
                    user_input[CONF_BAUD_RATE],
                    user_input[CONF_BYTESIZE],
                    user_input[CONF_STOPBITS],
                    user_input[CONF_PARITY],
                )
            except Exception:
                _LOGGER.exception("Unexpected error probing %s", serial_port)
                errors["base"] = "unknown"
                count = None

            if count is None and "base" not in errors:
                errors["base"] = "cannot_connect"

            if not errors:
                return self.async_create_entry(
                    title=f"Hex Proto Serial Relay ({serial_port})",
                    data={
                        CONF_SERIAL_PORT:   serial_port,
                        CONF_BAUD_RATE:     user_input[CONF_BAUD_RATE],
                        CONF_BYTESIZE:      user_input[CONF_BYTESIZE],
                        CONF_STOPBITS:      user_input[CONF_STOPBITS],
                        CONF_PARITY:        user_input[CONF_PARITY],
                        CONF_INVERT_STATE:  user_input[CONF_INVERT_STATE],
                        CONF_CHANNEL_COUNT: count,
                    },
                )

            # Re-show form preserving the user's choices on error
            return self.async_show_form(
                step_id="user",
                data_schema=_full_schema(
                    ports,
                    port=user_input[CONF_SERIAL_PORT],
                    baud=user_input[CONF_BAUD_RATE],
                    bytesize=user_input[CONF_BYTESIZE],
                    stopbits=user_input[CONF_STOPBITS],
                    parity=user_input[CONF_PARITY],
                    invert=user_input[CONF_INVERT_STATE],
                ),
                errors=errors,
            )

        return self.async_show_form(
            step_id="user",
            data_schema=_full_schema(ports),
            errors=errors,
        )

    # ------------------------------------------------------------------
    # Reconfigure — same single form, pre-populated with current values
    # ------------------------------------------------------------------

    async def async_step_reconfigure(
        self, user_input: dict[str, Any] | None = None
    ) -> FlowResult:
        """Reconfigure: same single-page form as initial setup."""
        errors: dict[str, str] = {}

        ports = await self.hass.async_add_executor_job(_list_serial_ports)
        if not ports:
            return self.async_abort(reason="no_ports")

        current_entry = self._get_reconfigure_entry()
        d = current_entry.data

        if user_input is not None:
            serial_port = user_input[CONF_SERIAL_PORT]

            try:
                count = await self.hass.async_add_executor_job(
                    _probe_device,
                    serial_port,
                    user_input[CONF_BAUD_RATE],
                    user_input[CONF_BYTESIZE],
                    user_input[CONF_STOPBITS],
                    user_input[CONF_PARITY],
                )
            except Exception:
                _LOGGER.exception("Unexpected error probing %s", serial_port)
                errors["base"] = "unknown"
                count = None

            if count is None and "base" not in errors:
                errors["base"] = "cannot_connect"

            if not errors:
                return self.async_update_reload_and_abort(
                    current_entry,
                    title=f"Hex Proto Serial Relay ({serial_port})",
                    data={
                        CONF_SERIAL_PORT:   serial_port,
                        CONF_BAUD_RATE:     user_input[CONF_BAUD_RATE],
                        CONF_BYTESIZE:      user_input[CONF_BYTESIZE],
                        CONF_STOPBITS:      user_input[CONF_STOPBITS],
                        CONF_PARITY:        user_input[CONF_PARITY],
                        CONF_INVERT_STATE:  user_input[CONF_INVERT_STATE],
                        CONF_CHANNEL_COUNT: count,
                    },
                )

            return self.async_show_form(
                step_id="reconfigure",
                data_schema=_full_schema(
                    ports,
                    port=user_input[CONF_SERIAL_PORT],
                    baud=user_input[CONF_BAUD_RATE],
                    bytesize=user_input[CONF_BYTESIZE],
                    stopbits=user_input[CONF_STOPBITS],
                    parity=user_input[CONF_PARITY],
                    invert=user_input[CONF_INVERT_STATE],
                ),
                errors=errors,
            )

        return self.async_show_form(
            step_id="reconfigure",
            data_schema=_full_schema(
                ports,
                port=d.get(CONF_SERIAL_PORT, ""),
                baud=d.get(CONF_BAUD_RATE, DEFAULT_BAUD_RATE),
                bytesize=d.get(CONF_BYTESIZE, DEFAULT_BYTESIZE),
                stopbits=d.get(CONF_STOPBITS, DEFAULT_STOPBITS),
                parity=d.get(CONF_PARITY, DEFAULT_PARITY),
                invert=d.get(CONF_INVERT_STATE, False),
            ),
            errors=errors,
        )

    # ------------------------------------------------------------------
    # Options flow (gear icon)
    # ------------------------------------------------------------------

    @staticmethod
    def async_get_options_flow(
        config_entry: config_entries.ConfigEntry,
    ) -> config_entries.OptionsFlow:
        return SerialRelayOptionsFlow(config_entry)


# ---------------------------------------------------------------------------
# Options flow — same single-page form
# ---------------------------------------------------------------------------

class SerialRelayOptionsFlow(config_entries.OptionsFlow):
    """Options flow — same single-page form as setup, pre-populated."""

    def __init__(self, config_entry: config_entries.ConfigEntry) -> None:
        self._config_entry = config_entry

    async def async_step_init(
        self, user_input: dict[str, Any] | None = None
    ) -> FlowResult:
        errors: dict[str, str] = {}

        ports = await self.hass.async_add_executor_job(_list_serial_ports)
        if not ports:
            return self.async_abort(reason="no_ports")

        d = self._config_entry.data

        if user_input is not None:
            serial_port = user_input[CONF_SERIAL_PORT]

            try:
                count = await self.hass.async_add_executor_job(
                    _probe_device,
                    serial_port,
                    user_input[CONF_BAUD_RATE],
                    user_input[CONF_BYTESIZE],
                    user_input[CONF_STOPBITS],
                    user_input[CONF_PARITY],
                )
            except Exception:
                _LOGGER.exception("Unexpected error probing %s", serial_port)
                errors["base"] = "unknown"
                count = None

            if count is None and "base" not in errors:
                errors["base"] = "cannot_connect"

            if not errors:
                new_data = {
                    **d,
                    CONF_SERIAL_PORT:   serial_port,
                    CONF_BAUD_RATE:     user_input[CONF_BAUD_RATE],
                    CONF_BYTESIZE:      user_input[CONF_BYTESIZE],
                    CONF_STOPBITS:      user_input[CONF_STOPBITS],
                    CONF_PARITY:        user_input[CONF_PARITY],
                    CONF_INVERT_STATE:  user_input[CONF_INVERT_STATE],
                    CONF_CHANNEL_COUNT: count,
                }
                self.hass.config_entries.async_update_entry(
                    self._config_entry, data=new_data
                )
                return self.async_create_entry(title="", data={})

            return self.async_show_form(
                step_id="init",
                data_schema=_full_schema(
                    ports,
                    port=user_input[CONF_SERIAL_PORT],
                    baud=user_input[CONF_BAUD_RATE],
                    bytesize=user_input[CONF_BYTESIZE],
                    stopbits=user_input[CONF_STOPBITS],
                    parity=user_input[CONF_PARITY],
                    invert=user_input[CONF_INVERT_STATE],
                ),
                errors=errors,
            )

        return self.async_show_form(
            step_id="init",
            data_schema=_full_schema(
                ports,
                port=d.get(CONF_SERIAL_PORT, ""),
                baud=d.get(CONF_BAUD_RATE, DEFAULT_BAUD_RATE),
                bytesize=d.get(CONF_BYTESIZE, DEFAULT_BYTESIZE),
                stopbits=d.get(CONF_STOPBITS, DEFAULT_STOPBITS),
                parity=d.get(CONF_PARITY, DEFAULT_PARITY),
                invert=d.get(CONF_INVERT_STATE, False),
            ),
            errors=errors,
        )
