from ekb.evaluate import metrics


def test_metrics_known_ranks_duplicate_ids_and_negatives():
    m = metrics(
        [
            {"relevant": ["a"], "ranked": ["x", "a", "a"], "status": "answered"},
            {"relevant": ["b", "c"], "ranked": ["b", "x"], "status": "answered"},
            {"relevant": [], "ranked": [], "status": "insufficient_knowledge"},
            {"relevant": [], "ranked": ["x"], "status": "answered"},
        ],
        k=4,
    )
    assert m["recall_at_k"] == 0.75
    assert m["mrr_at_k"] == 0.75
    assert m["negative_refusal_rate"] == 0.5
    assert m["answerable_count"] == 2 and m["negative_count"] == 2
    assert 0 < m["ndcg_at_k"] < 1


def test_empty_metrics_do_not_claim_perfect_scores():
    m = metrics([], k=4)
    assert m["recall_at_k"] is None
    assert m["negative_refusal_rate"] is None
