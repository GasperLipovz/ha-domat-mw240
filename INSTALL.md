# Installation and commissioning

## Before you start

- Use **Home Assistant Core 2026.9.4 or newer**. The tested baseline is 2026.9.4 on Python 3.14; older versions are not supported. Newer Core releases still need compatibility checks.
- Back up Home Assistant and retain a recoverable copy of any previous custom integration before replacing it.
- Provide either a supported RS-485 serial adapter or a standard Modbus TCP-to-RTU gateway, with correct wiring/termination. Keep one physical RS-485 master: disconnect/disable the USB master before connecting a gateway. Do not share control with another integration, vendor tool, or TCP client.
- Obtain each module's physical address and serial settings from its commissioning records. Do not guess or scan the entire address range. Check wiring and address changes using the manufacturer's procedure, with qualified help where required.
- Serial defaults to **19200 baud, 8 data bits, no parity, 1 stop bit (8N1)**. These are connection defaults, not a claim about every module's factory or installed settings. Use a stable serial device path where available.
- For TCP, obtain the gateway's actual IP/hostname and a DHCP reservation or documented static address. Confirm that Home Assistant can reach its configured Modbus TCP port (default **502**). No automatic network discovery is performed; raw RTU-over-TCP is not supported.

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
2. Set a stable bus name and choose **Serial** or **TCP**. Only fields for the selected connection are shown:
   - **Serial** (default): dedicated serial device path, baud rate, parity, and stop bits matching the installed hardware. Data bits are 8.
   - **TCP**: actual gateway host, TCP port (default **502**), and request timeout. Configure the gateway's serial side separately; no HA TCP field changes its baud rate or framing. Complete the gateway checks below before enabling actions.
3. Enter the modules as one `address: name` per line. For example, these are fictitious commissioning examples, not discovered devices:

   ```text
   23: West window
   24: East window
   ```

   Addresses must be unique within that bus and in **1..247**. A 20-module bus needs 20 explicitly entered addresses; they do not have to be consecutive. Never use address 0.
4. Leave movement, timing-change, and EEPROM-save permissions **off**. Complete setup and confirm the expected module devices, firmware information, and available entities without operating a motor.
5. If communication fails, check the adapter or gateway, HA-to-gateway reachability, port/protocol, wiring, address list, and serial parameters. Do not increase permissions or repeatedly send movement commands to diagnose a read failure. A TCP connection alone does not prove that a module answers Modbus reads.
6. Under controlled conditions, enable movement permission in the integration's settings and test one module's direction and local stopping arrangements. Keep a local stop accessible. Only proceed to the remaining modules after checking the first one.

Use the integration's configuration/options controls to edit the bus settings or module list later. The bus name is immutable; keep module addresses stable to preserve identifiers. Changing an address here targets a different physical module; it does not assign an address to hardware. Changing a Home Assistant baud setting does not program the modules.

## Domat R035 gateway commissioning

