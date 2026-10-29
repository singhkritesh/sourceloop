"""Connectors: contracts, OFAC parsing, Open-Meteo handling, fail-closed stubs, DB sync, safe_http redirects, weather tool wiring."""
import httpx
import pytest
from sqlalchemy import select

from api_support import make_settings
from sourceloop.agents.common import contracts as K
from sourceloop.agents.common.tools import ToolRunner
from sourceloop.connectors import ofac
from sourceloop.connectors.base import (
    ConnectorError,
    SanctionedEntity,
    Status,
    SupplierProfilePatch,
    WeatherSignal,
)
from sourceloop.connectors.catalog import CATALOG, by_id
from sourceloop.connectors.countries import iso2
from sourceloop.connectors.open_meteo import OpenMeteoWeather
from sourceloop.connectors.stubs import (
    CertificateRegistry,
    FinancialRiskProvider,
    SbnSupplierProfile,
    fill_blanks,
)
from sourceloop.connectors.sync import sync_sanctions
from sourceloop.data import db as dbm
from sourceloop.data.models import WatchlistEntry
from sourceloop.data.news import SyntheticNews
from sourceloop.data.registry import Registry
from sourceloop.data.synthetic import TODAY
from sourceloop.llm.client import LLMClient, MemoryReplayStore
from sourceloop.llm.config import stub_config
from sourceloop.llm.providers import StubProvider
from sourceloop.net.safe_http import EgressBlocked, SafeHttp
from sourceloop.subagents.buyer_tools import build_buyer_tools

SDN = ('36,"AEROCARIBBEAN AIRLINES",-0- ,"CUBA",-0- ,-0- ,-0- ,-0- ,-0- ,-0- ,-0- ,-0- \n'
       '173,"ANGLO-CARIBBEAN CO., LTD.",-0- ,"CUBA",-0- ,-0- ,-0- ,-0- ,-0- ,-0- ,-0- ,-0- \n'
       '999,"JOHN DOE","individual","SDGT",-0- ,-0- ,-0- ,-0- ,-0- ,-0- ,-0- ,-0- \n'
       '1000,"SOME VESSEL","vessel","IRAN",-0- ,-0- ,-0- ,-0- ,-0- ,-0- ,-0- ,-0- \n'
       'broken\n\n')
ALT = '36,12,"aka","AERO-CARIBBEAN",-0- \n173,57,"aka","AVIA IMPORT",-0- \n173,58,"aka","AVIA IMPORT",-0- \n999,1,"aka","JD",-0- \n'
ADD = ('36,25,-0- ,"Havana","Cuba",-0- \n173,129,"Ibex House","London EC3N 1DY","United Kingdom",-0- \n'
       '173,130,"Calle 1","Madrid","Spain",-0- \n173,131,"x","y","Atlantis",-0- \n')


# ------------------------------------------------------------------ catalog / contracts
def test_catalog_is_honest_and_consistent():
    ids = [c.id for c in CATALOG]
    assert len(ids) == len(set(ids))
    for c in CATALOG:
        if c.status is Status.LIVE:
            assert c.hosts, f"{c.id}: a LIVE connector must declare its egress hosts"
        if c.status is Status.STUB:
            assert c.fields and c.provider, c.id
    assert by_id("ofac_sdn").status is Status.LIVE and by_id("sbn_supplier_profile").status is Status.STUB and by_id("nope") is None


def test_contracts_are_strict():
    with pytest.raises(ValueError):
        SanctionedEntity(source_id="1", name="X", country="USA", list_name="L")          # not alpha-2
    with pytest.raises(ValueError):
        SanctionedEntity(source_id="1", name="X", country="US", list_name="L", extra="x")  # extra forbidden
    with pytest.raises(ValueError):
        WeatherSignal(horizon_days=3, max_precip_mm=-1, max_wind_kmh=0, severe_days=0, source_url="u")


# ------------------------------------------------------------------ OFAC parsing
def test_iso2_known_variants_and_unknown():
    assert iso2("Cuba") == "CU" and iso2(" united kingdom ") == "GB" and iso2("Burma") == "MM" and iso2("Korea, North") == "KP"
    assert iso2("Atlantis") == "XX" and iso2("") == "XX"


