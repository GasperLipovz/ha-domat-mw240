# Changelog

## 0.4.0

- Add standard Modbus TCP alongside Modbus RTU serial. Serial remains the default for existing configurations; TCP uses a host, TCP port (default 502), and configurable request timeout. UI forms show only the active connection's fields.
- Allow transport changes through an existing bus's options, preserving the immutable bus name, module IDs, devices, and entities. Close the old client before opening its replacement; retain one client and serialized command queue per bus.
- Document R035 Industrial Automation setup, single-master wiring, timeout commissioning, and serial-to-TCP migration. No RealPort/virtual COM dependency, raw RTU-over-TCP support, automatic network discovery, or downstream RTU throughput increase.
- Preserve movement/timing/EEPROM permissions, FW17-only two-action EEPROM protection, no startup writes, and no stale-command replay. Existing native devices and source helpers are unchanged.
- Retain Home Assistant 2026.9.4 / Python 3.14 as the baseline and runtime requirement `pymodbus[serial]>=3.13.1`, which includes `pyserial>=3.5` through the serial extra.

TCP loopback tests use a simulated Modbus server, not a physical R035. Physical gateway commissioning and a 20-module hardware bus remain unverified; prior hardware evidence is limited to one FW17 module. See the commissioning checklist before enabling actions.

## 0.3.0

- Add native Home Assistant UI configuration and options: one entry per bus, with a device for each explicitly configured module.
- Support legacy YAML import while retaining stable bus-name/module-address identifiers; existing UI configuration wins over later YAML imports.
- Expose serial path, baud rate, parity, stop bits, and multiline module address/name configuration in the UI. These settings do not program physical addresses or serial parameters.
- Retain UP/DOWN/STOP controls, timed height estimates, FW17 step-based tilt, and pending travel-time drafts with explicit temporary application to working RAM.
- Add a separately permission-gated, two-action EEPROM preparation/save for FW17 only, with a 60-second one-shot confirmation and configuration-change checks. It saves all current RAM configuration, not only the travel time. FW21+ separate travel timings remain supported without permitting unverified EEPROM saves.
- Keep movement, timing-change, and EEPROM-save permissions off by default. No automatic startup writes, EEPROM saves, resets, initialization, or stale action replay.
- Add HACS custom-repository packaging, an original generic blinds icon, installation/migration guidance, and offline CI on Home Assistant 2026.9.4 / Python 3.14.

Hardware verification is limited to one physical FW17 module. Twenty-module testing uses offline simulations; it is not a physical multi-module commissioning claim.
