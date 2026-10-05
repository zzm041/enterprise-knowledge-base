"""Read-only metadata probes; no loading models or inference."""

import json
from pathlib import Path

import httpx

ROOT = Path(__file__).resolve().parents[1]
results = []
with httpx.Client(timeout=2, trust_env=False, follow_redirects=False) as client:
    for port in (1234, 8081, 8082, 8001):
        try:
            r = client.get(f"http://127.0.0.1:{port}/v1/models")
            if r.status_code == 200:
                data = r.json()
                names = [
                    x.get("id") for x in data.get("data", []) if isinstance(x, dict)
                ]
                results.append({"port": port, "status": 200, "models": names})
            else:
                results.append({"port": port, "status": r.status_code})
        except (httpx.HTTPError, ValueError):
            results.append({"port": port, "status": "unreachable_or_invalid"})
(ROOT / "reports" / "local-model-probe.json").write_text(
    json.dumps(results, indent=2), encoding="utf8"
)
print(json.dumps(results))
