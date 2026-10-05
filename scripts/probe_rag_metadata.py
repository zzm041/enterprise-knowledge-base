import json
from pathlib import Path

import httpx

root = Path(__file__).resolve().parents[1]
results = {}
with httpx.Client(timeout=3, trust_env=False, follow_redirects=False) as client:
    for route in ("/api/version", "/api/tags", "/api/ps"):
        try:
            r = client.get("http://127.0.0.1:11438" + route)
            row = {"status": r.status_code}
            if r.status_code == 200:
                data = r.json()
                row.update(
                    {"version": data["version"]}
                    if route == "/api/version"
                    else {
                        "model_names": [
                            m.get("name", m.get("model"))
                            for m in data.get("models", [])
                        ]
                    }
                )
            results[route] = row
        except (httpx.HTTPError, ValueError):
            results[route] = {"status": "unreachable_or_invalid"}
    try:
        results["adapter_health_without_credentials"] = {
            "status": client.get("http://127.0.0.1:11436/healthz").status_code
        }
    except httpx.HTTPError:
        results["adapter_health_without_credentials"] = {"status": "unreachable"}
(root / "reports" / "rag-runtime-metadata.json").write_text(
    json.dumps(results, indent=2), encoding="utf8"
)
print(json.dumps(results))
