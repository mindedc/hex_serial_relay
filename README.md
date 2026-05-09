# Hex Proto Serial Relay (RS-232) Integration

A Home Assistant custom integration for controlling generic RS-232 relay controllers using the Hex Proto protocol. Supports relay boards with 1–32 channels.

## Features

- **Automatic channel detection** — Probes the device on setup to determine the number of relay channels
- **Configurable serial parameters** — Baud rate, data bits, stop bits, and parity (defaults: 9600, 8, N, 1)
- **Switch entities** — One per relay channel for ON/OFF control
- **Button entities** — Cycle and toggle actions per channel (disabled by default; enable as needed)
- **Invert state** — Compensates for hardware that reports relay state inverted relative to UI expectations
- **Reconfigurable** — Change serial port, parameters, or invert setting at runtime without losing entities
- **State-on-confirm** — Relay state updates only when the device confirms via response packet, never optimistically

## Installation

1. Download the integration and extract it into your Home Assistant `custom_components` directory:
   ```
   custom_components/serial_relay/
   ```

2. Restart Home Assistant.

3. Go to **Settings → Devices & Services → Create Automation** and search for "Hex Proto Serial Relay".

## Configuration

All settings are on a single form:

- **Serial Port** — The device path (e.g., `/dev/ttyUSB0` on Linux, `COM3` on Windows)
- **Baud Rate** — Communication speed (default: 9600)
- **Data Bits** — Typically 8
- **Stop Bits** — Typically 1
- **Parity** — None (N), Even (E), or Odd (O)
- **Invert relay state** — Enable if ON/OFF appear reversed in the UI

The device is automatically probed on setup to detect the number of relay channels.

## Entities

### Switches
One switch per detected relay channel (enabled by default):
- `switch.hex_proto_serial_relay_relay_N` — Turn relay N on/off

### Buttons
Two buttons per channel (both disabled by default):
- `button.hex_proto_serial_relay_relay_N_cycle` — Momentary cycle relay N
- `button.hex_proto_serial_relay_relay_N_toggle` — Toggle relay N

Enable or disable buttons per-entity via the entity registry as needed.

## Protocol Details

The integration uses a fixed 8-byte packet protocol:

**Commands:**
- Open relay: `55 56 00 00 00 <CH> 01 <CS>`
- Close relay: `55 56 00 00 00 <CH> 02 <CS>`
- Toggle relay: `55 56 00 00 00 <CH> 03 <CS>`
- Cycle relay: `55 56 00 00 00 <CH> 04 <CS>`
- Query channel state: `55 56 00 00 00 <CH> 00 <CS>`
- Query channel count: `55 56 00 00 00 00 00 <CS>`

**Responses:**
- Relay open: `33 3C 00 00 00 <CH> 01 <CS>`
- Relay closed: `33 3C 00 00 00 <CH> 02 <CS>`
- Channel count: `33 3C 00 00 00 <COUNT> 02 <CS>`

All checksums are simple byte sums: `CS = (sum of bytes 0–6) & 0xFF`.

## Troubleshooting

### Device not detected
- Check the serial port name is correct for your OS
- Verify the cable connection and power to the relay board
- Try adjusting baud rate and parity if the defaults don't work
- Ensure no other application has the port open

### Entities show as unavailable
- The integration waits for a confirmed device response before marking entities available
- Check the Home Assistant logs for communication errors
- Verify the relay board is powered and responding

### ON/OFF appear reversed
- Enable the "Invert relay state" option in configuration and re-probe the device

## Support

For issues, check your Home Assistant logs (`Settings → System → Logs`) for `serial_relay` debug output.