def test_parse_entities_only_with_aliases_and_countries():
    ents = ofac.parse(SDN, ALT, ADD)
    by = {(e.source_id, e.country): e for e in ents}
    assert {e.source_id for e in ents} == {"36", "173"}                 # individual / vessel / broken rows excluded
    assert set(by) == {("36", "CU"), ("173", "GB"), ("173", "ES")}     # one row per country; unknown country dropped
    assert by[("36", "CU")].aliases == ("AERO-CARIBBEAN",)
    assert by[("173", "GB")].aliases == ("AVIA IMPORT",)               # duplicate alias collapsed
    assert all(e.list_name == ofac.LIST_NAME for e in ents)


def test_entity_without_usable_address_gets_xx_not_dropped():
    ents = ofac.parse('5,"NO ADDRESS CO",-0- ,"X"\n', "", "")
    assert [(e.source_id, e.country) for e in ents] == [("5", "XX")]


def test_parse_strips_control_characters_and_survives_garbage():
    ents = ofac.parse('7,"EVIL\x00\x07 NAME\x1b[31m",-0- ,"X"\n', "", "")
    assert ents and "\x00" not in ents[0].name and "\x1b" not in ents[0].name
    assert ofac.parse("not,csv\n\n,,,\n", "x", "y") == []


class FakeHttp:
    def __init__(self, files=None, exc=None, status=200):
        self.files, self.exc, self.status = files or {}, exc, status

    async def get_following(self, url, **kw):
        if self.exc:
            raise self.exc
        name = url.rsplit("/", 1)[1]
        return httpx.Response(self.status, content=self.files.get(name, "").encode("latin-1"))


async def test_ofac_fetch_failure_codes_never_leak_upstream():
    for http, code in [(FakeHttp(exc=EgressBlocked("REDIRECT_BLOCKED")), "EGRESS_REDIRECT_BLOCKED"), (FakeHttp(exc=OSError("secret-host")), "UNREACHABLE"),
                       (FakeHttp(status=503), "HTTP_503")]:
        with pytest.raises(ConnectorError) as e:
            await ofac.OfacSdn(http).fetch()
        assert e.value.code == code and "secret" not in str(e.value)


async def test_ofac_rejects_truncated_list():
    with pytest.raises(ConnectorError) as e:
        await ofac.OfacSdn(FakeHttp({"sdn.csv": SDN, "alt.csv": ALT, "add.csv": ADD})).fetch()
    assert e.value.code == "LIST_TOO_SMALL"


# ------------------------------------------------------------------ DB sync
class ListSource:
    def __init__(self, ents):
        self.ents = ents

    async def fetch(self):
        return self.ents


def _ents(n, lst="OFAC_SDN"):
    return [SanctionedEntity(source_id=str(i), name=f"Entity {i}", aliases=(f"E{i}",), country="XX" if i % 10 == 0 else "CU", list_name=lst) for i in range(n)]


@pytest.fixture
async def database(tmp_path):
    dbm.configure(make_settings(tmp_path).database_url)
    await dbm.create_all()
    yield
    await dbm.dispose()


async def test_sync_replaces_only_its_own_list_and_refuses_truncated(database):
    from sourceloop.data.models import WatchlistEntry as W
    async with dbm.session_scope() as s:
        s.add(W(name="Synthetic Bad Co", aliases=[], country="DE", list_name="SYNTHETIC"))
        s.add(W(name="Old Ofac Row", aliases=[], country="CU", list_name="OFAC_SDN"))
    async with dbm.session_scope() as s:
        r = await sync_sanctions(s, ListSource(_ents(150)), list_name="OFAC_SDN")
    assert (r.inserted, r.removed, r.unknown_country) == (150, 1, 15)
    async with dbm.session_scope() as s:
        names = {n for (n,) in (await s.execute(select(WatchlistEntry.name))).all()}
        assert "Synthetic Bad Co" in names and "Old Ofac Row" not in names and "Entity 7" in names
    with pytest.raises(ConnectorError) as e:        # truncated feed must not wipe the list
        async with dbm.session_scope() as s:
            await sync_sanctions(s, ListSource(_ents(5)), list_name="OFAC_SDN")
    assert e.value.code == "LIST_TOO_SMALL"
    async with dbm.session_scope() as s:
        assert (await s.execute(select(WatchlistEntry).where(WatchlistEntry.list_name == "OFAC_SDN"))).scalars().all().__len__() == 150


