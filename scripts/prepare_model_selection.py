"""Prepare identical synthetic prompts; this command performs NO inference."""

import hashlib
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from ekb.providers import SYSTEM


def prepare():
    corpus_bytes = (ROOT / "evaluation" / "corpus.json").read_bytes()
    cases_bytes = (ROOT / "evaluation" / "model-selection-cases.json").read_bytes()
    corpus = {item["key"]: item for item in json.loads(corpus_bytes)}
    cases = json.loads(cases_bytes)
    models = {
        "candidate_4b": {
            "endpoint": "http://127.0.0.1:11438/v1/chat/completions",
            "tag": "qwen3.5:4b",
            "configured_context": 4096,
        },
        "candidate_27b": {
            "endpoint": "http://127.0.0.1:11435/v1/chat/completions",
            "tag": "qwen38-27b-q3km-g55-ctx8192:7af3b8f",
            "configured_context": 8192,
        },
    }
    requests = []
    for case in cases:
        evidence = [
            {"chunk_id": i + 1, "document_key": key, "text": corpus[key]["text"]}
            for i, key in enumerate(case["sources"])
        ]
        if case.get("extra_evidence"):
            evidence.append(
                {
                    "chunk_id": len(evidence) + 1,
                    "document_key": "untrusted-annotation",
                    "text": case["extra_evidence"],
                }
            )
        evidence_json = json.dumps(evidence, ensure_ascii=False, sort_keys=True)
        messages = [
            {"role": "system", "content": SYSTEM},
            {
                "role": "user",
                "content": json.dumps(
                    {"question": case["question"], "evidence": evidence},
                    ensure_ascii=False,
                ),
            },
        ]
        for candidate, model in models.items():
            requests.append(
                {
                    "case_id": case["id"],
                    "candidate": candidate,
                    "endpoint": model["endpoint"],
                    "evidence_sha256": hashlib.sha256(
                        evidence_json.encode()
                    ).hexdigest(),
                    "body": {
                        "model": model["tag"],
                        "messages": messages,
                        "temperature": 0,
                        "reasoning_effort": "none",
                        "max_tokens": 512,
                        "response_format": {"type": "json_object"},
                    },
                }
            )
    return {
        "status": "PREPARED_NOT_RUN",
        "scores": None,
        "requires_guarded_execution": True,
        "warning": "Native URLs are targets, not permission to bypass shared model protection.",
        "design": "controlled gold evidence; NOT end-to-end retrieval evaluation",
        "corpus_sha256": hashlib.sha256(corpus_bytes).hexdigest(),
        "cases_sha256": hashlib.sha256(cases_bytes).hexdigest(),
        "models": models,
        "cases": cases,
        "requests": requests,
    }


if __name__ == "__main__":
    target = ROOT / "reports" / "model-selection-manifest.json"
    target.write_text(
        json.dumps(prepare(), ensure_ascii=False, indent=2), encoding="utf8"
    )
    print("Prepared 24 paired requests; no inference executed. " + str(target))
