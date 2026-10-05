import pytest

from ekb.auth import Principal
from ekb.catalog import Catalog
from ekb.errors import KBError
from ekb.retrieval import Engine, bm25, rrf

OWNER = Principal("editor", "acme", {"kb": "editor"}, ("all", "internal"))
READER = Principal("reader", "acme", {"kb": "reader"}, ("all",))


@pytest.fixture
def setup(tmp_path):
    c = Catalog(tmp_path / "db")
    for title, text, aud in [
        ("Leave", "Annual leave requires two days notice.", "all"),
        ("VPN", "VPN access requires device approval.", "internal"),
    ]:
        d = c.create(OWNER, "kb", title, text, aud)
        c.ingest(OWNER, d["id"], 1)
    return c


def test_bm25_and_rrf_are_deterministic():
    assert bm25("leave notice", ["VPN access", "Annual leave notice"])[1] > 0
    assert bm25("zzzz", ["Annual leave"]) == [0.0]
    assert rrf([[2, 1], [1, 2]]) == rrf([[1, 2], [2, 1]])


def test_query_has_exact_citation_and_trace(setup):
    result = Engine(setup).query(READER, "kb", "annual leave notice")
    assert result["status"] == "answered"
    assert result["answer_mode"] == "extractive"
    assert "two days" in result["answer"]
    assert result["citations"][0]["title"] == "Leave"
    assert result["trace"]["retrieval_mode"] == "bm25"
    assert (
        Engine(setup).query(READER, "kb", "quantum spaceship")["status"]
        == "insufficient_knowledge"
    )
    assert (
        Engine(setup).query(READER, "kb", "VPN device approval")["status"]
        == "insufficient_knowledge"
    )


def test_unknown_citations_and_schema_are_rejected(setup):
    class Bad:
        mode = "test-double"

        def generate(self, question, evidence):
            return {
                "status": "answered",
                "answer": "Unsupported claim",
                "citation_chunk_ids": [evidence[0]["id"], 999],
            }

    result = Engine(setup, generator=Bad()).query(READER, "kb", "leave notice")
    assert result["status"] == "knowledge_unavailable"
    assert result["answer"] == "" and result["citations"] == []


def test_archive_during_generation_invalidates_answer(setup):
    class Archive:
        mode = "test-double"

        def generate(self, question, evidence):
            setup.archive(OWNER, evidence[0]["document_id"], 1)
            return {
                "status": "answered",
                "answer": "two days",
                "citation_chunk_ids": [evidence[0]["id"]],
            }

    result = Engine(setup, generator=Archive()).query(READER, "kb", "leave notice")
    assert result["status"] == "insufficient_knowledge"
    assert result["citations"] == []


def test_generator_timeout_and_rerank_failure_are_labeled(setup):
    class Bad:
        mode = "test-double"

        def generate(self, *args):
            raise KBError("KB.PROVIDER_TIMEOUT", 503)

    assert (
        Engine(setup, generator=Bad()).query(READER, "kb", "leave")["status"]
        == "knowledge_unavailable"
    )

    class BadRanker:
        def rerank(self, *args):
            raise KBError("KB.PROVIDER_TIMEOUT", 503)

    r = Engine(setup, reranker=BadRanker()).query(READER, "kb", "leave")
    assert r["status"] == "answered"
    assert r["trace"]["rerank"] == "unavailable_kept_original_order"


def test_dense_requires_matching_real_vector_contract(setup):
    class Embed:
        fingerprint = "tiny-test-vector:2"

        def embed(self, texts):
            return [[1.0, 0.0] for _ in texts]

    with pytest.raises(KBError) as e:
        Engine(setup, embedder=Embed()).query(READER, "kb", "leave", mode="dense")
    assert e.value.code == "KB.INDEX_MISMATCH"
    d = setup.create(OWNER, "kb", "Dense only", "Dense test content")
    setup.ingest(OWNER, d["id"], 1, Embed())
    setup.archive(OWNER, 1, 1)
    result = Engine(setup, embedder=Embed()).query(
        READER, "kb", "content", mode="hybrid"
    )
    assert result["citations"][0]["title"] == "Dense only"


@pytest.mark.parametrize(
    "question",
    [
        "What is the launch velocity of the lunar spacecraft?",
        "How many weeks of parental leave are available?",
        "What is the exact annual salary increase percentage?",
    ],
)
def test_extractive_refuses_unsupported_questions_with_incidental_overlap(
    setup, question
):
    result = Engine(setup).query(READER, "kb", question)
    assert result["status"] == "insufficient_knowledge"