# ------------------------------------------------------------------ Open-Meteo
def _meteo(body, status=200):
    class H:
        async def get(self, url, **kw):
            assert kw["params"]["forecast_days"] == 16
            return httpx.Response(status, content=body if isinstance(body, bytes) else body.encode())
    return OpenMeteoWeather(H())  # type: ignore[arg-type]


async def test_open_meteo_signal_counts_severe_days():
    body = '{"daily":{"precipitation_sum":[0,60,10,0],"wind_speed_10m_max":[10,20,70,5],"time":["a"]}}'
    s = await _meteo(body).signal("vn")
    assert (s.horizon_days, s.max_precip_mm, s.max_wind_kmh, s.severe_days) == (4, 60.0, 70.0, 2)


async def test_open_meteo_fails_closed():
    for body, status, code in [('{"daily":{"precipitation_sum":[null],"wind_speed_10m_max":[null]}}', 200, "NO_DATA"),
                               ("<html>", 200, "BAD_RESPONSE"), ('{"daily":{"precipitation_sum":[1]}}', 200, "BAD_RESPONSE"), ("{}", 429, "HTTP_429")]:
        with pytest.raises(ConnectorError) as e:
            await _meteo(body, status).signal("VN")
        assert e.value.code == code
    with pytest.raises(ConnectorError) as e:
        await _meteo("{}").signal("ZZ")
    assert e.value.code == "UNSUPPORTED_COUNTRY"


# ------------------------------------------------------------------ stubs
async def test_stubs_fail_closed_and_fill_blanks_never_overwrites():
    for coro in (SbnSupplierProfile().profile("S1"), FinancialRiskProvider().risk("S1"), CertificateRegistry().check("S1", "ISO9001")):
        with pytest.raises(ConnectorError) as e:
            await coro
        assert e.value.code == "NOT_CONFIGURED"
    patch = SupplierProfilePatch(supplier_id="S1", lead_time_weeks=6, capacity_units=900, years_in_business=12, source="t")
    out = fill_blanks({"lead_time_weeks": None, "capacity_units": 500, "years_in_business": None}, patch)
    assert out == {"lead_time_weeks": 6, "capacity_units": 500, "years_in_business": 12}


# ------------------------------------------------------------------ safe_http.get_following
class _Route(httpx.AsyncBaseTransport):
    routes: dict[str, tuple[int, dict, bytes]] = {}

    async def handle_async_request(self, request):
        host = request.extensions.get("sni_hostname", request.url.host)
        status, headers, body = self.routes[f"{host}{request.url.path}"]
        return httpx.Response(status, headers=headers, content=body)


@pytest.fixture
def routed(monkeypatch):
    async def fake_check(self, url):
        from urllib.parse import urlparse
        if (urlparse(url).hostname or "") not in self.allowed:
            raise EgressBlocked("HOST_NOT_ALLOWED")
        return ["203.0.113.9"]
    monkeypatch.setattr(SafeHttp, "_check", fake_check)
    monkeypatch.setattr(httpx, "AsyncHTTPTransport", _Route)
    return _Route.routes


async def test_get_following_follows_allowlisted_hops_with_larger_cap(routed):
    routed["a.example/x"] = (302, {"location": "https://b.example/y"}, b"")
    routed["b.example/y"] = (200, {}, b"z" * (3 * 1024 * 1024))
    h = SafeHttp({"a.example", "b.example"})
    r = await h.get_following("https://a.example/x", max_bytes=4 * 1024 * 1024)
    assert r.status_code == 200 and len(r.content) == 3 * 1024 * 1024
    with pytest.raises(EgressBlocked) as e:                    # default 2 MB cap still applies
        await h.get_following("https://a.example/x")
    assert e.value.code == "RESPONSE_TOO_LARGE"


