# Domat MW240-B Blinds

![Generic blinds icon](custom_components/domat_mw240/brand/icon.png)

A community Home Assistant custom integration for Domat MW240-B blinds on a local Modbus RTU / RS-485 bus. No cloud service, Node-RED flow, or MQTT bridge is required.

**Version 0.3.0 · Home Assistant 2026.9.4 or newer · Python 3.14**

[Installation and migration](INSTALL.md) · [Changelog](CHANGELOG.md) · [Report an issue](https://github.com/GasperLipovz/ha-domat-mw240/issues)

## Features

- Configure a bus in **Settings → Devices & services**. Enter the serial path, serial settings, and an explicit list of module addresses and names; no YAML is needed for a new installation.
- One configuration entry per bus and one Home Assistant device per module. A bus can contain 20 individually addressed modules; addresses must be unique integers from **1 to 247**. Address 0 / broadcast is never allowed.
- Native cover controls: **UP, DOWN, STOP**, and timed height estimates.
- Single-step tilt-up and tilt-down buttons for FW17. These are physical steps, **not an absolute slat-angle percentage**.
- Pending travel-time numbers and an explicit **Apply travel times (temporary)** action. Editing a pending number does not write to the module. Applying writes working RAM only.
- An explicit, separately permission-gated EEPROM save on **FW17 only**. Saving commits **all current RAM configuration**, not just travel time.
- Legacy YAML import preserves identifiers based on the stable bus name and module address. Existing UI configuration takes precedence over subsequent YAML imports.

## Safety and limitations

**This integration controls moving equipment.** Keep people and objects clear, retain the manufacturer's motor limits and protection, and commission one module under supervision before enabling automations. Home Assistant and serial communication are not safety systems.

- Movement commands, timing changes, and EEPROM saving are **separate permissions, all disabled by default**. Enable only what you need after read-only checks. Disabling movement commands also prevents sending STOP through this integration; keep a local stop available.
- Startup and polling do not apply settings, save EEPROM, reset or initialize modules, or replay old actions. Expired/offline commands are not queued for later replay.
- Height is an estimate based on travel time, not measured position. External/local controls, interrupted travel, power loss, or communication failures can make it inaccurate. Do not use it for safety decisions.
- FW17 has one shared travel time. FW21 and later have separate up/down travel-time support, but EEPROM saving remains blocked on those untested firmware versions. Do not infer full firmware compatibility from a readable version register.
- Hardware verification covers **one physical FW17 module**. The 20-module case is tested offline with simulated devices; it is **not** a claim of a physically commissioned 20-module bus.
- The integration does not program physical module addresses or serial settings. Editing a Home Assistant address list or baud rate changes how Home Assistant connects; it does not reconfigure the hardware.
- Use a dedicated adapter with **one Modbus master**. Do not share it with Home Assistant's built-in Modbus integration, vendor tools, Node-RED, or another process. No full-address scan or automatic baud/address probing is performed.

## Install and configure

Follow [INSTALL.md](INSTALL.md). The intended HACS route is a **custom repository**, not a claim that the integration is in HACS's default store. Installation and every integration update require a **Home Assistant Core restart**. Updating the integration does not recommission motors or require rewriting their travel times.

## Development

Run offline tests from the repository root using Python 3.14:

```sh
python3.14 -m venv .venv
.venv/bin/python -m pip install -r requirements_test.txt
.venv/bin/python -m unittest discover -s tests -v
```

The pinned Home Assistant test dependency has a substantial dependency tree. Tests use simulated transport and must not connect to real serial devices. CI also runs HACS validation and Home Assistant hassfest. These checks do not replace hardware commissioning.

For issue reports, include the integration version, Home Assistant version, firmware version, and a minimal redacted error. Do not upload full Home Assistant backups, serial captures, site configurations, private device paths, credentials, or personal data.

## License and attribution

[MIT](LICENSE). The included generic blinds artwork is original project artwork, not a Domat or Home Assistant logo. Product names belong to their respective owners. This community project is not affiliated with or endorsed by Domat or Home Assistant.
