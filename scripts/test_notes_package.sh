#!/usr/bin/env bash
# Manual packaging regression: install a wheel outside the source checkout.
# Usage: bash scripts/test_notes_package.sh /absolute/path/to/dreamlake.whl
set -euo pipefail
wheel="$(python3 -c 'import pathlib,sys; print(pathlib.Path(sys.argv[1]).resolve())' "${1:?Supply a built wheel}")"
notes_test_dir="$(mktemp -d)"
trap 'rm -rf "$notes_test_dir"' EXIT
uv venv "$notes_test_dir/venv"
uv pip install --python "$notes_test_dir/venv/bin/python" "$wheel"
cd "$notes_test_dir"
"$notes_test_dir/venv/bin/python" - <<'PY'
from dreamlake.api.notes import Note
from dreamlake.api._jsregex import compile_js
assert callable(Note.read) and callable(Note.read_lines)
assert isinstance(Note.files, property)
pattern = compile_js(r"(?<=a+)\p{Script=Greek}+", "u")
assert pattern.search("aaλ").group() == "λ"
print("PASS: fresh wheel loads Notes and its required regex runtime outside source")
PY