async def test_get_following_blocks_redirect_to_unlisted_host_http_and_loops(routed):
    routed["a.example/x"] = (302, {"location": "https://evil.example/y"}, b"")
    routed["a.example/h"] = (302, {"location": "http://a.example/y"}, b"")
    routed["a.example/loop"] = (302, {"location": "https://a.example/loop"}, b"")
    h = SafeHttp({"a.example"})
    for path, code in [("x", "REDIRECT_BLOCKED"), ("h", "REDIRECT_BLOCKED"), ("loop", "REDIRECT_BLOCKED")]:
        with pytest.raises(EgressBlocked) as e:
            await h.get_following(f"https://a.example/{path}", max_redirects=2)
        assert e.value.code == code
    with pytest.raises(EgressBlocked) as e:
        await h.get_following("http://a.example/x")             # https only
    assert e.value.code == "BAD_URL"


# ------------------------------------------------------------------ weather tool wiring
class LiveWx:
    def __init__(self, severe=0, exc=None):
        self.severe, self.exc = severe, exc

    async def signal(self, country):
        if self.exc:
            raise self.exc
        return WeatherSignal(horizon_days=16, max_precip_mm=80, max_wind_kmh=40, severe_days=self.severe, source_url="https://api.open-meteo.com/v1/forecast?x")


async def _wx(**kw):
    llm = LLMClient(stub_config(), {"stub": StubProvider()}, MemoryReplayStore())
    tools = ToolRunner(build_buyer_tools(Registry([], []), llm, SyntheticNews(), "P", TODAY, **kw))
    return await tools.call(K.WEATHER, K.WeatherIn(origin_country="DE", lead_time_weeks=8))


async def test_weather_default_is_unchanged_and_needs_no_connector():
    base = await _wx()
    assert base.ok and base.confidence == 0.7
    assert (await _wx(weather=LiveWx(5))).value.delay_probability == base.value.delay_probability   # not wanted => ignored


async def test_live_weather_raises_probability_and_fails_closed_to_low_confidence():
    base = await _wx()
    live = await _wx(weather=LiveWx(severe=3), weather_wanted=True)
    assert live.value.delay_probability == pytest.approx(base.value.delay_probability + 0.15) and live.confidence == 0.75
    assert any("severe weather forecast" in d for d in live.value.drivers) and any("open-meteo" in s.url for s in live.sources)
    down = await _wx(weather=LiveWx(exc=ConnectorError("UNREACHABLE")), weather_wanted=True)
    assert down.value.delay_probability == base.value.delay_probability and down.confidence == 0.4 and "UNREACHABLE" in down.assumptions[0]
    none = await _wx(weather=None, weather_wanted=True)           # asked for live, none available => low confidence, not silent
    assert none.confidence == 0.4


# ------------------------------------------------------------------ Studio wiring
def test_weather_source_default_is_omitted_so_published_hashes_stay_valid():
    from sourceloop.studio.defaults import default_config
    from sourceloop.studio.lifecycle import content_hash

    cfg = default_config()
    assert cfg.data_sources.weather_source == "recorded"
    assert "weather_source" not in cfg.model_dump(mode="json")["data_sources"]
    on = cfg.model_copy(update={"data_sources": cfg.data_sources.model_copy(update={"weather_source": "open_meteo"})})
    assert on.model_dump(mode="json")["data_sources"]["weather_source"] == "open_meteo" and content_hash(on) != content_hash(cfg)


def test_validator_warns_when_live_weather_host_is_not_allowed_and_accepts_when_listed():
    from sourceloop.studio.defaults import default_config
    from sourceloop.studio.validator import validate

    base = default_config()

    def with_ds(**kw):
        return base.model_copy(update={"data_sources": base.data_sources.model_copy(update=kw)})

    def codes(cfg):
        return {x.code for x in validate(cfg, is_admin=True).warnings}

    assert "WEATHER_HOST_NOT_ALLOWED" in codes(with_ds(weather_source="open_meteo"))
    assert "WEATHER_HOST_NOT_ALLOWED" not in codes(with_ds(weather_source="open_meteo", egress_allowlist=("api.open-meteo.com",)))
    assert "WEATHER_HOST_NOT_ALLOWED" not in codes(base)


