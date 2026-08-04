"""eval.metrics 純函式測試——零 DB / 零 backend import。"""

import pytest

from eval.metrics import mean, mrr, ndcg_at_k, percentile, precision_at_k, reciprocal_rank

# --- precision_at_k -----------------------------------------------------------


def test_precision_at_k_basic():
    # 前 3 名命中 2 個 → 2/3
    assert precision_at_k(["a", "b", "c", "d"], {"a", "c", "z"}, 3) == pytest.approx(2 / 3)


def test_precision_at_k_denominator_is_k():
    # 相關數 (1) < K (5)：經典定義分母仍為 K → 上限 0.2
    assert precision_at_k(["a", "b", "c", "d", "e"], {"a"}, 5) == pytest.approx(1 / 5)


def test_precision_at_k_empty_relevant_is_zero():
    assert precision_at_k(["a", "b"], set(), 3) == 0.0


def test_precision_at_k_k_longer_than_ranking():
    # ranked 只有 2 個、K=5：命中 1 個 → 1/5
    assert precision_at_k(["a", "b"], {"b"}, 5) == pytest.approx(1 / 5)


def test_precision_at_k_duplicates_count_once():
    # "a" 重複出現：只計首次 → 前 3 名實際為 a, b, c
    assert precision_at_k(["a", "a", "b", "c"], {"a", "c"}, 3) == pytest.approx(2 / 3)


def test_precision_at_k_rejects_non_positive_k():
    with pytest.raises(ValueError):
        precision_at_k(["a"], {"a"}, 0)


# --- reciprocal_rank ----------------------------------------------------------


def test_reciprocal_rank_first_hit_position():
    assert reciprocal_rank(["x", "y", "a"], {"a"}) == pytest.approx(1 / 3)


def test_reciprocal_rank_hit_at_top():
    assert reciprocal_rank(["a", "b"], {"a", "b"}) == 1.0


def test_reciprocal_rank_no_hit_is_zero():
    assert reciprocal_rank(["x", "y"], {"a"}) == 0.0


def test_reciprocal_rank_empty_relevant_is_zero():
    assert reciprocal_rank(["x"], set()) == 0.0


def test_reciprocal_rank_duplicates_do_not_shift_positions():
    # 去重後 "a" 在第 2 位（x, a, y），不是第 3 位
    assert reciprocal_rank(["x", "x", "a", "y"], {"a"}) == pytest.approx(1 / 2)


# --- mrr ----------------------------------------------------------------------


def test_mrr_averages_reciprocal_ranks():
    rankings = [
        (["a", "b"], {"a"}),  # RR = 1
        (["x", "a"], {"a"}),  # RR = 1/2
        (["x", "y"], {"a"}),  # RR = 0
    ]
    assert mrr(rankings) == pytest.approx((1 + 0.5 + 0) / 3)


def test_mrr_empty_input_is_zero():
    assert mrr([]) == 0.0


# --- mean / percentile --------------------------------------------------------


def test_ndcg_perfect_order_is_one():
    grades = {"a": 3, "b": 2, "c": 1, "d": 0}
    assert ndcg_at_k(["a", "b", "c", "d"], grades, 3) == pytest.approx(1.0)


def test_ndcg_distinguishes_order_that_binarized_pk_cannot():
    # 兩個排序的前 3 名都含 {a, b}（P@3 相同），但 trap（grade 0）在
    # 第 2 名 vs 墊底——NDCG 必須照出差異。
    grades = {"a": 3, "b": 2, "trap": 0, "c": 1}
    better = ndcg_at_k(["a", "b", "c", "trap"], grades, 3)
    worse = ndcg_at_k(["a", "trap", "b", "c"], grades, 3)
    assert better > worse


def test_ndcg_all_zero_grades_is_zero():
    assert ndcg_at_k(["a", "b"], {"a": 0, "b": 0}, 3) == 0.0


def test_ndcg_missing_ids_count_as_grade_zero():
    grades = {"a": 3}
    assert ndcg_at_k(["x", "a"], grades, 2) == pytest.approx(
        (2**3 - 1) / 1.584962500721156 / (2**3 - 1)
    )  # a 在第 2 名：DCG = 7/log2(3)，IDCG = 7/log2(2)


def test_ndcg_rejects_bad_k():
    with pytest.raises(ValueError):
        ndcg_at_k(["a"], {"a": 1}, 0)


def test_mean_basic_and_default():
    assert mean([1.0, 2.0, 3.0]) == pytest.approx(2.0)
    assert mean([]) == 0.0
    assert mean([], default=-1.0) == -1.0


def test_percentile_nearest_rank():
    values = [10.0, 20.0, 30.0, 40.0]
    # p50 → ceil(0.5*4)=2 → 第 2 個 = 20；p95 → ceil(0.95*4)=4 → 40
    assert percentile(values, 50) == 20.0
    assert percentile(values, 95) == 40.0
    assert percentile(values, 100) == 40.0


def test_percentile_single_sample():
    assert percentile([7.0], 50) == 7.0
    assert percentile([7.0], 95) == 7.0


def test_percentile_empty_is_none():
    assert percentile([], 50) is None


def test_percentile_rejects_out_of_range_p():
    with pytest.raises(ValueError):
        percentile([1.0], 0)
    with pytest.raises(ValueError):
        percentile([1.0], 101)
