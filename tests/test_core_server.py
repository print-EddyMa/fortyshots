import json
import sys
from pathlib import Path

from fastapi.testclient import TestClient

sys.path.insert(0, str(Path(__file__).parent / "fixtures"))
from corehelp import BASE_URL, FIXTURE_DATA, MockTransport, make_cfg, run

from fortyshots import mockllm
from fortyshots.cli import main as cli_main
from fortyshots.doctor import run_doctor
from fortyshots.server import create_app


def test_live_server_api(tmp_path):
    cfg = make_cfg(tmp_path, "A5")
    summary, camp = run(cfg, run_id="mock-A5-P0-s0-202610300003")
    site = tmp_path / "site"
    site.mkdir()
    (site / "index.html").write_text("<html>fortyshots</html>")
    c = TestClient(create_app(cfg.runs_dir, site))
    runs = c.get("/api/runs").json()
    assert runs[0]["run_id"] == camp.run_id and runs[0]["hits"] == summary["hits"]
    with c.stream("GET", f"/api/runs/{camp.run_id}/events?after=2") as r:
        body = "".join(r.iter_text())
    data = [json.loads(line[6:]) for line in body.splitlines() if line.startswith("data: {\"seq")]
    assert data[0]["seq"] == 3 and data[-1]["kind"] == "summary" and "event: end" in body
    assert c.get(f"/api/runs/{camp.run_id}/summary").json() == summary
    img = next(p.name for p in (camp.run_dir / "img").iterdir())
    assert c.get(f"/api/runs/{camp.run_id}/img/{img}").content[:4] == b"\x89PNG"
    assert c.get(f"/data/runs/{camp.run_id}/summary.json").status_code == 200
    assert c.get("/").text.startswith("<html>")
    assert c.get("/api/runs/..%2Fetc/summary").status_code == 404
    assert c.post(f"/api/runs/{camp.run_id}/human", json={"action": "approve"}).status_code == 409
    (camp.run_dir / "pending.json").write_text(json.dumps({"round": 2, "ids": ["m0000000"]}))
    ok = c.post(f"/api/runs/{camp.run_id}/human", json={"action": "reject", "ids": ["m0000000"], "note": "no"})
    assert ok.json()["round"] == 2
    assert json.loads((camp.run_dir / "human.jsonl").read_text())["action"] == "reject"
    assert c.post(f"/api/runs/{camp.run_id}/human", json={"action": "maybe"}).status_code == 422


def test_mock_server_endpoints_match_llama_server_shape():
    c = TestClient(mockllm.create_app())
    assert c.get("/health").json() == {"status": "ok"}
    props = c.get("/props").json()
    assert props["build_info"] and props["model_path"].endswith(".gguf")
    assert c.get("/v1/models").json()["data"][0]["id"]
    assert len(c.post("/tokenize", json={"content": "abcdefgh"}).json()["tokens"]) == 2
    r = c.post("/v1/chat/completions", json={"messages": [{"role": "user", "content": "Reply READY"}]}).json()
    assert set(r["choices"][0]["message"]) >= {"content", "reasoning_content"}
    assert set(r["timings"]) >= {"prompt_n", "prompt_ms", "prompt_per_second", "predicted_n", "predicted_ms",
                                 "predicted_per_second", "cache_n"}
    again = c.post("/v1/chat/completions", json={"messages": [{"role": "user", "content": "Reply READY"}]}).json()
    assert again == r


def test_doctor_runs_against_the_mock(tmp_path):
    out = tmp_path / "doctor" / "report.json"
    rep = run_doctor(BASE_URL, out, FIXTURE_DATA, "P0", transport=MockTransport())
    assert rep["ok"] and json.loads(out.read_text())["summary"]["vision"] is True
    names = {c["name"] for c in rep["checks"]}
    assert {"tool_call", "json_schema", "vision", "reasoning_budget", "throughput"} <= names
    assert rep["estimate"]["campaign_minutes"] > 0 and rep["estimate"]["seeds_per_pool_k"] in (1, 2, 3)
    roles = next(c for c in rep["checks"] if c["name"] == "throughput")["detail"]["roles"]
    assert set(roles) == {"director", "proposer", "critic", "reporter"}


def test_doctor_without_vision_skips_image_checks(tmp_path, monkeypatch):
    """A profile without mmproj: llama-server reports no vision and rejects images; doctor must still pass."""
    from fortyshots import mockllm

    monkeypatch.setitem(mockllm.PROPS, "modalities", {"vision": False, "audio": False})
    real = mockllm.respond

    def no_images(req, **kw):
        if any(isinstance(m.get("content"), list) and any(p.get("type") == "image_url" for p in m["content"])
               for m in req.get("messages", [])):
            raise mockllm.MockHTTPError(500, {"error": {"code": 500, "message": "image input is not supported"}})
        return real(req, **kw)

    monkeypatch.setattr(mockllm, "respond", no_images)
    rep = run_doctor(BASE_URL, tmp_path / "doctor" / "report.json", FIXTURE_DATA, "P0", transport=MockTransport())
    vision = next(c for c in rep["checks"] if c["name"] == "vision")
    assert rep["ok"] and vision.get("skipped") and not vision["required"]
    assert rep["estimate"]["campaign_minutes"] > 0 and rep["recommendations"]["vision"] is False


def test_cli_run_and_index(tmp_path, capsys):
    runs = tmp_path / "runs"
    cli_main(["run", "--arm", "A4", "--pool", "P0", "--seed", "3", "--mode", "mock", "--data-dir", str(FIXTURE_DATA),
              "--runs-dir", str(runs), "--base-url", "http://127.0.0.1:9", "--doctor-report", ""])
    out = json.loads(capsys.readouterr().out.strip().splitlines()[-1])
    assert out["run_id"].startswith("mock-A4-P0-s3-") and out["spent"] == 40
    assert json.loads((runs / "index.json").read_text())[0]["run_id"] == out["run_id"]
    plan = tmp_path / "plan.json"
    plan.write_text(json.dumps({"defaults": {"mode": "mock"}, "runs": [{"arm": "A4", "pool": "P0", "seed": 3}]}))
    try:
        cli_main(["record", str(plan), "--data-dir", str(FIXTURE_DATA), "--runs-dir", str(runs),
                  "--base-url", "http://127.0.0.1:9", "--doctor-report", ""])
    except SystemExit as e:
        assert e.code == 0
    assert "skip" in capsys.readouterr().err


def test_mock_is_deterministic_and_policy_uses_only_messages(tmp_path):
    run_a = run(make_cfg(tmp_path, "A4", runs_dir=tmp_path / "a"))[0]
    run_b = run(make_cfg(tmp_path, "A4", runs_dir=tmp_path / "b"))[0]
    assert {k: v for k, v in run_a.items() if k != "duration_s"} == {k: v for k, v in run_b.items()
                                                                      if k != "duration_s"}
    src = Path(mockllm.__file__).read_text()
    assert "labels.parquet" not in src and "read_parquet" not in src