def test_scenario_spec_carries_weather_source_and_hosts_and_service_only_attaches_when_allowed():
    from sourceloop.api.service import ProjectService
    from sourceloop.connectors.open_meteo import OpenMeteoWeather
    from sourceloop.studio.defaults import default_config

    base = default_config()
    cfg = base.model_copy(update={"data_sources": base.data_sources.model_copy(update={"weather_source": "open_meteo", "egress_allowlist": ("API.Open-Meteo.com",)})})
    spec = cfg.to_scenario_spec()
    assert spec.weather_source == "open_meteo" and spec.egress_hosts == frozenset({"api.open-meteo.com"}) and spec.weather is None
    ProjectService._live_weather(spec)
    assert isinstance(spec.weather, OpenMeteoWeather)
    nohost = base.model_copy(update={"data_sources": base.data_sources.model_copy(update={"weather_source": "open_meteo"})}).to_scenario_spec()
    ProjectService._live_weather(nohost)
    assert nohost.weather is None                      # host not allowed => no network source
    rec = default_config().to_scenario_spec()
    ProjectService._live_weather(rec)
    assert rec.weather is None and rec.weather_source == "recorded"


# ------------------------------------------------------------------ profile enrichment (blank fields) + DB watchlist reaching runs
async def _research(profile=None):
    from sourceloop.data.synthetic import generate, make_needs
    vis, full, _ = generate(42)
    llm = LLMClient(stub_config(), {"stub": StubProvider()}, MemoryReplayStore())
    tools = ToolRunner(build_buyer_tools(vis, llm, SyntheticNews(), "P", TODAY, profile=profile(full) if profile else None))
    n = make_needs(42)[0]
    need = K.NeedSpec(id=n.id, category=n.category, industry=n.industry, volume_units=n.volume_units, required_certs=n.required_certs,
                      excluded_countries=n.excluded_countries, max_lead_time_weeks=n.max_lead_time_weeks)
    return vis, full, await tools.call(K.RESEARCH, K.ResearchIn(need=need, longlist_size=60))


async def test_simulated_profile_fills_only_blanks_and_reduces_missing_data_reviews():
    from sourceloop.connectors.simulated import SimulatedProfile
    _, _, plain = await _research()
    _, _, rich = await _research(SimulatedProfile)
    n_missing = lambda r: sum(1 for c in r.value.candidates if any(x.startswith("MISSING_DATA") for x in c.reasons))  # noqa: E731
    assert plain.ok and rich.ok and n_missing(plain) > 0 and n_missing(rich) == 0
    assert any("blank fields filled" in a for a in rich.assumptions) and not plain.assumptions


async def test_profile_never_overwrites_a_present_value_or_leaks_unit_cost():
    from sourceloop.connectors.simulated import SimulatedProfile
    vis, full, _ = await _research()
    prof = SimulatedProfile(full)
    sid = next(s.id for s in vis.suppliers if s.lead_time_weeks is None)
    patch = await prof.profile(sid)
    assert "unit_cost" not in patch.model_dump() and patch.lead_time_weeks is not None
    merged = fill_blanks({"lead_time_weeks": 99, "capacity_units": None, "years_in_business": None, "financial_risk": None}, patch)
    assert merged["lead_time_weeks"] == 99                      # a present value always wins
    with pytest.raises(ConnectorError) as e:
        await prof.profile("S-NOPE")
    assert e.value.code == "NOT_FOUND"


async def test_failing_profile_source_leaves_blanks_for_review_and_lowers_confidence():
    class Down:
        def __init__(self, full):
            pass

        async def profile(self, supplier_id):
            raise ConnectorError("UNREACHABLE")
    _, _, r = await _research(Down)
    assert r.ok and r.confidence == 0.8 and any("no profile data" in a for a in r.assumptions)
    assert any(x.startswith("MISSING_DATA") for c in r.value.candidates for x in c.reasons)   # still blank => still REVIEW


