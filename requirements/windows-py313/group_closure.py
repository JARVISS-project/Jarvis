"""Compute group closures from pip --report JSON; do not resolve/install/write."""
import json
from pathlib import Path
import sys
from packaging.markers import default_environment
from packaging.requirements import Requirement
from packaging.utils import canonicalize_name

root = Path(__file__).resolve().parent
report = json.loads(Path(sys.argv[1]).read_text(encoding="utf-8"))
packages = {canonicalize_name(x["metadata"]["name"]): x for x in report["install"]}
environment = default_environment()
assert environment["sys_platform"] == "win32" and environment["python_version"] == "3.13"
result = {}
for group in ("core", "voice", "ui", "media", "tests"):
    queue = [Requirement(line) for line in (root / (group + ".in")).read_text().splitlines()
             if line and not line.startswith("#")]
    seen = set()
    names = set()
    while queue:
        requirement = queue.pop()
        key = (canonicalize_name(requirement.name), tuple(sorted(requirement.extras)))
        if key in seen:
            continue
        seen.add(key)
        names.add(key[0])
        package = packages[key[0]]
        for value in package["metadata"].get("requires_dist", []):
            child = Requirement(value)
            if child.marker is None or any(
                child.marker.evaluate(dict(environment, extra=extra))
                for extra in ("", *requirement.extras)
            ):
                queue.append(child)
    result[group] = sorted(names)
print(json.dumps(result))
