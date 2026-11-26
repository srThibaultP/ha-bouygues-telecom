"""Print the requirements of the components the tests load."""

import json
import pathlib

components = pathlib.Path("ha-core/homeassistant/components")
todo = ["diagnostics", "http"]
seen: set[str] = set()
requirements: set[str] = set()
while todo:
    name = todo.pop()
    if name in seen:
        continue
    seen.add(name)
    manifest = json.loads((components / name / "manifest.json").read_text())
    requirements.update(manifest.get("requirements", []))
    todo += manifest.get("dependencies", []) + manifest.get("after_dependencies", [])
print(" ".join(sorted(requirements)))
