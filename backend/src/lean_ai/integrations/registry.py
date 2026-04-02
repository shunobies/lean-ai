"""Integration registry — discover, configure, and manage integrations.

Integrations register via the @register_integration decorator.
The registry manages lifecycle (init, health checks) and provides
a single access point for active integrations.
"""

from __future__ import annotations

import logging

from lean_ai.integrations.base import IntegrationProvider

logger = logging.getLogger(__name__)

# Available integration classes (populated by @register_integration)
_REGISTRY: dict[str, type[IntegrationProvider]] = {}

# Active (configured and initialized) integrations
_ACTIVE: dict[str, IntegrationProvider] = {}


def register_integration(cls: type[IntegrationProvider]) -> type[IntegrationProvider]:
    """Class decorator to register an integration provider."""
    name = getattr(cls, "INTEGRATION_NAME", None)
    if not name:
        raise ValueError(
            f"Integration class {cls.__name__} must define INTEGRATION_NAME class attribute"
        )
    _REGISTRY[name] = cls
    logger.debug("Registered integration: %s (%s)", name, cls.__name__)
    return cls


def get_integration(name: str) -> IntegrationProvider | None:
    """Get an active integration by name."""
    return _ACTIVE.get(name)


def list_integrations() -> list[dict]:
    """List all registered integrations with their status."""
    result = []
    for name, cls in _REGISTRY.items():
        display = getattr(cls, "DISPLAY_NAME", name)
        result.append({
            "name": name,
            "display_name": display,
            "active": name in _ACTIVE,
        })
    return result


def list_active_integrations() -> list[IntegrationProvider]:
    """Return all active (configured) integrations."""
    return list(_ACTIVE.values())


async def init_integration(name: str, **kwargs) -> IntegrationProvider | None:
    """Initialize and activate an integration. Returns None on failure."""
    cls = _REGISTRY.get(name)
    if not cls:
        logger.warning("Unknown integration: %s", name)
        return None
    try:
        provider = cls(**kwargs)
        if await provider.check_health():
            _ACTIVE[name] = provider
            logger.info("Integration activated: %s", name)
            return provider
        else:
            logger.warning("Integration health check failed: %s", name)
            return None
    except Exception as e:
        logger.warning("Failed to init integration %s: %s", name, e)
        return None


def deactivate_integration(name: str) -> bool:
    """Deactivate a running integration. Returns True if it was active."""
    return _ACTIVE.pop(name, None) is not None


async def shutdown_integrations() -> None:
    """Clean up all active integrations."""
    _ACTIVE.clear()
