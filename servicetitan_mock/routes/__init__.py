from fastapi import APIRouter, Depends

from ..deps import authorize


def tenant_router(module: str) -> APIRouter:
    """A router under /{module}/v2/tenant/{tenant_id}, the path pattern ServiceTitan uses."""
    return APIRouter(prefix=f"/{module}/v2/tenant/{{tenant_id}}", dependencies=[Depends(authorize)])
