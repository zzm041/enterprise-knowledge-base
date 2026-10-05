import hashlib

from fastapi.testclient import TestClient

from ekb.api import create_app
from ekb.auth import Principal
from ekb.config import Config


def test_api_authorization_lifecycle_and_redacted_validation(tmp_path):
    owner = Principal("owner", "acme", {"kb": "editor"}, ("all", "internal"))
    reader = Principal("reader", "acme", {"kb": "reader"}, ("all",))
    conf = Config(
        tmp_path / "db",
        {
            hashlib.sha256(b"synthetic-owner").hexdigest(): owner,
            hashlib.sha256(b"synthetic-reader").hexdigest(): reader,
        },
    )
    with TestClient(create_app(conf)) as client:
        assert client.get("/health").status_code == 200
        assert client.get("/documents", params={"workspace": "kb"}).status_code == 401
        h = {"Authorization": "Bearer synthetic-owner"}
        rh = {"Authorization": "Bearer synthetic-reader"}
        body = {
            "workspace": "kb",
            "title": "Leave",
            "text": "Annual leave requires two days notice.",
        }
        assert client.post("/documents", headers=rh, json=body).status_code == 403
        created = client.post("/documents", headers=h, json=body)
        assert created.status_code == 201
        did = created.json()["id"]
        assert (
            client.post(f"/documents/{did}/versions/1/ingest", headers=h).status_code
            == 200
        )
        answer = client.post(
            "/query", headers=rh, json={"workspace": "kb", "question": "leave notice"}
        )
        assert answer.json()["status"] == "answered"
        assert answer.headers["X-Request-ID"]
        bad = client.post(
            "/query",
            headers=rh,
            json={"workspace": "kb", "question": "", "secret": "DO_NOT_ECHO"},
        )
        assert bad.status_code == 422 and "DO_NOT_ECHO" not in bad.text
        assert (
            client.post(
                "/query",
                headers=rh,
                json={"workspace": "elsewhere", "question": "leave"},
            ).status_code
            == 403
        )
        huge = client.post("/query", headers=rh, content=b"x" * 260_000)
        assert huge.status_code == 413
        assert (
            client.post(
                f"/documents/{did}/archive", headers=h, json={"expected_version": 1}
            ).status_code
            == 200
        )
        assert (
            client.post(
                "/query", headers=rh, json={"workspace": "kb", "question": "leave"}
            ).json()["status"]
            == "insufficient_knowledge"
        )


def test_config_rejects_empty_auth_and_outside_database(tmp_path):
    import json

    import pytest

    from ekb.config import load_config
    from ekb.errors import KBError

    p = tmp_path / "config.json"
    p.write_text(
        json.dumps({"database": "D:/property-ai/unsafe.db", "credentials": []}),
        encoding="utf8",
    )
    with pytest.raises(KBError):
        load_config(p)


def test_default_request_logging_excludes_source_and_token(tmp_path):
    import io
    import json
    import logging

    from ekb.api import logger

    actor = Principal("owner", "acme", {"kb": "editor"}, ("all",))
    token = "SYNTHETIC_DO_NOT_LOG_TOKEN"
    conf = Config(
        tmp_path / "logging.db", {hashlib.sha256(token.encode()).hexdigest(): actor}
    )
    stream = io.StringIO()
    handler = logging.StreamHandler(stream)
    logger.addHandler(handler)
    try:
        with TestClient(create_app(conf)) as client:
            response = client.post(
                "/documents",
                headers={"Authorization": "Bearer " + token},
                json={
                    "workspace": "kb",
                    "title": "note",
                    "text": "SYNTHETIC_DO_NOT_LOG_SOURCE",
                },
            )
        lines = [json.loads(x) for x in stream.getvalue().splitlines()]
        assert any(
            x.get("request_id") == response.headers["X-Request-ID"] for x in lines
        )
        assert (
            token not in stream.getvalue()
            and "SYNTHETIC_DO_NOT_LOG_SOURCE" not in stream.getvalue()
        )
    finally:
        logger.removeHandler(handler)


def test_oversized_request_uses_one_request_id(tmp_path):
    conf = Config(tmp_path / "large.db", {})
    with TestClient(create_app(conf)) as client:
        response = client.post("/query", content=b"x" * 260_000)
    assert response.status_code == 413
    assert response.json()["request_id"] == response.headers["X-Request-ID"]