The [official R035 datasheet (08/2025)](https://domat.blob.core.windows.net/cms/ContentItems/1555_r035/snKPeC/domat-r035-en.pdf) documents **Industrial Automation** mode: the R035 is a genuine Modbus TCP server and routes requests as the Modbus RTU master on RS-485. Its virtual-COM/RealPort mode is a different mode and is not used here.

1. Obtain the device's actual network address from its commissioning records or DHCP lease, reserve/document it, and secure access using the manufacturer's current guidance. Do not assume an example or factory address is reachable.
2. In the gateway web interface, open **Configuration → Serial Ports → Port 1** and select the **Industrial Automation** profile for Modbus TCP-to-RTU routing. Use the configured Modbus TCP server port, normally **502**. Do not select a raw serial/RTU-over-TCP tunnel.
3. Match the gateway's RS-485 settings to the installed modules. The datasheet assigns communication speed and data-bit selection to **DIP switches**; consult its table for your hardware, and verify the remaining framing settings. HA's TCP options do not configure these settings.
4. With equipment safe and motion automation paused, disconnect/disable the previous USB/serial master before wiring the R035 as the sole physical master. Verify RS-485 polarity, reference, termination, and power using the manufacturer instructions.
5. Confirm reachability from **Home Assistant's network**, then perform the integration's read-only firmware/status checks with all action permissions off. Start with one known module; do not scan addresses or use motor writes as a connectivity test.
6. Commission **both** the HA request timeout and the gateway's serial-response timeout for the actual baud rate, nodes, and failure cases. Allow for the gateway's downstream transaction and network overhead; an HA timeout shorter than the gateway's response window can abandon an in-flight request. Verify bounded failure/recovery rather than assuming default values are universally suitable.

The integration retains one client and serialized command queue per bus. A gateway may accept several TCP clients, but unrelated clients can interleave multi-step motor/configuration commands; do not share control. TCP does not speed up the downstream RTU bus or guarantee a particular 20-node polling rate. After a timeout or lost acknowledgement, inspect state before a deliberate new command: the module may already have acted. Stale commands and failed EEPROM saves are not replayed automatically.

**Evidence boundary:** prior physical verification covers one FW17 module. TCP loopback testing uses a simulated Modbus server, not an R035; 20-node tests are simulations. Physical R035 configuration, wiring, timeouts, network interruption/recovery, and supervised motor operation still require commissioning.

## Move an existing serial bus to TCP

Installing 0.4.0 does **not** switch existing entries to TCP. Existing entries and legacy YAML without a connection type remain serial.

1. Back up the configuration/registries and record the bus name, module IDs, entity IDs, connection settings, and current permissions. Pause motion automations and disable movement, timing changes, and EEPROM saving before the cutover; retain local stopping arrangements.
2. Commission the gateway as above, removing/disabling the old serial master before attaching it. Obtain the real host/port; no gateway address is supplied or discovered by the integration.
3. Open the **existing bus entry's options/configuration** in **Settings → Devices & services**. Choose **TCP** and enter the host, TCP port, and commissioned request timeout. Keep the immutable bus name and all module IDs unchanged, and complete the module-list step without altering those IDs. Do not delete/re-add the bus or create a second entry.
4. Save the options. The integration unloads/closes the old client before replacing it. Verify read-only module availability and that the existing devices, entity IDs, and automation references remain intact. Native devices and source helpers need no changes.
5. Only after read-only verification, deliberately restore the permissions you need and test one module under supervision. Updating transport never requires applying travel times, saving EEPROM, or arming actions on startup. If reads fail, leave actions disabled and diagnose; do not resend old motor commands.

To return to serial, use the same entry's options with its recorded serial settings. Disconnect/disable the gateway's RS-485 master before reconnecting the serial master. For a software downgrade to 0.3.0, first restore a compatible serial configuration or compatible backup; 0.3.0 does not understand TCP entries.

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
4. Install 0.4.0, leave the existing `domat_mw240:` YAML in place for the initial import, and run Home Assistant's configuration check. YAML without a connection type imports as serial. On Home Assistant OS/Supervised this can be done with `ha core check`, or use the supported configuration-check control for your installation. Fix errors before proceeding.
5. Restart Home Assistant Core. Confirm that the imported bus appears in **Settings → Devices & services**, expected modules are attached to it, and existing entity identifiers and automations still refer to the intended devices. Check the permission settings before enabling actions.
6. Only after successful import, remove the legacy `domat_mw240:` block (or its dedicated include) while preserving unrelated configuration. Run the configuration check again, then restart Core and verify that the UI entry still loads on its own.

An existing UI entry takes precedence over later YAML imports. Leaving or changing the YAML is not a supported way to overwrite an already configured UI bus. Edit the UI entry instead. If identifiers do not match, stop and restore or investigate the backup; do not delete entity registries or repeatedly re-add entries blindly.

## Updates, rollback, and removal

- Back up first, download the new integration version in HACS, and **restart Core**. Verify loaded version, module availability, and permission settings. Updates do not recommission motors, rewrite timing, or require EEPROM saving.
- For rollback, restore the prior component version and compatible configuration/backup, check the configuration, then restart Core. Keep unrelated later changes intact.
- Remove the bus entry through **Devices & services** before uninstalling through HACS. Remove any legacy `domat_mw240:` YAML, check the configuration if edited, then restart Core. Removing software does not reset the modules' RAM or EEPROM.

## Maintainer publication checklist

The public repository must contain exactly one integration under `custom_components/domat_mw240/`, its runtime files and translations, the local `brand/icon.png`, and the root documentation, license, and `hacs.json`. Include only sanitized offline tests. Exclude site files and captures.

Before announcing a release, set the GitHub description (for example, “Local Modbus RTU/TCP blinds integration for Domat MW240-B and Home Assistant”), topics such as `home-assistant`, `hacs`, `domat`, `modbus`, and `blinds`, and enable issues. Confirm the manifest version matches the release, and require passing unit tests, hassfest, and HACS validation on GitHub. HACS validation needs actual public repository metadata; local file checks alone cannot prove it passes. Report actual test results without treating loopback/simulation as physical R035 commissioning.

These conventions follow [HACS general requirements](https://www.hacs.xyz/docs/publish/start/), [integration requirements](https://www.hacs.xyz/docs/publish/integration/), and the [HACS validation action](https://www.hacs.xyz/docs/publish/action/).
