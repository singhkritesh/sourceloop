"""The project record in plain sentences: a buyer reads its whole project, a supplier only its own view, and no number, hash or limit ever appears."""
import re

from api_support import Api, resolve_all, w  # noqa: F401
from test_studio_api import new_project, no_network  # noqa: F401


async def test_the_record_is_plain_sentences_for_each_side(w):  # noqa: F811
    b = await w.client("buyer")
    p = await new_project(w)
    await b.post(f"/projects/{p['id']}/start")
    await w.service.join(p["id"])
    for _ in range(6):
        await resolve_all(w, p["id"])
        await w.service.join(p["id"])
    r = await b.get(f"/projects/{p['id']}/record")
    assert r.status_code == 200, r.text
    rec = r.json()
    assert rec["items"] and rec["chain"]["valid"] is True and rec["chain"]["events"] >= len(rec["items"])
    assert set(rec["items"][0]) == {"seq", "ts", "who", "what"}
    text = " ".join(i["what"] for i in rec["items"])
    assert "sent an invitation to quote" in text
    assert not re.search(r"[0-9a-f]{40,}|\d+\.\d{2,}", text)           # no hash, no price
    s = await w.client("supplier1")
    sup = await s.get(f"/projects/{p['id']}/record")
    assert sup.status_code == 200 and sup.json()["chain"] is None
    assert all(i["what"] for i in sup.json()["items"])
    assert len(sup.json()["items"]) <= len(rec["items"])
    assert (await (await w.client("outsider")).get(f"/projects/{p['id']}/record")).status_code == 404
