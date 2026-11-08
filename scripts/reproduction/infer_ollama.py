"""One neutral host Ollama prompt; no Tool definitions or runtime execution."""
import argparse
import json
from pathlib import Path
import time
import urllib.request


def request(path: str, payload: dict | None = None) -> dict:
    data = None if payload is None else json.dumps(payload).encode("utf-8")
    req = urllib.request.Request("http://127.0.0.1:11434/api/" + path,
                                 data=data, headers={"Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=30) as response:
        return json.load(response)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--manifest", type=Path, required=True)
    args = parser.parse_args()
    expected = json.loads(args.manifest.read_text(encoding="utf-8"))["qwen"]
    actual = next((x for x in request("tags")["models"] if x["name"] == expected["tag"]), None)
    if actual is None or actual["digest"].removeprefix("sha256:") != expected["manifest_sha256"]:
        print(json.dumps({"status": "blocked_model_identity"}))
        return 1
    start = time.perf_counter()
    result = request("generate", {
        "model": expected["tag"], "prompt": "Responda somente com duas palavras: teste local",
        "stream": False, "options": {"temperature": 0, "seed": 0, "num_predict": 32},
    })
    print(json.dumps({"status": "passed" if result.get("done") else "failed",
                      "response": result.get("response"), "seconds": time.perf_counter()-start,
                      "eval_count": result.get("eval_count"),
                      "total_duration_ns": result.get("total_duration"),
                      "tools_sent": False, "host_service_reused": True}))
    return int(not result.get("done"))


if __name__ == "__main__":
    raise SystemExit(main())
