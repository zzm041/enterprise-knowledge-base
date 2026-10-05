import sqlite3

import pytest

from ekb.auth import Principal
from ekb.catalog import Catalog
from ekb.errors import KBError
from ekb.text import chunks, normalize

OWNER = Principal("editor", "acme", {"handbook": "editor"}, ("all", "internal"))
READER = Principal("reader", "acme", {"handbook": "reader"}, ("all",))
OTHER = Principal("other", "other", {"handbook": "editor"}, ("all", "internal"))


@pytest.fixture
def catalog(tmp_path):
    return Catalog(tmp_path / "knowledge.db")


def create(c, text="Annual leave requires two days notice.", audience="all"):
    return c.create(OWNER, "handbook", "Leave guide", text, audience)


def test_normalization_chunk_offsets_and_input_guards():
    text = normalize("a.md", "  甲乙丙丁\r\n\r\nLeave guide.  ")
    pieces = chunks(text, size=8, overlap=2)
    assert pieces and all(text[x["start"] : x["end"]] == x["text"] for x in pieces)
    assert chunks(text, size=8, overlap=2) == pieces
    for filename, body in [("a.pdf", "x"), ("a.txt", " "), ("a.md", "bad\x00text")]:
        with pytest.raises(KBError):
            normalize(filename, body)
    with pytest.raises(ValueError):
        chunks(text, size=4, overlap=4)


def test_lifecycle_permissions_versions_and_archive(catalog):
    d = create(catalog)
    assert catalog.eligible(READER, "handbook") == []
    assert catalog.ingest(OWNER, d["id"], 1)["status"] == "ACTIVE"
    assert catalog.ingest(OWNER, d["id"], 1)["status"] == "ACTIVE"
    visible = catalog.eligible(READER, "handbook")
    assert len(visible) == 1 and visible[0]["version"] == 1
    for actor in [READER, OTHER]:
        with pytest.raises(KBError):
            catalog.add_version(actor, d["id"], "New policy", 1)
    assert catalog.eligible(OTHER, "handbook") == []
    create(catalog, "Internal review requires board approval.", "internal")
    internal = catalog.list_documents(OWNER, "handbook")[-1]
    catalog.ingest(OWNER, internal["id"], 1)
    assert len(catalog.eligible(READER, "handbook")) == 1
    catalog.add_version(OWNER, d["id"], "Annual leave requires five days notice.", 1)
    assert catalog.eligible(READER, "handbook")[0]["version"] == 1
    with pytest.raises(KBError) as conflict:
        catalog.add_version(OWNER, d["id"], "stale", 1)
    assert conflict.value.code == "KB.CONFLICT"
    catalog.ingest(OWNER, d["id"], 2)
    assert catalog.eligible(READER, "handbook")[0]["version"] == 2
    catalog.archive(OWNER, d["id"], 2)
    assert catalog.eligible(READER, "handbook") == []
    catalog.add_version(OWNER, d["id"], "Annual leave requires three days notice.", 2)
    catalog.ingest(OWNER, d["id"], 3)
    assert catalog.eligible(READER, "handbook")[0]["version"] == 3
    assert Catalog(catalog.path).detail(OWNER, d["id"])["current_version"] == 3


def test_failed_embedding_preserves_last_active_and_retry(catalog):
    d = create(catalog)
    catalog.ingest(OWNER, d["id"], 1)
    catalog.add_version(OWNER, d["id"], "Updated notice", 1)

    class Broken:
        fingerprint = "test-model:2"

        def embed(self, texts):
            raise KBError("KB.PROVIDER_UNAVAILABLE", 503)

    with pytest.raises(KBError):
        catalog.ingest(OWNER, d["id"], 2, Broken())
    detail = catalog.detail(OWNER, d["id"])
    assert detail["current_version"] == 1
    assert detail["versions"][-1]["status"] == "FAILED"
    catalog.ingest(OWNER, d["id"], 2)
    assert catalog.detail(OWNER, d["id"])["current_version"] == 2


def test_archive_during_embedding_cannot_publish(catalog):
    d = create(catalog)

    class Interleaved:
        fingerprint = "test-model:2"

        def embed(self, texts):
            catalog.archive(OWNER, d["id"], 1)
            return [[1.0, 0.0] for _ in texts]

    with pytest.raises(KBError):
        catalog.ingest(OWNER, d["id"], 1, Interleaved())
    assert catalog.eligible(OWNER, "handbook") == []
    assert catalog.detail(OWNER, d["id"])["status"] == "ARCHIVED"


def test_newer_revision_fences_older_inflight(catalog):
    d = create(catalog)

    class Interleaved:
        fingerprint = "test-model:2"

        def embed(self, texts):
            catalog.add_version(OWNER, d["id"], "Newer policy", 1)
            return [[1.0, 0.0] for _ in texts]

    with pytest.raises(KBError):
        catalog.ingest(OWNER, d["id"], 1, Interleaved())
    assert catalog.eligible(OWNER, "handbook") == []
    catalog.ingest(OWNER, d["id"], 2)
    assert catalog.detail(OWNER, d["id"])["current_version"] == 2


def test_hidden_document_is_404_and_audit_has_no_content(catalog):
    d = create(catalog)
    with pytest.raises(KBError) as error:
        catalog.detail(OTHER, d["id"])
    assert error.value.status == 404
    with sqlite3.connect(catalog.path) as conn:
        audit = conn.execute("select action, request_id from audit").fetchall()
        assert audit[0][0] == "created"


def test_chunk_capacity_rejection_preserves_old_revision(catalog):
    d = create(catalog)
    catalog.ingest(OWNER, d["id"], 1)
    catalog.add_version(OWNER, d["id"], "\n\n".join(["entry"] * 5001), 1)
    with pytest.raises(KBError) as error:
        catalog.ingest(OWNER, d["id"], 2)
    assert error.value.code == "KB.CORPUS_LIMIT"
    assert catalog.detail(OWNER, d["id"])["current_version"] == 1
    assert len(catalog.eligible(READER, "handbook")) == 1


def test_workspace_capacity_is_checked_across_documents(catalog):
    bulk = create(catalog, "\n\n".join(["entry"] * 4999))
    catalog.ingest(OWNER, bulk["id"], 1)
    d = create(catalog)
    catalog.ingest(OWNER, d["id"], 1)
    catalog.add_version(OWNER, d["id"], "first\n\nsecond", 1)
    with pytest.raises(KBError) as error:
        catalog.ingest(OWNER, d["id"], 2)
    assert error.value.code == "KB.CORPUS_LIMIT"
    assert len(catalog.eligible(READER, "handbook")) == 5000
    assert catalog.detail(OWNER, d["id"])["current_version"] == 1


def test_embedding_ingestion_is_batched_and_publishes_all_chunks(catalog):
    calls = []

    class BoundedEmbed:
        fingerprint = "test:1024"

        def embed(self, texts):
            calls.append(len(texts))
            assert len(texts) <= 8
            return [[0.25] * 1024 for _ in texts]

    d = create(catalog, "A policy passage. " * 1000)
    catalog.ingest(OWNER, d["id"], 1, BoundedEmbed())
    assert len(calls) > 1 and max(calls) <= 8
    assert len(catalog.eligible(READER, "handbook")) == sum(calls)
