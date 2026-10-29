import asyncio

from pydantic import BaseModel

from sourceloop.agents.common.tools import Tool, ToolOutput, ToolRunner, TurnGuard


class Inp(BaseModel):
    x: int


class Outp(BaseModel):
    y: int


def mk(fn, **kw):
    return Tool("t", Inp, Outp, fn, **kw)


async def test_cache_and_singleflight():
    n = 0

    async def fn(i: Inp):
        nonlocal n
        n += 1
        await asyncio.sleep(0.02)
        return ToolOutput[Outp](value=Outp(y=i.x * 2), confidence=0.9)

    r = ToolRunner([mk(fn)])
    results = await asyncio.gather(*[r.call("t", Inp(x=2)) for _ in range(10)])
    assert n == 1 and all(x.ok and x.value.y == 4 for x in results)
    again = await r.call("t", Inp(x=2))
    assert again.cached and n == 1
    assert r.stats["singleflight_join"] == 9 and r.stats["cache_hit"] == 1
    await r.call("t", Inp(x=3))
    assert n == 2


async def test_timeout_fail_closed_and_retry():
    calls = 0

    async def slow(i: Inp):
        nonlocal calls
        calls += 1
        await asyncio.sleep(1)
        return ToolOutput[Outp](value=Outp(y=1))

    r = ToolRunner([mk(slow, timeout_s=0.01, retries=1)])
    res = await r.call("t", Inp(x=1))
    assert not res.ok and res.error == "TIMEOUT" and res.confidence == 0 and calls == 2


async def test_exception_and_invalid_output_do_not_leak():
    async def boom(i: Inp):
        raise RuntimeError("secret-internal-detail")

    async def wrong(i: Inp):
        return ToolOutput[Inp](value=Inp(x=1))

    r = ToolRunner([mk(boom, retries=0)])
    res = await r.call("t", Inp(x=1))
    assert res.error == "ERROR" and "secret" not in res.model_dump_json()
    r2 = ToolRunner([mk(wrong, retries=3)])
    res2 = await r2.call("t", Inp(x=1))
    assert res2.error == "INVALID_OUTPUT" and r2.stats["retry"] == 0


async def test_failures_not_cached_disabled_unknown():
    state = {"fail": True}

    async def flaky(i: Inp):
        if state["fail"]:
            raise RuntimeError
        return ToolOutput[Outp](value=Outp(y=1))

    r = ToolRunner([mk(flaky, retries=0)])
    assert not (await r.call("t", Inp(x=1))).ok
    state["fail"] = False
    assert (await r.call("t", Inp(x=1))).ok
    r.set_enabled("t", False)
    assert (await r.call("t", Inp(x=9))).error == "DISABLED"
    assert (await r.call("nope", Inp(x=1))).error == "UNKNOWN_TOOL"


async def test_loop_guard():
    async def ok(i: Inp):
        return ToolOutput[Outp](value=Outp(y=i.x))

    r = ToolRunner([mk(ok)])
    g = TurnGuard(max_calls=3)
    outs = [await r.call("t", Inp(x=i), guard=g) for i in range(5)]
    assert [o.ok for o in outs] == [True, True, True, False, False] and outs[3].error == "LOOP_LIMIT"
    g2 = TurnGuard(max_stale=3)
    import pytest

    from sourceloop.agents.common.tools import LoopLimit

    g2.progress({"a": 1})
    g2.progress({"a": 1})
    with pytest.raises(LoopLimit):
        g2.progress({"a": 1})


async def test_hook_receives_events():
    seen = []

    async def hook(kind, data):
        seen.append(kind)

    async def ok(i: Inp):
        return ToolOutput[Outp](value=Outp(y=1))

    r = ToolRunner([mk(ok)], hook=hook)
    await r.call("t", Inp(x=1))
    assert seen == ["subagent_call", "subagent_result"]


async def test_hook_receives_llm_provenance():
    from sourceloop.agents.common import provenance

    seen = []

    async def hook(kind, data):
        if kind == "subagent_result":
            seen.append(data)

    async def fn(i: Inp):
        provenance.note({"provider": "p", "model": "m", "digest": "d", "tokens": 7, "replayed": False})
        return ToolOutput[Outp](value=Outp(y=1))

    r = ToolRunner([mk(fn)], hook=hook)
    await r.call("t", Inp(x=1))
    assert seen[0]["llm"] == [{"provider": "p", "model": "m", "digest": "d", "tokens": 7, "replayed": False}]
