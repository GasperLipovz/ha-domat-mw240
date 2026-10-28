# Changelog

## 0.3.0

- Add native Home Assistant UI configuration and options: one entry per bus, with a device for each explicitly configured module.
- Support legacy YAML import while retaining stable bus-name/module-address identifiers; existing UI configuration wins over later YAML imports.
- Expose serial path, baud rate, parity, stop bits, and multiline module address/name configuration in the UI. These settings do not program physical addresses or serial parameters.
- Retain UP/DOWN/STOP controls, timed height estimates, FW17 step-based tilt, and pending travel-time drafts with explicit temporary application to working RAM.
- Add a separately permission-gated, two-action EEPROM preparation/save for FW17 only, with a 60-second one-shot confirmation and configuration-change checks. It saves all current RAM configuration, not only the travel time. FW21+ separate travel timings remain supported without permitting unverified EEPROM saves.
- Keep movement, timing-change, and EEPROM-save permissions off by default. No automatic startup writes, EEPROM saves, resets, initialization, or stale action replay.
- Add HACS custom-repository packaging, an original generic blinds icon, installation/migration guidance, and offline CI on Home Assistant 2026.9.4 / Python 3.14.

Hardware verification is limited to one physical FW17 module. Twenty-module testing uses offline simulations; it is not a physical multi-module commissioning claim.
