"""Live market data: FX and metal feeds, served from recorded responses."""

import threading
import time

import pytest

from app.services import catalog


def estimate_box(client, location="IN-CHENNAI"):
    import trimesh

    from tests.conftest import mesh_bytes

    r = client.post(
        "/api/estimate",
        files={"file": ("box.stl", mesh_bytes(trimesh.creation.box(extents=[100, 60, 40])))},
        data={"location_id": location, "material_id": "AL6061", "quantity": "100"},
    )
    assert r.status_code == 200, r.text
    return r.json()


# ---------- FX ----------

def test_fx_off_uses_stored_value_and_says_so(client):
    loc = catalog.get_location("IN-CHENNAI")
    assert (loc.fx_per_usd, loc.fx_status) == (96.8, "fallback")
    e = estimate_box(client)
    assert any("fallback" in a and "INR/USD" in a for a in e["assumptions"])


def test_fx_live_from_frankfurter(client, live_feeds):
    live_feeds.fx_feed.refresh()
    loc = catalog.get_location("IN-CHENNAI")
    assert (loc.fx_per_usd, loc.fx_status, loc.fx_as_of) == (96.65, "live", "2026-10-11")
    assert "Frankfurter" in loc.fx_source
    for cur in ("CNY", "EUR", "MXN", "PLN", "VND"):
        assert live_feeds.fx(cur) is not None, cur
    usd = catalog.get_location("US-MICHIGAN")
    assert (usd.fx_per_usd, usd.fx_status) == (1.0, "fixed")
    rate = client.get("/api/rates/labour", params={"location_id": "IN-CHENNAI", "skill": "operator"}).json()
    assert rate["rate_local_per_hour"] == pytest.approx(3.5 * 96.65, abs=0.01)
    assert not any("INR/USD" in a for a in estimate_box(client)["assumptions"])


def test_fx_stale_value_is_served_as_cached(live_feeds):
    live_feeds.fx_feed.refresh()
    live_feeds.fx_feed.fetched_at -= live_feeds.FX_TTL_S + 1
    live_feeds._http_get = None  # the background refresh will fail; old data must stay
    assert catalog.get_location("IN-CHENNAI").fx_status == "cached"
    assert catalog.get_location("IN-CHENNAI").fx_per_usd == 96.65


def test_failed_fetch_falls_back(client, live_feeds, monkeypatch):
    def down(url):
        raise TimeoutError("timed out")

    monkeypatch.setattr(live_feeds, "_http_get", down)
    live_feeds.fx_feed.refresh()
    assert live_feeds.fx_feed.error.startswith("TimeoutError")
    assert catalog.get_location("IN-CHENNAI").fx_status == "fallback"
    estimate_box(client)  # still prices


def test_slow_fetch_never_delays_a_lookup(live_feeds, monkeypatch):
    release = threading.Event()

    def slow(url):
        release.wait(5)
        raise TimeoutError

    monkeypatch.setattr(live_feeds, "_http_get", slow)
    t0 = time.perf_counter()
    loc = catalog.get_location("IN-CHENNAI")
    assert time.perf_counter() - t0 < 0.5
    assert loc.fx_status == "fallback"
    release.set()
