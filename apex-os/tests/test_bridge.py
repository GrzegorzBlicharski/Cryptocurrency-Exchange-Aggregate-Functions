import json
import subprocess
import sys
from pathlib import Path

from apex import bridge

from conftest import TODAY


def _state():
    ev = []
    for i in range(10):
        d = f"2026-09-{20 + i:02d}"
        ev += [{"id": f"s{i}", "ts": f"{d}T07:00:00Z", "kind": "sleep", "data": {"day": d, "duration_min": 400 + i}},
               {"id": f"g{i}", "ts": f"{d}T09:00:00Z", "kind": "german_session",
                "data": {"day": d, "minutes": 30, "skill": "speaking", "mode": "active", "focus": 4}},
               {"id": f"q{i}", "ts": f"{d}T18:00:00Z", "kind": "questions",
                "data": {"day": d, "domain": "law", "area": "contract", "total": 10, "correct": 5 + i % 4}},
               {"id": f"m{i}", "ts": f"{d}T20:00:00Z", "kind": "movement", "data": {"day": d, "steps": 6000}}]
    ev.append({"id": "bad", "ts": "2026-09-30T10:00:00Z", "kind": "sleep", "data": {"day": "nope"}})
    return {"today": TODAY.isoformat(), "events": ev,
            "goals": [{"id": "g1", "title": "C1 niemiecki", "domain": "german", "weight": 5}],
            "skills": [{"name": "German", "level": 3}],
            "plan": [{"day": "2026-10-01", "title": "Prawo blok", "domain": "law", "minutes": 60, "status": "done"}]}


def test_bridge_snapshot_shape(db):
    out = bridge.run(_state())
    assert out["load"]["events_loaded"] == 40
    assert out["load"]["errors"][0]["id"] == "bad"  # reported, never guessed
    assert out["domains"]["law"]["score"] is not None and out["domains"]["german"]["label"] == "Niemiecki"
    assert len(out["top3"]) <= 3 and all("why" in a and "criteria" in a for a in out["top3"])
    assert out["coverage"]["events"] == 41 and out["brief"]["day"] == TODAY.isoformat()
    json.dumps(out, default=str)  # serializable for the artifact store


def test_bridge_empty_state_is_honest(db):
    out = bridge.run({"today": TODAY.isoformat()})
    assert out["apex_score"] is None and out["sustainability"]["band"] == "UNKNOWN"
    assert all(a["key"].endswith(":onboard") for a in out["top3"])


def test_bridge_cli_uses_memory_only(tmp_path):
    src, dst = tmp_path / "state.json", tmp_path / "out.json"
    src.write_text(json.dumps(_state()))
    r = subprocess.run([sys.executable, "-m", "apex", "bridge", "--in", str(src), "--out", str(dst)],
                       capture_output=True, text=True, cwd=Path(__file__).parents[1])
    assert r.returncode == 0, r.stderr
    assert json.loads(r.stdout)["events_loaded"] == 40
    assert json.loads(dst.read_text())["schema"] == 1
