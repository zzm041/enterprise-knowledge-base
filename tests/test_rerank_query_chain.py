"""Positive rerank evidence through the real FastAPI query route.

Only the remote reranker transport is deterministic; authorization, catalog,
retrieval, rerank application, answer selection and citations are application code.
"""

import hashlib
import json
import os
from pathlib import Path

import httpx
from fastapi.testclient import TestClient

from ekb.api import create_app
from ekb.auth import Principal
from ekb.config import Config
from ekb.providers import ModelHTTP


def test_rerank_changes_query_order_without_crossing_scopes(tmp_path):
    actors = {
        "owner_a": Principal(
            "owner-a",
            "tenant-a",
            {"handbook": "editor", "restricted": "editor"},
            ("all", "internal"),
        ),
        "reader_a": Principal("reader-a", "tenant-a", {"handbook": "reader"}, ("all",)),
        "internal_a": Principal(
            "internal-a", "tenant-a", {"handbook": "reader"}, ("all", "internal")
        ),
        "owner_b": Principal("owner-b", "tenant-b", {"handbook": "editor"}, ("all",)),
        "reader_b": Principal("reader-b", "tenant-b", {"handbook": "reader"}, ("all",)),
    }
    credentials = {
        hashlib.sha256(f"synthetic-{name}".encode()).hexdigest(): actor
        for name, actor in actors.items()
    }
    conf = Config(tmp_path / "rerank-query-chain.db", credentials)
    calls = []

    def ranked_response(request):
        body = json.loads(request.content)
        assert request.url.path == "/v1/rerank"
        documents = body["documents"]
        calls.append(documents)
        scores = [
            0.99 if "PUBLIC_BRAVO" in item else 0.5 if "INTERNAL_ONLY" in item else 0.01
            for item in documents
        ]
        return httpx.Response(
            200,
            json={
                "results": [
                    {"index": index, "relevance_score": scores[index]}
                    for index in reversed(range(len(documents)))
                ]
            },
        )

    reranker = ModelHTTP(
        "http://127.0.0.1:9/v1",
        "deterministic-rerank-fixture",
        client=httpx.Client(transport=httpx.MockTransport(ranked_response)),
    )

    def headers(name):
        return {"Authorization": f"Bearer synthetic-{name}"}

    def query(client, name, workspace="handbook"):
        return client.post(
            "/query",
            headers=headers(name),
            json={"workspace": workspace, "question": "remote access policy"},
        )

    with TestClient(create_app(conf)) as client:

        def add(owner, workspace, title, text, audience="all"):
            created = client.post(
                "/documents",
                headers=headers(owner),
                json={
                    "workspace": workspace,
                    "title": title,
                    "text": text,
                    "audience": audience,
                },
            )
            assert created.status_code == 201
            document_id = created.json()["id"]
            ingested = client.post(
                f"/documents/{document_id}/versions/1/ingest", headers=headers(owner)
            )
            assert ingested.status_code == 200
            return document_id

        alpha = add(
            "owner_a",
            "handbook",
            "Alpha",
            "Remote access policy requires approved device. PUBLIC_ALPHA",
        )
        bravo = add(
            "owner_a",
            "handbook",
            "Bravo",
            "Remote access policy requires approved device. PUBLIC_BRAVO",
        )
        internal = add(
            "owner_a",
            "handbook",
            "Internal",
            "Remote access policy requires security review. INTERNAL_ONLY",
            "internal",
        )
        other_workspace = add(
            "owner_a",
            "restricted",
            "Restricted",
            "Remote access policy requires special review. OTHER_WORKSPACE",
        )
        other_tenant = add(
            "owner_b",
            "handbook",
            "Other tenant",
            "Remote access policy requires separate approval. OTHER_TENANT",
        )
        plain = query(client, "reader_a")
        assert plain.status_code == 200
        baseline = plain.json()
        assert baseline["status"] == "answered"
        assert baseline["trace"]["ranked_document_ids"][:2] == [alpha, bravo]

    conf.reranking = {"model": "deterministic-rerank-fixture"}
    conf.provider = lambda settings: reranker if settings else None
    with TestClient(create_app(conf)) as client:
        ranked_response_a = query(client, "reader_a")
        assert ranked_response_a.status_code == 200
        ranked = ranked_response_a.json()
        assert ranked["status"] == "answered"
        assert ranked["trace"]["rerank"] == "applied"
        assert ranked["trace"]["ranked_document_ids"][:2] == [bravo, alpha]
        assert (
            ranked["answer"]
            == "Remote access policy requires approved device. PUBLIC_BRAVO"
        )
        assert len(ranked["citations"]) == 1
        assert ranked["citations"][0]["document_id"] == bravo
        assert ranked["citations"][0]["title"] == "Bravo"
        assert ranked["citations"][0]["version"] == 1
        assert ranked["citations"][0]["excerpt"] == ranked["answer"]
        assert ranked["trace"]["permission_recheck"] == "passed"
        assert len(calls[0]) == 2
        assert all(
            "INTERNAL_ONLY" not in text
            and "OTHER_WORKSPACE" not in text
            and "OTHER_TENANT" not in text
            for text in calls[0]
        )
        assert not {internal, other_workspace, other_tenant}.intersection(
            ranked["trace"]["ranked_document_ids"]
        )

        allowed_internal = query(client, "internal_a").json()
        assert allowed_internal["trace"]["rerank"] == "applied"
        assert any("INTERNAL_ONLY" in text for text in calls[1])
        assert all(
            "OTHER_WORKSPACE" not in text and "OTHER_TENANT" not in text
            for text in calls[1]
        )

        forbidden_workspace = query(client, "reader_a", "restricted")
        assert forbidden_workspace.status_code == 403
        assert forbidden_workspace.json()["error"]["code"] == "KB.FORBIDDEN"
        assert len(calls) == 2

        tenant_b = query(client, "reader_b").json()
        assert tenant_b["status"] == "answered"
        assert tenant_b["trace"]["rerank"] == "applied"
        assert tenant_b["citations"][0]["document_id"] == other_tenant
        assert len(calls[2]) == 1 and "OTHER_TENANT" in calls[2][0]

    evidence = {
        "test": "tests/test_rerank_query_chain.py::test_rerank_changes_query_order_without_crossing_scopes",
        "baseline_document_order": [alpha, bravo],
        "reranked_document_order": ranked["trace"]["ranked_document_ids"],
        "citation_document_id": ranked["citations"][0]["document_id"],
        "tenant_a_public_candidate_count": len(calls[0]),
        "tenant_a_internal_candidate_count": len(calls[1]),
        "tenant_b_candidate_count": len(calls[2]),
        "forbidden_workspace_status": forbidden_workspace.status_code,
        "transport": "HTTP MockTransport, no model service",
        "result": "PASS",
    }
    report = os.environ.get("EKB_RERANK_REPORT")
    if report:
        root = Path(__file__).resolve().parents[1]
        path = Path(report).resolve()
        assert path.is_relative_to(root / "reports")
        path.write_text(json.dumps(evidence, indent=2) + "\n", encoding="utf8")
    print("RERANK_QUERY_CHAIN_EVIDENCE=" + json.dumps(evidence, sort_keys=True))
