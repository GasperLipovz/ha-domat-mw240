# Installation and commissioning

## Before you start

- Use **Home Assistant Core 2026.9.4 or newer**. The tested baseline is 2026.9.4 on Python 3.14; older versions are not supported. Newer Core releases still need compatibility checks.
- Back up Home Assistant and retain a recoverable copy of any previous custom integration before replacing it.
- Provide a supported RS-485 adapter, correct wiring/termination, and exclusive access from Home Assistant. Use a stable serial device path where available. Do not run another Modbus master on the same bus.
- Obtain each module's physical address and serial settings from its commissioning records. Do not guess or scan the entire address range. Check wiring and address changes using the manufacturer's procedure, with qualified help where required.
- The integration defaults to **19200 baud, 8 data bits, no parity, 1 stop bit (8N1)**. These are connection defaults, not a claim about every module's factory or installed settings.

## HACS installation (custom repository)

1. Install and configure [HACS](https://www.hacs.xyz/docs/use/) if it is not already available.
2. Open HACS, open its menu, and choose **Custom repositories**.
3. Add `https://github.com/GasperLipovz/ha-domat-mw240` with type **Integration**.
4. Find **Domat MW240-B Blinds** in HACS and download it.
5. **Restart Home Assistant Core**. Refresh the browser if the integration is not immediately visible.
6. Continue with **New setup** below.

Custom repository installation requires the repository to be public on GitHub. It does not mean inclusion in HACS's default catalog.

### Manual alternative

Download the intended release from this repository. Copy only `custom_components/domat_mw240/` into your Home Assistant configuration directory, producing:

```text
custom_components/
  domat_mw240/
    __init__.py
    manifest.json
    config_flow.py
    brand/
      icon.png
    ...
```

Do not copy the entire repository into `custom_components`, and do not create a nested `domat_mw240/domat_mw240` directory. Keep a backup of an existing version, run Home Assistant's configuration check, and restart Core before setup.

## New setup — no configuration-file editing

1. Go to **Settings → Devices & services → Add integration** and select **Domat MW240-B Blinds**.
2. Set a stable bus name, the dedicated serial device path, baud rate, parity, and stop bits to match the installed hardware. Data bits are 8.
3. Enter the modules as one `address: name` per line. For example, these are fictitious commissioning examples, not discovered devices:

   ```text
   23: West window
   24: East window
   ```

   Addresses must be unique within that bus and in **1..247**. A 20-module bus needs 20 explicitly entered addresses; they do not have to be consecutive. Never use address 0.
4. Leave movement, timing-change, and EEPROM-save permissions **off**. Complete setup and confirm the expected module devices, firmware information, and available entities without operating a motor.
5. If communication fails, check the dedicated adapter, permissions, wiring, address list, and matching serial parameters. Do not increase permissions or repeatedly send movement commands to diagnose a read failure.
6. Under controlled conditions, enable movement permission in the integration's settings and test one module's direction and local stopping arrangements. Keep a local stop accessible. Only proceed to the remaining modules after checking the first one.

Use the integration's configuration/options controls to edit the bus settings or module list later. Keep the bus name and addresses stable to preserve identifiers. Changing an address here targets a different physical module; it does not assign an address to hardware. Changing a Home Assistant baud setting does not program the modules.

## Travel-time changes and persistence

1. Record the module's current working timing before changing anything. An integration update is not a reason to replace it with an example or assumed default.
2. Enable **timing changes** separately from movement permission. Edit the pending travel-time number(s). A pending number is only a Home Assistant draft, not a bus write.
3. Press **Apply travel times (temporary)** for the selected module. This explicitly applies the pending value to working RAM. Read back the applied value; do not assume editing the number applied it.
4. Test under supervision if movement has separately been enabled. RAM changes are lost when the module loses power unless explicitly persisted. Pending drafts are not a durable backup and are not automatically applied on startup.
5. On physically verified **FW17 only**, enable the separate EEPROM-save permission only if persistence is intentional. Press **Prepare EEPROM save**: this only reads and captures the known configuration. Review the `eeprom_configuration_words` attributes, then press **Save settings to EEPROM** within **60 seconds**. This consumes the preparation once, even if a reply is lost. Pending travel-time drafts must be applied or discarded first. **Saving commits ALL current RAM configuration, including unrelated settings.** Do not automate repeated saves; leave this permission off during normal operation.

The save action is limited to idle FW17 modules with normal status and no configured startup action. Changed configuration, active local inputs/outputs or override flags reject the save rather than silently stopping the module. A failed save is not retried. If safe disarming cannot be confirmed, configuration writes are blocked for that module for the current bus-controller session; inspect status and recover deliberately before reloading/restarting. A later normal poll is not a recovery procedure. A successful action confirms the sequence/status/readback, **not a new physical power-cycle test**. Power interruption remains the deliberate commissioning proof of persistence.

FW17 has one common travel time and step-based tilt controls; do not treat tilt arrows as angle percentages. FW21+ supports separate up/down travel times, but the integration does not permit EEPROM saving on those unverified firmware versions. No reset, initialization, automatic EEPROM save, or startup configuration write is required for normal operation.

## Migrate an existing YAML installation

Migration should preserve entities, not create a second master on the adapter.

1. Back up Home Assistant, including its configuration and registries, and retain the previous component version. Save the existing `domat_mw240:` YAML privately. Never publish this backup or site configuration.
2. Record the current bus **name**, module **IDs**, and entity identifiers. Keep the bus name and IDs unchanged through import: they form the stable identity. Module display names can be edited later.
3. Stop any competing process or old integration that owns the same adapter. Do not configure a second UI bus entry on the port before importing the existing YAML.
4. Install 0.3.0, leave the existing `domat_mw240:` YAML in place for the initial import, and run Home Assistant's configuration check. On Home Assistant OS/Supervised this can be done with `ha core check`, or use the supported configuration-check control for your installation. Fix errors before proceeding.
5. Restart Home Assistant Core. Confirm that the imported bus appears in **Settings → Devices & services**, expected modules are attached to it, and existing entity identifiers and automations still refer to the intended devices. Check the permission settings before enabling actions.
6. Only after successful import, remove the legacy `domat_mw240:` block (or its dedicated include) while preserving unrelated configuration. Run the configuration check again, then restart Core and verify that the UI entry still loads on its own.

An existing UI entry takes precedence over later YAML imports. Leaving or changing the YAML is not a supported way to overwrite an already configured UI bus. Edit the UI entry instead. If identifiers do not match, stop and restore or investigate the backup; do not delete entity registries or repeatedly re-add entries blindly.

## Updates, rollback, and removal

- Back up first, download the new integration version in HACS, and **restart Core**. Verify loaded version, module availability, and permission settings. Updates do not recommission motors, rewrite timing, or require EEPROM saving.
- For rollback, restore the prior component version and compatible configuration/backup, check the configuration, then restart Core. Keep unrelated later changes intact.
- Remove the bus entry through **Devices & services** before uninstalling through HACS. Remove any legacy `domat_mw240:` YAML, check the configuration if edited, then restart Core. Removing software does not reset the modules' RAM or EEPROM.

## Maintainer publication checklist

The public repository must contain exactly one integration under `custom_components/domat_mw240/`, its runtime files and translations, the local `brand/icon.png`, and the root documentation, license, and `hacs.json`. Include only sanitized offline tests. Exclude site files and captures.

Before announcing a release, set the GitHub description (for example, “Local Modbus RTU blinds integration for Domat MW240-B and Home Assistant”), topics such as `home-assistant`, `hacs`, `domat`, `modbus`, and `blinds`, and enable issues. Confirm the manifest version matches the release, and require passing unit tests, hassfest, and HACS validation on GitHub. HACS validation needs actual public repository metadata; local file checks alone cannot prove it passes.

These conventions follow [HACS general requirements](https://www.hacs.xyz/docs/publish/start/), [integration requirements](https://www.hacs.xyz/docs/publish/integration/), and the [HACS validation action](https://www.hacs.xyz/docs/publish/action/).
