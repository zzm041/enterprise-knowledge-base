"""Permission-scoped exact retrieval; scores are experiments, not authorization."""

import json
import math
import re
from collections import Counter
from time import perf_counter

from pydantic import ValidationError

from ekb.answer import AnswerOutput, Extractive
from ekb.errors import KBError

STOP_WORDS = frozenset(
    "a an the is are was were be been to of for and or in on at by with from as it its this that these those i my we our you your how what when where who which can could do does did should must many much me get have has may exact".split()
)


def tokens(text):
    lower = text.casefold()
    words = [w for w in re.findall(r"[a-z0-9]+", lower) if w not in STOP_WORDS]
    for run in re.findall(r"[\u3400-\u9fff]+", lower):
        words.extend(run[i : i + 2] for i in range(len(run) - 1))
        if len(run) == 1:
            words.append(run)
    return words


def bm25(query, documents):
    query_tokens = set(tokens(query))
    docs = [Counter(tokens(d)) for d in documents]
    n = len(docs)
    if not n:
        return []
    lengths = [sum(d.values()) for d in docs]
    avg = sum(lengths) / n or 1
    df = {t: sum(t in d for d in docs) for t in query_tokens}
    return [
        sum(
            math.log(1 + (n - df[t] + 0.5) / (df[t] + 0.5))
            * (d[t] * 2.5)
            / (d[t] + 1.5 * (0.25 + 0.75 * length / avg))
            for t in query_tokens
            if d[t]
        )
        for d, length in zip(docs, lengths)
    ]


def rrf(rankings, k=60):
    scores = {}
    for ranking in rankings:
        for rank, item in enumerate(ranking, 1):
            scores[item] = scores.get(item, 0) + 1 / (k + rank)
    return sorted(scores, key=lambda i: (-scores[i], i))


def cosine(a, b):
    if len(a) != len(b) or not a:
        raise KBError("KB.INDEX_MISMATCH", 409)
    norm = math.sqrt(sum(x * x for x in a) * sum(x * x for x in b))
    if not norm:
        raise KBError("KB.INDEX_MISMATCH", 409)
    return sum(x * y for x, y in zip(a, b, strict=True)) / norm


