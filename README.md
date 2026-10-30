# Domat MW240-B Blinds

<a href="https://www.domat-int.com/en/product/mw240-b"><img src="https://domat.blob.core.windows.net/imagehandler/domat.blob.core.windows.net/cms/ContentItems/1546_mw240-b/images/emN8kF/m_max__w_1005__h_754__o/MW240-B.jpeg" alt="Domat MW240-B blinds control module" width="320"></a>

*Product photograph: [Domat Control System](https://www.domat-int.com/en/product/mw240-b). Linked from the manufacturer's website; not covered by this project's MIT license.*

A community Home Assistant custom integration for Domat MW240-B blinds on a local RS-485 bus. Connect through a dedicated **Modbus RTU serial adapter** or a **standard Modbus TCP-to-RTU gateway**, such as the Domat R035 in Industrial Automation mode. No cloud service, Node-RED flow, MQTT bridge, RealPort driver, or virtual COM port is required.

**Version 0.4.0 · Home Assistant 2026.9.4 or newer · Python 3.14**

[Installation and migration](INSTALL.md) · [Changelog](CHANGELOG.md) · [Report an issue](https://github.com/GasperLipovz/ha-domat-mw240/issues)

## Features

- Configure a bus in **Settings → Devices & services**. Choose **Serial** (the default) or **TCP**; only the selected connection's fields are shown. TCP uses a host, TCP port (default **502**), and configurable request timeout. Enter an explicit list of module addresses and names; no YAML is needed for a new installation.
- Existing serial configurations remain serial. Switch an existing bus to TCP through its options without recreating it: keep the immutable bus name and module addresses to preserve devices and entities.
- One configuration entry, client, and serialized command queue per bus, with one Home Assistant device per module. A bus can contain 20 individually addressed modules; addresses must be unique integers from **1 to 247**. Address 0 / broadcast is never allowed.
- Native cover controls: **UP, DOWN, STOP**, and timed height estimates.
- Single-step tilt-up and tilt-down buttons for FW17. These are physical steps, **not an absolute slat-angle percentage**.
- Pending travel-time numbers and an explicit **Apply travel times (temporary)** action. Editing a pending number does not write to the module. Applying writes working RAM only.
- An explicit, separately permission-gated EEPROM save on **FW17 only**. Saving commits **all current RAM configuration**, not just travel time.
- Legacy YAML import preserves identifiers based on the stable bus name and module address. Existing UI configuration takes precedence over subsequent YAML imports.

## Safety and limitations

**This integration controls moving equipment.** Keep people and objects clear, retain the manufacturer's motor limits and protection, and commission one module under supervision before enabling automations. Home Assistant and network/serial communication are not safety systems.

- Movement commands, timing changes, and EEPROM saving are **separate permissions, all disabled by default**. Enable only what you need after read-only checks. Disabling movement commands also prevents sending STOP through this integration; keep a local stop available.
- Startup and polling do not apply settings, save EEPROM, reset or initialize modules, or replay old actions. Expired/offline commands are not queued for later replay. A lost acknowledgement does not prove that a write failed; inspect status before any deliberate new action rather than resending blindly.
- Height is an estimate based on travel time, not measured position. External/local controls, interrupted travel, power loss, or communication failures can make it inaccurate. Do not use it for safety decisions.
- FW17 has one shared travel time. FW21 and later have separate up/down travel-time support, but EEPROM saving remains blocked on those untested firmware versions. Do not infer full firmware compatibility from a readable version register.
- Hardware verification covers **one physical FW17 module**. The 20-module case uses offline simulations; TCP loopback tests use a simulated Modbus server, **not a physical R035**. Gateway and full-bus hardware commissioning remain required.
- The integration does not program physical module addresses or serial settings. Serial-mode fields configure the adapter; TCP-mode fields configure the network connection, not the gateway's RS-485 parameters.
- Keep **one physical RS-485 master**. Before attaching a gateway, disconnect/disable the USB master. Do not share control with Home Assistant's built-in Modbus integration, vendor tools, Node-RED, or another client: a gateway accepting multiple TCP clients does not make multi-step motor commands safe to interleave.
- TCP does not increase the downstream RTU bus throughput. Commission polling and both the HA request timeout and gateway serial-response timeout for the actual bus; there is no universal timeout that proves safe operation for 20 nodes.
- Only standard Modbus TCP is supported over the network; raw RTU-over-TCP is not supported. No automatic network scan, full-address scan, or baud/address probing is performed.

## Install and configure

Follow [INSTALL.md](INSTALL.md), including the R035 commissioning and existing-bus migration steps. The intended HACS route is a **custom repository**, not a claim that the integration is in HACS's default store. Installation and every integration update require a **Home Assistant Core restart**. Updating the integration does not recommission motors or require rewriting their travel times. Existing native devices and source helpers are unaffected by changing this bus's transport.

## Development

Run offline tests from the repository root using Python 3.14:

```sh
python3.14 -m venv .venv
.venv/bin/python -m pip install -r requirements_test.txt
.venv/bin/python -m unittest discover -s tests -v
```

The pinned Home Assistant test dependency has a substantial dependency tree. Tests use simulated transport and local loopback Modbus servers, not real serial devices or a physical R035. CI also runs HACS validation and Home Assistant hassfest. These checks do not replace hardware commissioning.

For issue reports, include the integration version, Home Assistant version, firmware version, connection type, and a minimal redacted error. Do not upload full Home Assistant backups, captures, site configurations, private device paths or network addresses, credentials, or personal data.

## License and attribution

[MIT](LICENSE). The included generic blinds artwork is original project artwork, not a Domat or Home Assistant logo. Product names belong to their respective owners. This community project is not affiliated with or endorsed by Domat or Home Assistant.
