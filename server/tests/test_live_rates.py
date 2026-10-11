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


# ---------- Metal prices ----------

STORED = {m.id: m.price_usd_per_kg for m in catalog.materials().values()}


def usd_per_kg(material_id):
    """Material price before the location factor (US-MICHIGAN factor is 1.1)."""
    r = catalog.material_rate(material_id, "US-MICHIGAN")
    return r.price_usd_per_kg / 1.1, r


def push_observation(live, series, factor, month="2026-09-01"):
    history = live.fred_feed.data[series]
    history.append((month, history[-1][1] * factor))


def test_materials_off_fall_back_or_stay_manual():
    assert usd_per_kg("AL6061")[1].status == "fallback"
    assert usd_per_kg("SS304")[1].status == "fallback"
    _, ti = usd_per_kg("TI64")
    assert (ti.status, ti.source.split(";")[0]) == ("manual", "manual")


def test_day_one_prices_unchanged_and_labelled(live_feeds):
    live_feeds.fred_feed.refresh()
    for mid, status in [("AL6061", "live"), ("AL7075", "live"), ("BRASS360", "live"),
                        ("MS1018", "indexed"), ("AISI4140", "indexed"),
                        ("SS304", "indexed"), ("SS316", "indexed"), ("PA6", "manual")]:
        price, rate = usd_per_kg(mid)
        assert price == pytest.approx(STORED[mid], rel=1e-5), mid
        assert rate.status == status, mid
    _, al = usd_per_kg("AL6061")
    assert "FRED" in al.source and al.as_of == "2026-07"
    _, ms = usd_per_kg("MS1018")
    assert "not a price" in ms.source and "tariffs" in ms.source


def test_commodity_price_follows_the_metal(live_feeds):
    live_feeds.fred_feed.refresh()
    push_observation(live_feeds, "PALUMUSDM", 1.10)
    price, rate = usd_per_kg("AL6061")
    assert price == pytest.approx(4.2 * 1.10, rel=1e-5)
    assert rate.as_of == "2026-09"


def test_stainless_blends_steel_index_with_nickel(live_feeds):
    live_feeds.fred_feed.refresh()
    push_observation(live_feeds, "PNICKUSDM", 1.20)
    assert usd_per_kg("SS304")[0] == pytest.approx(4.0 * (0.70 + 0.30 * 1.20), rel=1e-4)
    assert usd_per_kg("MS1018")[0] == pytest.approx(1.2, rel=1e-5)  # steel index unchanged


def test_index_ratio_is_clamped_and_reported(client, live_feeds):
    live_feeds.fred_feed.refresh()
    live_feeds.fred_feed.data["WPU101"].append(("2026-09-01", 378.128 * 2))
    price, rate = usd_per_kg("MS1018")
    assert price == pytest.approx(1.2 * 1.3, rel=1e-6)
    assert "clamped" in rate.assumption
    from tests.conftest import mesh_bytes
    import trimesh

    r = client.post(
        "/api/estimate",
        files={"file": ("box.stl", mesh_bytes(trimesh.creation.box(extents=[100, 60, 40])))},
        data={"location_id": "IN-CHENNAI", "material_id": "MS1018", "quantity": "10"},
    )
    assert any("clamped" in a for a in r.json()["assumptions"])


def test_stainless_without_nickel_falls_back(live_feeds):
    live_feeds.fred_feed.refresh()
    del live_feeds.fred_feed.data["PNICKUSDM"]
    price, rate = usd_per_kg("SS316")
    assert (price, rate.status) == (pytest.approx(5.5), "fallback")


def test_failed_fred_fetch_backs_off(live_feeds, monkeypatch):
    def down(url):
        raise OSError("unreachable")

    monkeypatch.setattr(live_feeds, "_http_get", down)
    live_feeds.fred_feed.refresh()
    assert live_feeds.fred_feed.error
    started = []
    monkeypatch.setattr(live_feeds.threading, "Thread", lambda **kw: started.append(kw))
    live_feeds.fred_feed.ensure_fresh()
    assert started == []  # within the retry window: no new fetch


# ---------- Labour: base wage x burden ----------

def test_labour_rate_is_base_wage_times_burden(client, tmp_path, monkeypatch):
    rate = client.get("/api/rates/labour", params={"location_id": "IN-CHENNAI", "skill": "machinist"}).json()
    assert (rate["base_wage_usd_per_hour"], rate["burden_factor"], rate["rate_usd_per_hour"]) == (5.0, 1.0, 5.0)
    assert rate["source"] and rate["as_of"] and rate["status"] == "manual"

    from app.schemas import Location

    loc = catalog.locations()["IN-CHENNAI"].model_dump()
    loc["labour_rates"] = {"machinist": {"base_wage_usd_per_hour": 1.0, "burden_factor": 1.3,
                                         "source": "test", "as_of": "2026-10-12"}}
    loc.pop("labour_inputs")
    assert Location(**loc).labour_rates == {"machinist": pytest.approx(1.3)}
