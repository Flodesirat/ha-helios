"""Minimal HomeAssistant mock for simulation dispatch.

Provides only what DeviceManager.async_dispatch needs:
  - hass.states.get(entity_id) → object with .state attribute
  - hass.services.async_call(domain, service, data, blocking) → no-op
    (DeviceManager._async_set_switch sets device.is_on directly)
"""
from __future__ import annotations


class _SimState:
    __slots__ = ("state",)

    def __init__(self, state: str) -> None:
        self.state = state


class _SimStates:
    def __init__(self, state_dict: dict[str, str]) -> None:
        self._d = state_dict

    def get(self, entity_id: str) -> "_SimState | None":
        v = self._d.get(entity_id)
        return _SimState(v) if v is not None else None


class _SimServices:
    """No-op service bus — device state is managed by DeviceManager directly."""

    async def async_call(
        self,
        domain: str,
        service: str,
        data: dict | None = None,
        blocking: bool = False,
    ) -> None:
        pass


class SimHass:
    """Minimal hass mock exposing what DeviceManager.async_dispatch needs."""

    def __init__(self, state_dict: dict[str, str]) -> None:
        self.states = _SimStates(state_dict)
        self.services = _SimServices()
