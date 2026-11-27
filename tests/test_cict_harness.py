import json
import math
import pytest
from dreamlake.cict import Suite


def definition():
    return (Suite("suite").node("choice", kind="decision")
            .node("a", attributes={"device": "cpu"}).node("b")
            .edge("a-path", "choice", "a").edge("b-path", "choice", "b"))


def test_round_trip_static_and_unvisited_alternatives(tmp_path):
    suite = definition()
    suite.write(tmp_path / "suite.json")
    loaded = Suite.read(tmp_path / "suite.json")
    assert loaded.snapshot() == suite.snapshot()
    events = []
    run = loaded.run("r1", on_event=events.append)
    run.select("choice", "a-path", attributes={"reason": "quick"})
    with run.test("a"):
        run.finish("a", "passed", metrics={"durationMs": 0})
    data = run.snapshot()
    assert [r["status"] for r in data["records"]] == ["passed", "passed", "not_run"]
    assert [e["selected"] for e in data["edges"]] == [True, False]
    assert data["records"][1]["metrics"] == {"durationMs": 0}
    assert data["records"][2]["metrics"] == {}
    assert events[0]["snapshot"]["records"][1]["status"] == "not_run"
    assert [e["sequence"] for e in events] == list(range(1, len(events) + 1))
    assert events == run.events()
    run.write(tmp_path / "cict.json")
    run.write_events(tmp_path / "events.jsonl")
    assert json.loads((tmp_path / "cict.json").read_text()) == data
    assert [json.loads(s) for s in (tmp_path / "events.jsonl").read_text().splitlines()] == events
    assert all(r["status"] == "not_run" for r in suite.snapshot()["records"])


@pytest.mark.parametrize("error,status", [(AssertionError("secret"), "failed"),
                                          (RuntimeError("secret"), "unknown"),
                                          (KeyboardInterrupt("secret"), "unknown")])
def test_exception_is_propagated_and_never_captures_output(error, status, capsys):
    run = definition().run("r")
    with pytest.raises(type(error)):
        with run.test("a"):
            print("private console output")
            raise error
    assert run.snapshot()["records"][1]["status"] == status
    assert "secret" not in json.dumps(run.events())
    assert "private console output" not in json.dumps(run.snapshot())
    assert capsys.readouterr().out == "private console output\n"


def test_invalid_mutations_leave_state_unchanged():
    run = definition().run("r")
    baseline = run.snapshot()
    for operation in [lambda: run.finish("a", "passed"),
                      lambda: run.select("choice", "missing"),
                      lambda: run.start("missing")]:
        with pytest.raises(ValueError):
            operation()
        assert run.snapshot() == baseline
    run.start("a")
    running = run.snapshot()
    for metrics in [{"x": math.nan}, {"x": math.inf}, {"x": True}, {"x": "1"}]:
        with pytest.raises(ValueError):
            run.finish("a", "passed", metrics=metrics)
        assert run.snapshot() == running
    run.finish("a", "passed")
    with pytest.raises(ValueError):
        run.finish("a", "failed")
    with pytest.raises(ValueError):
        run.start("a")


def test_graph_integrity_and_static_input_validation():
    suite = definition()
    for operation in [lambda: suite.node("a"), lambda: suite.node("c", parent_id="missing"),
                      lambda: suite.edge("cycle", "a", "choice"),
                      lambda: suite.edge("bad", "a", "missing"),
                      lambda: suite.edge("a-path", "a", "b"),
                      lambda: suite.node("bad", attributes={1: "key"}),
                      lambda: suite.node("bad", attributes={"secret": object()})]:
        with pytest.raises(ValueError):
            operation()
    bad = suite.snapshot()
    bad["records"][0]["status"] = "passed"
    with pytest.raises(ValueError):
        Suite.from_dict(bad)
    with pytest.raises(ValueError):
        Suite.from_dict({"schemaVersion": "future/v2", "kind": "suite"})


def test_snapshots_events_and_callback_are_detached():
    def mutate(event):
        event.clear()
    run = definition().run("r", on_event=mutate)
    run.finish("a", "skipped")
    run.finish("b", "unknown")
    snapshot = run.snapshot()
    snapshot["records"][0]["status"] = "failed"
    events = run.events()
    events.clear()
    assert run.snapshot()["records"][0]["status"] == "not_run"
    assert len(run.events()) == 3
    assert all(e["selected"] is None for e in run.snapshot()["edges"])


def test_callback_failure_records_once_and_propagates():
    observed = []
    def consume(event):
        observed.append(event)
        if event["type"] == "node.updated":
            raise RuntimeError("consumer failed")
    run = definition().run("r", on_event=consume)
    with pytest.raises(RuntimeError, match="consumer failed"):
        run.start("a")
    assert run.snapshot()["records"][1]["status"] == "running"
    assert len(run.events()) == 2
    assert len(observed) == 2


def test_live_metrics_preserve_prior_measurements_and_validate_atomically():
    run = definition().run("r")
    with pytest.raises(ValueError):
        run.record("a", metrics={"steps": 1})
    run.start("a")
    run.record("a", metrics={"steps": 1}, attributes={"phase": "warmup"})
    before = run.snapshot()
    with pytest.raises(ValueError):
        run.record("a", metrics={"steps": 2}, attributes={"bad": set()})
    assert run.snapshot() == before
    run.record("a", metrics={"steps": 2})
    run.finish("a", "passed", metrics={"durationMs": 0})
    assert run.snapshot()["records"][1]["metrics"] == {"steps": 2, "durationMs": 0}
    assert run.events()[-2]["record"]["status"] == "running"
