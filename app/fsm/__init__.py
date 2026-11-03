"""FSM adapters. The tools talk to the field service system only through make_adapter's result."""

import os

import httpx

from ..settings import Settings
from ..tenant import Tenant
from .base import FsmAdapter, FsmRejected, FsmUnavailable
from .cache import SlotCache
from .servicetitan import ServiceTitanAdapter

__all__ = ["FsmAdapter", "FsmRejected", "FsmUnavailable", "SlotCache", "make_adapter"]


def make_adapter(
    tenant: Tenant, settings: Settings, http: httpx.Client | None = None
) -> FsmAdapter:
    """Credentials come from the environment; the mock's own values in config.yaml are the
    fallback, since they are not secrets."""
    if tenant.fsm["adapter"] != "servicetitan":
        raise ValueError(f"no adapter called {tenant.fsm['adapter']!r}")
    base_url = settings.fsm_base_url or tenant.fsm["base_url"]
    http = http or httpx.Client(base_url=base_url, timeout=settings.fsm_timeout_seconds)
    auth = tenant.mock_auth
    return ServiceTitanAdapter(
        http,
        tenant.fsm_tenant_id,
        os.environ.get("FSM_CLIENT_ID", auth.get("client_id", "")),
        os.environ.get("FSM_CLIENT_SECRET", auth.get("client_secret", "")),
        os.environ.get("FSM_APP_KEY", auth.get("app_key", "")),
        token_url=os.environ.get("FSM_TOKEN_URL", "/connect/token"),
    )
