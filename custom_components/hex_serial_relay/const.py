"""Constants and protocol helpers for the Serial Relay integration."""
from __future__ import annotations

DOMAIN = "serial_relay"
DEFAULT_NAME = "Hex Proto Serial Relay (RS-232)"

# Config / options keys
CONF_SERIAL_PORT = "serial_port"
CONF_INVERT_STATE = "invert_state"
CONF_CHANNEL_COUNT = "channel_count"  # stored in entry.data after first probe
CONF_BAUD_RATE = "baud_rate"
CONF_BYTESIZE = "bytesize"
CONF_STOPBITS = "stopbits"
CONF_PARITY = "parity"

# Serial parameter defaults
DEFAULT_BAUD_RATE = 9600
DEFAULT_BYTESIZE = 8
DEFAULT_STOPBITS = 1
DEFAULT_PARITY = "N"

# Keep legacy constants as aliases so coordinator imports still resolve
SERIAL_BAUD     = DEFAULT_BAUD_RATE
SERIAL_BYTESIZE = DEFAULT_BYTESIZE
SERIAL_STOPBITS = DEFAULT_STOPBITS
SERIAL_PARITY   = DEFAULT_PARITY

# Valid options for dropdowns
BAUD_RATE_OPTIONS = [1200, 2400, 4800, 9600, 19200, 38400, 57600, 115200]
BYTESIZE_OPTIONS  = [5, 6, 7, 8]
STOPBITS_OPTIONS  = [1, 2]
PARITY_OPTIONS    = {"N": "None", "E": "Even", "O": "Odd"}

RESPONSE_LENGTH = 8

# ---------------------------------------------------------------------------
# Action bytes (byte index 6 in the 8-byte command packet)
# ---------------------------------------------------------------------------
ACTION_RELAY_OPEN   = 0x01  # Open relay  (circuit broken  / OFF)
ACTION_RELAY_CLOSE  = 0x02  # Close relay (circuit complete / ON)
ACTION_RELAY_TOGGLE = 0x03  # Toggle relay
ACTION_RELAY_CYCLE  = 0x04  # Momentary cycle relay
ACTION_RELAY_QUERY  = 0x00  # Query single channel state (channel byte >= 1)
ACTION_COUNT_QUERY  = 0x00  # Query channel count       (channel byte == 0)

# Response state bytes (byte index 6 in the 8-byte response packet)
RESP_STATE_OPEN  = 0x01  # relay open  (circuit broken)
RESP_STATE_CLOSE = 0x02  # relay closed (circuit complete)

# ---------------------------------------------------------------------------
# Protocol helpers
# ---------------------------------------------------------------------------

def _checksum(payload: list[int]) -> int:
    """Return the 8-bit checksum: sum of all payload bytes mod 256."""
    return sum(payload) & 0xFF


def build_command(channel: int, action: int) -> bytes:
    """
    Build an 8-byte command packet for any channel and action.

    Packet layout (all fixed-length, no terminator):
      [0] 0x55  — fixed preamble
      [1] 0x56  — fixed preamble
      [2] 0x00  — fixed
      [3] 0x00  — fixed
      [4] 0x00  — fixed
      [5] channel  0 = count query, 1-32 = relay channel
      [6] action   see ACTION_* constants above
      [7] checksum sum(bytes[0:7]) & 0xFF
    """
    if not (0 <= channel <= 32):
        raise ValueError(f"Channel must be 0-32, got {channel}")
    payload = [0x55, 0x56, 0x00, 0x00, 0x00, channel, action]
    return bytes(payload + [_checksum(payload)])


# Convenience command builders -----------------------------------------------

def cmd_open(channel: int) -> bytes:
    """Open relay — circuit broken (switch OFF)."""
    return build_command(channel, ACTION_RELAY_OPEN)

def cmd_close(channel: int) -> bytes:
    """Close relay — circuit complete (switch ON)."""
    return build_command(channel, ACTION_RELAY_CLOSE)

def cmd_toggle(channel: int) -> bytes:
    """Toggle relay."""
    return build_command(channel, ACTION_RELAY_TOGGLE)

def cmd_cycle(channel: int) -> bytes:
    """Momentary cycle relay."""
    return build_command(channel, ACTION_RELAY_CYCLE)

def cmd_query(channel: int) -> bytes:
    """Query state of a single relay channel (channel 1-32)."""
    return build_command(channel, ACTION_RELAY_QUERY)

# Channel-count query packet (channel byte = 0)
QUERY_CHANNEL_COUNT_CMD: bytes = build_command(0, ACTION_COUNT_QUERY)


# ---------------------------------------------------------------------------
# Response decoding
# ---------------------------------------------------------------------------

def verify_checksum(packet: bytes) -> bool:
    """
    Return True if byte[7] == sum(bytes[0:7]) & 0xFF.

    Example — relay open ch1 response:
      0x33+0x3C+0x00+0x00+0x00+0x01+0x01 = 0x71  checksum byte = 0x71  ✓
    """
    return (
        len(packet) == RESPONSE_LENGTH
        and packet[7] == (sum(packet[:7]) & 0xFF)
    )


def decode_response(packet: bytes) -> tuple[int, bool] | None:
    """
    Decode an 8-byte relay-state response packet.

    Returns ``(channel, is_closed)`` where:
      - *channel*   is the relay channel number (1-32)
      - *is_closed* is True when the relay is closed (ON), False when open (OFF)

    Returns ``None`` for any packet that cannot be parsed as a relay-state
    response (bad checksum, wrong header, unrecognised state byte, or the
    channel-count response whose channel byte equals the board's channel count
    rather than a relay index — the coordinator avoids passing count responses
    to this function by design).

    Response packet layout:
      [0] 0x33  fixed header
      [1] 0x3C  fixed header
      [2] 0x00  fixed
      [3] 0x00  fixed
      [4] 0x00  fixed
      [5] channel  (1-32)
      [6] state    (0x01 = open, 0x02 = closed)
      [7] checksum
    """
    if not verify_checksum(packet):
        return None
    if packet[0] != 0x33 or packet[1] != 0x3C:
        return None

    channel = packet[5]
    state   = packet[6]

    if not (1 <= channel <= 32):
        return None
    if state == RESP_STATE_OPEN:
        return (channel, False)
    if state == RESP_STATE_CLOSE:
        return (channel, True)
    return None


def decode_count_response(packet: bytes) -> int | None:
    """
    Decode the channel-count query response.

    Returns the board's channel count (1-32) or ``None`` if the packet is
    invalid.  Byte index 5 carries the count; byte index 6 is always 0x02.

    On a 4-channel board: ``33 3C 00 00 00 04 02 75``
    This is byte-for-byte identical to a ch4-close state response — the
    coordinator resolves the ambiguity by calling this function only for the
    very first response after issuing QUERY_CHANNEL_COUNT_CMD (before the
    normal reader loop starts).
    """
    if not verify_checksum(packet):
        return None
    if packet[0] != 0x33 or packet[1] != 0x3C:
        return None
    count = packet[5]
    if 1 <= count <= 32:
        return count
    return None
