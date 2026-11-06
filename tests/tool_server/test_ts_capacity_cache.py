"""The capacity cache: warmed at /calls/start off the request, and an entry nobody reads is not
refreshed for ever (app/fsm/cache.py)."""

from app.fsm.cache import IDLE_SECONDS, SlotCache
from app.tools import warm_capacity


class Clock:
    def __init__(self):
        self.now = 0.0

    def __call__(self):
        return self.now


def test_an_entry_nobody_reads_stops_being_refreshed_and_is_dropped():
    clock = Clock()
    cache = SlotCache(60, clock=clock)
    fetched = []
    cache.get("old", lambda: fetched.append("old") or 1)
    clock.now = IDLE_SECONDS - 5
    cache.get("fresh", lambda: fetched.append("fresh") or 2)
    clock.now = IDLE_SECONDS + 10
    assert cache.refresh() == 1
    assert len(cache) == 1 and fetched == ["old", "fresh", "fresh"]


def test_warming_fills_the_cache_for_every_job_type(agent):
    services = agent.app.state.services
    tenant = next(iter(services.tenants))
    cache = services.caches[tenant.slug]
    assert len(cache) == 0
    warm_capacity(tenant, services.fsm[tenant.slug], cache, services.clock())
    assert len(cache) == len(tenant.job_types)


def test_a_booking_clears_the_cache_and_it_is_fetched_again_off_the_request():
    """Jackson: keep the open windows in memory, refreshed on any booking."""
    import threading

    cache = SlotCache(90, background=True)
    done = threading.Event()
    calls = []

    def fetch():
        calls.append(1)
        if len(calls) > 1:
            done.set()
        return len(calls)

    assert cache.get("no_heat", fetch) == 1
    cache.invalidate()
    assert done.wait(2), "fetched again in the background"
    assert cache.get("no_heat", lambda: 99) == 2


def test_with_the_cache_off_nothing_is_kept_or_warmed(agent):
    services = agent.app.state.services
    tenant = next(iter(services.tenants))
    off = SlotCache(0)
    assert warm_capacity(tenant, services.fsm[tenant.slug], off, services.clock()) is False