async def test_extra_watchlist_rows_screen_suppliers_in_a_real_run():
    from sourceloop.data.registry import WatchEntry
    from sourceloop.data.synthetic import generate
    from sourceloop.orchestrator.scenario import ScenarioSpec, build_project, run_to_completion
    vis, full, _ = generate(42)
    base = await run_to_completion(build_project(ScenarioSpec(need_index=0), vis, full))
    top = base.shortlist[0]
    name = full.get(top).name
    spec = ScenarioSpec(need_index=0, extra_watchlist=(WatchEntry("DB:1", name, "XX", "OFAC_SDN"),))
    after = await run_to_completion(build_project(spec, vis, full))
    assert top in base.shortlist and top not in after.shortlist   # the listed supplier is screened out of the run


async def test_db_watchlist_loader_skips_lists_the_registry_already_carries(database):
    from sourceloop.api.service import ProjectService
    from sourceloop.data.models import WatchlistEntry as W
    from sourceloop.data.synthetic import generate
    own = generate(42)[0].watchlist[0].list_name
    async with dbm.session_scope() as s:
        s.add(W(name="Seeded Synthetic Co", aliases=[], country="DE", list_name=own))
        s.add(W(name="Real Listed Co", aliases=["RLC"], country="CU", list_name="OFAC_SDN"))
    async with dbm.session_scope() as s:
        ents, sig = await ProjectService._db_watchlist(s, 42)
    assert [(e.name, e.list_name, e.aliases) for e in ents] == [("Real Listed Co", "OFAC_SDN", ("RLC",))] and sig
    async with dbm.session_scope() as s:
        await s.execute(W.__table__.delete())
    async with dbm.session_scope() as s:
        assert await ProjectService._db_watchlist(s, 42) == ((), "")


def test_profile_source_default_is_omitted_from_dumps_and_reaches_the_spec():
    from sourceloop.studio.defaults import default_config
    from sourceloop.studio.lifecycle import content_hash
    cfg = default_config()
    assert "profile_source" not in cfg.model_dump(mode="json")["data_sources"] and cfg.to_scenario_spec().profile_source == "none"
    on = cfg.model_copy(update={"data_sources": cfg.data_sources.model_copy(update={"profile_source": "simulated"})})
    assert on.model_dump(mode="json")["data_sources"]["profile_source"] == "simulated" and content_hash(on) != content_hash(cfg)
    assert on.to_scenario_spec().profile_source == "simulated"


# ------------------------------------------------------------------ through the real API: a DB watchlist row screens a real project
from api_support import build_world, create_and_start, run_to_done, w  # noqa: E402,F401,F811


async def test_synced_watchlist_row_changes_a_real_api_project(w):  # noqa: F811
    from sourceloop.data.models import WatchlistEntry as W
    from sourceloop.data.synthetic import generate
    from sourceloop.orchestrator.scenario import ScenarioSpec, build_project, run_to_completion

    def invited(audit):
        return {e["evidence"][0].get("to") for e in audit
                if e["action"] == "message_delivered" and e["evidence"] and e["evidence"][0].get("type") == "INVITE"}

    pid = await create_and_start(w)
    assert (await run_to_done(w, pid))["outcome"] == "DEAL"
    vis, full, _ = generate(42)
    target = (await run_to_completion(build_project(ScenarioSpec(need_index=0), vis, full))).awarded   # the supplier the first run awards
    assert target
    async with dbm.session_scope() as s:   # what `sync-ofac` writes: a sanctioned entity whose name matches that supplier
        s.add(W(name=full.get(target).name, aliases=[], country="XX", list_name="OFAC_SDN"))
    pid2 = await create_and_start(w)
    await run_to_done(w, pid2)
    a1 = (await (await w.client("org_admin")).get(f"/projects/{pid}/audit?limit=500")).json()["items"]
    a2 = (await (await w.client("org_admin")).get(f"/projects/{pid2}/audit?limit=500")).json()["items"]
    assert f"supplier:{target}" in invited(a1) and f"supplier:{target}" not in invited(a2)   # the listed supplier is never invited
    ev = await w.client("evaluator")
    assert (await ev.get(f"/projects/{pid2}/audit/verify")).json()["valid"] is True
