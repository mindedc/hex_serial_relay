"""Coordinator for the Serial Relay integration."""
from __future__ import annotations

import asyncio
import logging
import serial
from typing import Any

import aioserial

from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers.dispatcher import async_dispatcher_send

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
    RESPONSE_LENGTH,
    QUERY_CHANNEL_COUNT_CMD,
    cmd_query,
    verify_checksum,
    decode_response,
    decode_count_response,
)

_LOGGER = logging.getLogger(__name__)

SIGNAL_RELAY_UPDATE = f"{DOMAIN}_relay_update"

# How long (seconds) to wait for the count-query response during startup
COUNT_PROBE_TIMEOUT = 3.0


class SerialRelayCoordinator:
    """
    Manages the async serial connection to the relay controller.

    Design principles:
    - Channel count is discovered once at startup via a blocking synchronous
      read *before* the async reader loop begins, eliminating any ambiguity
      between the count-response and the ch<N>-close state response.
    - The async reader loop only ever sees relay-state packets; it never needs
      to distinguish count responses.
    - Entity state is updated ONLY when the device sends a confirmed response —
      never optimistically on command send.
    - invert_state flips the logical meaning of open/close for hardware that
      reports states inverted relative to the UI expectation.
    """

    def __init__(self, hass: HomeAssistant, config: dict[str, Any]) -> None:
        self.hass = hass
        self.serial_port: str = config[CONF_SERIAL_PORT]
        self.invert_state: bool = config.get(CONF_INVERT_STATE, False)

        # Serial parameters — use stored values, fall back to protocol defaults
        self._baud:     int = config.get(CONF_BAUD_RATE, DEFAULT_BAUD_RATE)
        self._bytesize: int = config.get(CONF_BYTESIZE,  DEFAULT_BYTESIZE)
        self._stopbits: int = config.get(CONF_STOPBITS,  DEFAULT_STOPBITS)
        self._parity:   str = config.get(CONF_PARITY,    DEFAULT_PARITY)

        # channel_count is set either from stored config data (fast path on
        # subsequent startups) or by probing the device (first start / reconfigure).
        self.channel_count: int = config.get(CONF_CHANNEL_COUNT, 0)

        # Relay state: channel (1-N) -> True=ON, False=OFF, None=unknown
        # "ON" always means "logically on" after invert_state is applied.
        self.relay_state: dict[int, bool | None] = {}

        self.serial: aioserial.AioSerial | None = None
        self.txQueue: asyncio.Queue[bytes] = asyncio.Queue()

        self._tasks: list[asyncio.Task] = []
        self._running = False

    # ------------------------------------------------------------------
    # Lifecycle
    # ------------------------------------------------------------------

    async def async_start(self) -> None:
        """
        Open the serial port, determine channel count, then start I/O tasks.

        Channel count is probed synchronously in an executor thread so the
        result is known before any async I/O begins.  This keeps the reader
        loop simple — it never sees a count-response packet.

        If a channel count was already stored in the config entry (i.e. this
        is not the first startup), we trust it and skip the probe, saving a
        round-trip and avoiding any startup relay-state noise.
        """
        _LOGGER.debug("Opening serial port %s", self.serial_port)
        try:
            self.serial = aioserial.AioSerial(
                port=self.serial_port,
                baudrate=self._baud,
                bytesize=self._bytesize,
                stopbits=self._stopbits,
                parity=self._parity,
            )
        except Exception as exc:
            _LOGGER.error("Failed to open %s: %s", self.serial_port, exc)
            raise

        if self.channel_count == 0:
            # First start or reconfigure — probe via blocking read on the open port
            count = await self.hass.async_add_executor_job(self._probe_channel_count)
            if count:
                self.channel_count = count
                _LOGGER.info("Detected %d relay channel(s) on %s", count, self.serial_port)
            else:
                # Fallback: assume 4 channels so we're not completely broken
                self.channel_count = 4
                _LOGGER.warning(
                    "Could not detect channel count; defaulting to 4"
                )

        # Initialise state table now that we know channel_count
        self.relay_state = {ch: None for ch in range(1, self.channel_count + 1)}

        self._running = True
        self._tasks = [
            asyncio.get_event_loop().create_task(
                self._reader_task(), name="serial_relay_reader"
            ),
            asyncio.get_event_loop().create_task(
                self._writer_task(), name="serial_relay_writer"
            ),
        ]
        _LOGGER.info(
            "Serial Relay coordinator started on %s (%d channels, invert=%s)",
            self.serial_port, self.channel_count, self.invert_state,
        )

        # Query every channel's current state so entities become available
        # immediately rather than waiting for the first user interaction.
        asyncio.get_event_loop().create_task(
            self._query_all_channels(), name="serial_relay_init_query"
        )

    def _probe_channel_count(self) -> int | None:
        """
        Blocking probe — runs in an executor thread.

        Sends QUERY_CHANNEL_COUNT_CMD on the already-open aioserial port's
        underlying serial.Serial object and reads back one response packet.
        We bypass the async queue here because the reader task has not started
        yet, so there is no contention on the port.
        """
        try:
            raw: serial.Serial = self.serial.serial  # underlying pyserial object
            raw.timeout = COUNT_PROBE_TIMEOUT
            raw.write(QUERY_CHANNEL_COUNT_CMD)
            raw.flush()
            packet = raw.read(RESPONSE_LENGTH)
            raw.timeout = None  # restore non-blocking for the async reader
            return decode_count_response(packet)
        except Exception as exc:
            _LOGGER.warning("Channel count probe failed: %s", exc)
            return None

    async def async_stop(self) -> None:
        """Stop background tasks and close the serial port."""
        self._running = False
        for task in self._tasks:
            task.cancel()
        await asyncio.gather(*self._tasks, return_exceptions=True)
        self._tasks.clear()

        if self.serial and self.serial.is_open:
            try:
                self.serial.close()
            except Exception:
                pass
        self.serial = None
        _LOGGER.info("Serial Relay coordinator stopped")

    # ------------------------------------------------------------------
    # Background tasks
    # ------------------------------------------------------------------

    async def _query_all_channels(self) -> None:
        """Query every channel's state shortly after startup."""
        await asyncio.sleep(0.5)  # let HA finish entity registration first
        _LOGGER.debug("Querying initial state for %d channels", self.channel_count)
        for ch in range(1, self.channel_count + 1):
            await self.txQueue.put(cmd_query(ch))
            await asyncio.sleep(0.05)  # small inter-command gap

    async def _reader_task(self) -> None:
        """
        Continuously read 8-byte response packets from the serial port.

        Self-synchronising: reads one byte at a time and slides the window
        until the 0x33 0x3C header is found, then collects the remaining
        6 bytes to form a complete packet.  Bad checksums are discarded.
        """
        _LOGGER.debug("Reader task started")
        buf = bytearray()

        while self._running:
            try:
                chunk = await self.serial.read_async(1)
                if not chunk:
                    await asyncio.sleep(0.01)
                    continue

                buf.extend(chunk)

                # Slide window until we have a valid header in position 0-1
                if len(buf) >= 2 and not (buf[0] == 0x33 and buf[1] == 0x3C):
                    buf = buf[1:]
                    continue

                if len(buf) < RESPONSE_LENGTH:
                    continue

                packet = bytes(buf[:RESPONSE_LENGTH])
                buf = buf[RESPONSE_LENGTH:]

                if not verify_checksum(packet):
                    _LOGGER.warning("Checksum failure — discarding: %s", packet.hex(" ").upper())
                    continue

                self._handle_response(packet)

            except asyncio.CancelledError:
                break
            except Exception as exc:
                _LOGGER.error("Reader task error: %s", exc)
                await asyncio.sleep(1)

        _LOGGER.debug("Reader task stopped")

    async def _writer_task(self) -> None:
        """Drain the TX queue and write bytes to the serial port."""
        _LOGGER.debug("Writer task started")
        while self._running:
            try:
                data = await self.txQueue.get()
                if self.serial and self.serial.is_open:
                    await self.serial.write_async(data)
                    _LOGGER.debug("TX: %s", data.hex(" ").upper())
                self.txQueue.task_done()
            except asyncio.CancelledError:
                break
            except Exception as exc:
                _LOGGER.error("Writer task error: %s", exc)
                await asyncio.sleep(0.1)

        _LOGGER.debug("Writer task stopped")

    # ------------------------------------------------------------------
    # Protocol handling
    # ------------------------------------------------------------------

    @callback
    def _handle_response(self, packet: bytes) -> None:
        """Parse a relay-state response and dispatch to entity listeners."""
        _LOGGER.debug("RX: %s", packet.hex(" ").upper())

        result = decode_response(packet)
        if result is None:
            _LOGGER.warning("Unrecognised response: %s", packet.hex(" ").upper())
            return

        channel, is_closed = result

        # Apply invert_state: swap the logical meaning of open/close
        logical_on = is_closed if not self.invert_state else not is_closed

        self.relay_state[channel] = logical_on
        _LOGGER.debug(
            "Channel %d: physical=%s logical=%s",
            channel,
            "CLOSED" if is_closed else "OPEN",
            "ON" if logical_on else "OFF",
        )

        async_dispatcher_send(self.hass, f"{SIGNAL_RELAY_UPDATE}_{channel}", logical_on)

    # ------------------------------------------------------------------
    # Command helpers
    # ------------------------------------------------------------------

    async def async_send_command(self, cmd_bytes: bytes) -> None:
        """Queue a raw command for transmission."""
        await self.txQueue.put(cmd_bytes)
