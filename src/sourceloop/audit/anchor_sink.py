"""External audit anchor: a hash-chained, append-only file that lives OUTSIDE the database.

The in-DB chain and triggers stop edits through the application, but someone with write access to the database could rewrite a whole
chain consistently. Every run segment end publishes `(project, seq, head_hash)` here; `verify` then checks that the chain still contains
that `seq` with that hash. A rewrite is caught unless the attacker also rewrites this file, which is a different trust zone.

Test-bed sink: a local JSONL file (each line commits to the previous line, so the file itself is tamper-evident). The class is the
interface a production sink (object storage with retention lock, RFC 3161 timestamps) would implement. Hashes only: nothing sensitive.
"""
from __future__ import annotations

import json
import threading
from dataclasses import dataclass
from pathlib import Path

from sourceloop.audit.canonical import dumps, sha256_hex

GENESIS = "0" * 64


@dataclass(frozen=True)
class Anchor:
    project_id: str
    seq: int
    head_hash: str
    ts: str


class FileAnchorSink:
    def __init__(self, path: str | Path) -> None:
        self.path = Path(path)
        self._lock = threading.Lock()

    def _lines(self) -> list[dict]:
        if not self.path.exists():
            return []
        out = []
        for raw in self.path.read_text().splitlines():
            if raw.strip():
                out.append(json.loads(raw))
        return out

    def publish(self, project_id: str, seq: int, head_hash: str, ts: str) -> None:
        with self._lock:
            lines = self._lines()
            prev = lines[-1]["line_hash"] if lines else GENESIS
            body = {"project": project_id, "seq": seq, "head": head_hash, "ts": ts, "prev": prev}
            body["line_hash"] = sha256_hex(dumps(body))
            self.path.parent.mkdir(parents=True, exist_ok=True)
            with self.path.open("a") as f:
                f.write(json.dumps(body, sort_keys=True) + "\n")

    def file_intact(self) -> bool:
        """The anchor file's own chain: every line commits to the one before it."""
        prev = GENESIS
        try:
            for ln in self._lines():
                body = {k: v for k, v in ln.items() if k != "line_hash"}
                if ln.get("prev") != prev or ln.get("line_hash") != sha256_hex(dumps(body)):
                    return False
                prev = ln["line_hash"]
        except (ValueError, KeyError, TypeError):
            return False
        return True

    def anchors(self, project_id: str) -> list[Anchor]:
        try:
            return [Anchor(ln["project"], ln["seq"], ln["head"], ln["ts"]) for ln in self._lines() if ln.get("project") == project_id]
        except (ValueError, KeyError, TypeError):
            return []

    def check(self, project_id: str, hashes_by_seq: dict[int, str]) -> dict:
        """Every anchor published for the project must still match the chain. Returns a small verdict for the verify endpoint."""
        anchors = self.anchors(project_id)
        intact = self.file_intact()
        bad = [a.seq for a in anchors if hashes_by_seq.get(a.seq) != a.head_hash]
        return {"anchors": len(anchors), "file_intact": intact, "matches": intact and not bad, "first_mismatch_seq": bad[0] if bad else None}
