"""Evaluate existing 23AB transcripts with the isolated 23AC resolver."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
import statistics
import sys
import time

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from scripts.semantic_resolver_23ac import Catalog, Meaning, parse, resolve


SOURCE = ROOT / "data/voice-diagnostics/sprint_23ab_20260925_174300/benchmark.json"
OUTPUT = ROOT / "data/voice-diagnostics/sprint_23ac_semantic_results.json"
PORTABLE = ROOT / "docs/sprint-23ac-semantic-matrix.json"
PHRASE_8_INFERRED = "Diminua o volume, por favor."


def preserved(expected: Meaning, actual: Meaning) -> dict[str, bool]:
    result = {
        "intent": actual.intent == expected.intent,
        "action": actual.action == expected.action,
        "target": actual.target == expected.target,
        "parameters": actual.parameters == expected.parameters,
        "negation": actual.negated == expected.negated,
    }
    result["semantic"] = all(result.values()) and not actual.ambiguous
    return result


def score_run(rows: list[dict], catalog: Catalog) -> tuple[list[dict], dict]:
    results = []
    for row in rows:
        expected = parse(row["reference"])
        actual = resolve(row["text"], catalog)
        if expected.intent is None:
            raise RuntimeError(f"Ground truth not covered by generic grammar: {row['id']}")
        comparison = preserved(expected, actual.meaning)
        expected_outcome = resolve(row["reference"], catalog).outcome
        times = [resolve(row["text"], catalog).elapsed_ms for _ in range(100)]
        tool_risk = (catalog.tools[actual.tool].risk if actual.tool else None)
        potentially_dangerous = bool(
            actual.outcome == "execute" and (
                not comparison["semantic"] or tool_risk not in {"SAFE", "LOW"}
            )
        )
        results.append({
            "id": row["id"], "category": row["category"], "sha256": row["sha256"],
            "scripted_reference": row["reference"], "transcript": row["text"],
            "expected": expected.as_dict(), "resolution": actual.as_dict(),
            "preserved": comparison,
            "expected_outcome_from_script": expected_outcome,
            "correct_rejection": actual.outcome == "reject" and expected_outcome == "reject",
            "potentially_dangerous_execute": potentially_dangerous,
            "resolver_median_ms_100_replays": statistics.median(times),
        })
    total = len(results)
    aggregate = {
        "count": total,
        "intent_correct": sum(item["preserved"]["intent"] for item in results),
        "action_correct": sum(item["preserved"]["action"] for item in results),
        "target_correct": sum(item["preserved"]["target"] for item in results),
        "parameters_correct": sum(item["preserved"]["parameters"] for item in results),
        "slots_correct": sum(item["preserved"]["target"] and item["preserved"]["parameters"]
                             for item in results),
        "negation_preserved": sum(item["preserved"]["negation"] for item in results),
        "semantic_preserved": sum(item["preserved"]["semantic"] for item in results),
        "execute": sum(item["resolution"]["outcome"] == "execute" for item in results),
        "confirm": sum(item["resolution"]["outcome"] == "confirm" for item in results),
        "reject": sum(item["resolution"]["outcome"] == "reject" for item in results),
        "correct_rejections": sum(item["correct_rejection"] for item in results),
        "potentially_dangerous_executions": sum(item["potentially_dangerous_execute"] for item in results),
        "resolver_median_ms": statistics.median(item["resolver_median_ms_100_replays"] for item in results),
        "resolver_max_ms": max(item["resolver_median_ms_100_replays"] for item in results),
    }
    aggregate["semantic_preservation_rate"] = aggregate["semantic_preserved"] / total
    return results, aggregate


def main() -> None:
    raw_bytes = SOURCE.read_bytes()
    raw = json.loads(raw_bytes)
    started = time.perf_counter()
    catalog = Catalog.from_checkout()
    catalog_load_ms = (time.perf_counter() - started) * 1000
    runs = []
    for run in raw["runs"]:
        if run["error"] or run["exit_code"] or len(run["rows"]) != 15:
            raise RuntimeError(f"Incomplete 23AB input: {run['model']} run {run['run']}")
        rows, aggregate = score_run(run["rows"], catalog)
        runs.append({"model": run["model"], "run": run["run"],
                     "aggregate": aggregate, "rows": rows})
    # Courtesy is the only known user addition; it does not change the parsed
    # action, target, number or negation in either phrase-8 scenario.
    scripted = parse(next(row for row in raw["runs"][0]["rows"] if row["id"] == "phrase_08")["reference"])
    inferred = parse(PHRASE_8_INFERRED)
    scenario_same = scripted == inferred
    if not scenario_same:
        raise RuntimeError("Phrase-8 courtesy changed semantic ground truth")
    payload = {
        "source": str(SOURCE), "source_sha256": hashlib.sha256(raw_bytes).hexdigest(),
        "catalog": catalog.snapshot(), "catalog_load_ms": catalog_load_ms,
        "phrase_8_scenarios": {
            "scripted": "Diminua o volume.",
            "inferred_not_confirmed": PHRASE_8_INFERRED,
            "user_could_not_identify_phrase": True,
            "semantic_ground_truth_equivalent": scenario_same,
        },
        "runs": runs,
        "policy": "diagnostic_only_no_tool_execution",
    }
    OUTPUT.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    portable = {
        "source_sha256": payload["source_sha256"],
        "catalog_source_sha256": payload["catalog"]["source_sha256"],
        "enabled_tools": [tool["name"] for tool in payload["catalog"]["tools"] if tool["enabled"]],
        "allowed_desktop_applications": payload["catalog"]["allowed_desktop_applications"],
        "phrase_8_scenarios": payload["phrase_8_scenarios"],
        "runs": [
            {"model": run["model"], "run": run["run"], "aggregate": run["aggregate"],
             "rows": [
                 {"id": row["id"], "category": row["category"],
                  "reference": row["scripted_reference"], "transcript": row["transcript"],
                  "expected": row["expected"], "resolution": row["resolution"],
                  "preserved": row["preserved"], "correct_rejection": row["correct_rejection"],
                  "potentially_dangerous_execute": row["potentially_dangerous_execute"]}
                 for row in run["rows"]
             ] if run["run"] == 1 else []}
            for run in runs
        ],
    }
    PORTABLE.write_text(json.dumps(portable, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(f"Wrote {OUTPUT}; {len(runs)} runs")


if __name__ == "__main__":
    main()