class Engine:
    def __init__(
        self,
        catalog,
        embedder=None,
        generator=None,
        reranker=None,
        top_k=4,
        candidate_k=12,
    ):
        self.catalog, self.embedder = catalog, embedder
        self.generator = generator or Extractive()
        self.reranker = reranker
        self.top_k, self.candidate_k = top_k, candidate_k
        self.min_lexical_coverage = 0.45

    def resolve_mode(self, mode):
        """Return the retrieval path a request really uses.

        "auto" is the honest default: a configured embedder makes the hybrid
        (RRF) path reachable, while a deployment without one can only do BM25.
        Resolving here keeps the strict modes strict -- asking for "dense" or
        "hybrid" without an embedder still fails instead of silently degrading.
        """
        if mode == "auto":
            return "hybrid" if self.embedder is not None else "bm25"
        return mode

    def query(self, actor, workspace, question, mode="auto"):
        start = perf_counter()
        if (
            mode not in ("auto", "bm25", "dense", "hybrid")
            or not question.strip()
            or len(question) > 2000
        ):
            raise KBError("KB.INVALID_INPUT", 422)
        requested, mode = mode, self.resolve_mode(mode)
        trace = {
            "retrieval_mode": mode,
            "requested_mode": requested,
            "rerank": "disabled",
            "eligible_chunks": 0,
            "ranked_chunk_ids": [],
        }

        def result(status, answer="", cites=None):
            trace["total_ms"] = round((perf_counter() - start) * 1000, 3)
            return {
                "status": status,
                "answer": answer,
                "answer_mode": self.generator.mode,
                "citations": cites or [],
                "trace": trace,
            }

        rows = self.catalog.eligible(actor, workspace)
        trace["eligible_chunks"] = len(rows)
        if not rows:
            return result("insufficient_knowledge")
        text_scores = bm25(question, [x["text"] for x in rows])
        query_terms = set(tokens(question))
        coverage = [
            len(query_terms.intersection(tokens(x["text"]))) / max(len(query_terms), 1)
            for x in rows
        ]
        trace["min_lexical_coverage"] = self.min_lexical_coverage
        trace["max_lexical_coverage"] = max(coverage, default=0)
        lexical = sorted(
            (
                i
                for i, s in enumerate(text_scores)
                if s > 0 and coverage[i] >= self.min_lexical_coverage
            ),
            key=lambda i: (-text_scores[i], rows[i]["id"]),
        )
        ranking = lexical
        if mode in ("dense", "hybrid"):
            if self.embedder is None:
                raise KBError("KB.EMBEDDING_NOT_CONFIGURED", 409)
            if any(
                x["fingerprint"] != self.embedder.fingerprint or x["vector"] is None
                for x in rows
            ):
                raise KBError("KB.INDEX_MISMATCH", 409)
            try:
                vector = self.embedder.embed([question])[0]
            except KBError as error:
                trace["error_code"] = error.code
                return result("knowledge_unavailable")
            dense_scores = [cosine(vector, json.loads(x["vector"])) for x in rows]
            dense = sorted(
                (i for i, s in enumerate(dense_scores) if s >= 0.25),
                key=lambda i: (-dense_scores[i], rows[i]["id"]),
            )
            ranking = (
                dense
                if mode == "dense"
                else rrf([lexical[: self.candidate_k], dense[: self.candidate_k]])
            )
            if mode == "hybrid":
                trace["fusion"] = "rrf"
                trace["lexical_candidates"] = len(lexical[: self.candidate_k])
                trace["dense_candidates"] = len(dense[: self.candidate_k])
        candidates = [rows[i] for i in ranking[: self.candidate_k]]
        if self.reranker and candidates:
            try:
                scores = self.reranker.rerank(question, [x["text"] for x in candidates])
                if len(scores) != len(candidates) or any(
                    not isinstance(s, (int, float))
                    or isinstance(s, bool)
                    or not math.isfinite(s)
                    for s in scores
                ):
                    raise KBError("KB.INVALID_PROVIDER_OUTPUT", 502)
                candidates = [
                    x
                    for x, _ in sorted(
                        zip(candidates, scores, strict=True), key=lambda pair: -pair[1]
                    )
                ]
                trace["rerank"] = "applied"
            except KBError:
                trace["rerank"] = "unavailable_kept_original_order"
        trace["ranked_chunk_ids"] = [x["id"] for x in candidates]
        trace["ranked_document_ids"] = [x["document_id"] for x in candidates]
        selected = candidates[: self.top_k]
        if not selected:
            return result("insufficient_knowledge")
        current = {x["id"] for x in self.catalog.eligible(actor, workspace)}
        if any(x["id"] not in current for x in selected):
            return result("insufficient_knowledge")
        try:
            parsed = AnswerOutput.model_validate(
                self.generator.generate(question, selected)
            )
            allowed = {x["id"] for x in selected}
            if any(x not in allowed for x in parsed.citation_chunk_ids):
                raise KBError("KB.INVALID_CITATION", 502)
            if parsed.status == "insufficient_knowledge":
                return result("insufficient_knowledge")
            if not parsed.answer.strip() or not parsed.citation_chunk_ids:
                raise KBError("KB.INVALID_CITATION", 502)
        except (KBError, ValidationError) as error:
            trace["error_code"] = (
                error.code
                if isinstance(error, KBError)
                else "KB.INVALID_PROVIDER_OUTPUT"
            )
            return result("knowledge_unavailable")
        current = {x["id"] for x in self.catalog.eligible(actor, workspace)}
        if any(x["id"] not in current for x in selected):
            trace["permission_recheck"] = "changed"
            return result("insufficient_knowledge")
        cited = [x for x in selected if x["id"] in parsed.citation_chunk_ids]
        citations = [
            {
                "chunk_id": x["id"],
                "document_id": x["document_id"],
                "title": x["title"],
                "version": x["version"],
                "start": x["start"],
                "end": x["end"],
                "excerpt": x["text"],
            }
            for x in cited
        ]
        trace["permission_recheck"] = "passed"
        return result("answered", parsed.answer, citations)
