"""CPU-only demonstration; all outputs stay local until explicitly uploaded."""
from pathlib import Path
from dreamlake.cict import Suite

out = Path(__file__).parent / "outputs"
suite = (Suite("numpy-smoke")
         .node("choose-size", kind="decision", attributes={"suite": "numpy", "device": "cpu"})
         .node("small", attributes={"suite": "numpy", "device": "cpu", "family": "smoke"})
         .node("large", attributes={"suite": "numpy", "device": "cpu", "family": "stress"})
         .edge("use-small", "choose-size", "small", condition="quick run")
         .edge("use-large", "choose-size", "large", condition="extended run"))
suite.write(out / "cict-suite.json")
# on_event can feed a caller-owned live graph; it does not upload by itself.
run = suite.run("local-example")
run.select("choose-size", "use-small")
with run.test("small"):
    assert sum([1, 2, 3]) == 6
    run.record("small", metrics={"durationMs": 0}, attributes={"samples": 3})
# large remains not_run, and use-large selected:false, rather than guessed passed.
run.write(out / "cict.json")
run.write_events(out / "cict-events.jsonl")
