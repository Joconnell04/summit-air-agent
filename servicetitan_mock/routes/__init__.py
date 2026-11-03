from fastapi import APIRouter, Depends

from ..deps import authorize


def tenant_router(module: str, version: str = "v2") -> APIRouter:
    """A router under /{module}/{version}/tenant/{tenant_id}, the path pattern ServiceTitan uses.
    Every module is v2 except the telecom call feed, which is v3."""
    return APIRouter(
        prefix=f"/{module}/{version}/tenant/{{tenant_id}}", dependencies=[Depends(authorize)]
    )
