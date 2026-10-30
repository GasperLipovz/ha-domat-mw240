"""Native Domat MW240-B bus entries with read-only legacy YAML import."""
from __future__ import annotations

from .config_schema import BUS_SCHEMA, CONFIG_SCHEMA, DOMAIN, connection_key, modbus_connection_key


async def async_setup(hass, config):
    """Import legacy YAML; config entries alone own serial workers."""
    from homeassistant.config_entries import SOURCE_IMPORT

    state = hass.data.setdefault(DOMAIN, {"ports": {}, "modbus_ports": set()})
    state["modbus_ports"] = await hass.async_add_executor_job(
        lambda: {endpoint for hub in config.get("modbus", [])
                 if (endpoint := modbus_connection_key(hub, True)) is not None}
    )
    for bus_config in config.get(DOMAIN, []):
        hass.async_create_task(hass.config_entries.flow.async_init(
            DOMAIN, context={"source": SOURCE_IMPORT}, data=bus_config
        ))
    return True


async def async_migrate_entry(hass, entry):
    """Version 0.3 entries retain serial transport and every existing option."""
    if entry.version != 1:
        return False
    if entry.minor_version < 2:
        hass.config_entries.async_update_entry(
            entry, data={"connection_type": "serial", **entry.data}, minor_version=2,
        )
    return True


async def async_setup_entry(hass, entry):
    """Create one bus owner and register a separate physical device per module."""
    from homeassistant.const import EVENT_HOMEASSISTANT_STOP, Platform
    from homeassistant.exceptions import ConfigEntryError
    from homeassistant.helpers import device_registry as dr, entity_registry as er
    from .runtime import Hub

    bus_config = BUS_SCHEMA({**entry.data, **entry.options})
    port = await hass.async_add_executor_job(connection_key, bus_config, True)
    state = hass.data.setdefault(DOMAIN, {"ports": {}, "modbus_ports": set()})
    if port in state["modbus_ports"] or state["ports"].get(port, entry.entry_id) != entry.entry_id:
        raise ConfigEntryError("Connection endpoint already has a Modbus owner; duplicate bus refused")
    state["ports"][port] = entry.entry_id
    hub = None
    try:
        hub = Hub(hass, bus_config, config_entry=entry)
        entry.runtime_data = hub
        hub.bus.start()
        await hub.async_refresh()
        registry = dr.async_get(hass)
        bus_device = registry.async_get_or_create(
            config_entry_id=entry.entry_id,
            identifiers={(DOMAIN, f"{bus_config['name']}_bus")},
            name=bus_config["name"], manufacturer="Domat", model="Modbus RTU bus",
            entry_type=dr.DeviceEntryType.SERVICE,
        )
        hub.bus_device_id = bus_device.id
        for device in bus_config["devices"]:
            snapshot = hub.records[device["id"]].snapshot
            registry.async_get_or_create(
                config_entry_id=entry.entry_id,
                identifiers={(DOMAIN, f"{bus_config['name']}_{device['id']}")},
                manufacturer="Domat", model="MW240-B", name=device["name"],
                sw_version=str(snapshot.firmware) if snapshot else None,
                via_device_id=bus_device.id,
            )
        # Adopt only this bus's existing YAML entities. Never rename IDs, aliases,
        # areas, disabled flags or user names, and never steal another entry.
        entity_registry = er.async_get(hass)
        prefixes = tuple(f"{DOMAIN}_{bus_config['name']}_{device['id']}_"
                         for device in bus_config["devices"])
        for old in list(entity_registry.entities.values()):
            if old.platform == DOMAIN and old.config_entry_id is None and old.unique_id.startswith(prefixes):
                entity_registry.async_update_entity(old.entity_id, config_entry_id=entry.entry_id)
        await hass.config_entries.async_forward_entry_setups(entry, [
            Platform.COVER, Platform.BUTTON, Platform.NUMBER, Platform.SENSOR, Platform.BINARY_SENSOR,
        ])
    except BaseException:
        if hub:
            await hub.async_close()
        if state["ports"].get(port) == entry.entry_id:
            state["ports"].pop(port)
        raise

    async def stop_bus(event):
        await hub.async_close()

    entry.async_on_unload(hass.bus.async_listen_once(EVENT_HOMEASSISTANT_STOP, stop_bus))
    entry.async_on_unload(entry.add_update_listener(_async_options_updated))
    return True


async def _async_options_updated(hass, entry):
    """Close the old owner before a UI settings change opens its replacement."""
    await hass.config_entries.async_reload(entry.entry_id)


async def async_unload_entry(hass, entry):
    """Discard pending work and close this bus, without transmitting a motor action."""
    from homeassistant.const import Platform

    platforms = [Platform.COVER, Platform.BUTTON, Platform.NUMBER, Platform.SENSOR, Platform.BINARY_SENSOR]
    if not await hass.config_entries.async_unload_platforms(entry, platforms):
        return False
    await entry.runtime_data.async_close()
    state = hass.data.get(DOMAIN, {})
    for port, owner in list(state.get("ports", {}).items()):
        if owner == entry.entry_id:
            state["ports"].pop(port)
    return True
