"""TF-IDF baseline 的確定性與行為特徵測試（不需 DB / API）。"""

from eval.baselines.tfidf import rank_jobs_tfidf

RESUME = "Python backend engineer. FastAPI, PostgreSQL, Docker, REST API design, pytest."

JOBS = {
    "keyword_match": "Backend engineer: Python, FastAPI, PostgreSQL required. REST API design.",
    "semantic_only": (
        "Server-side engineer: modern dynamic language, relational data modeling, "
        "HTTP interface craftsmanship."
    ),
    "unrelated": "Pastry chef needed. Croissants, lamination, sourdough starters.",
}


def test_keyword_overlap_ranks_first():
    ranking = rank_jobs_tfidf(RESUME, JOBS)
    assert ranking[0] == "keyword_match"
    assert ranking[-1] == "unrelated"


def test_semantic_match_is_invisible_to_tfidf():
    """語意等價但零關鍵字重疊的職缺，TF-IDF 看不見——這正是 baseline 的已知盲點。"""
    ranking = rank_jobs_tfidf(RESUME, {k: JOBS[k] for k in ("semantic_only", "unrelated")})
    # 兩者對 TF-IDF 都近乎零相似度；斷言的是它「無法把 semantic_only 顯著排前」
    # 的結構性事實：與 keyword_match 同場時 semantic_only 永遠不會是第一名。
    full = rank_jobs_tfidf(RESUME, JOBS)
    assert full[0] != "semantic_only"
    assert set(ranking) == {"semantic_only", "unrelated"}


def test_deterministic():
    assert rank_jobs_tfidf(RESUME, JOBS) == rank_jobs_tfidf(RESUME, JOBS)


def test_all_keys_returned_exactly_once():
    ranking = rank_jobs_tfidf(RESUME, JOBS)
    assert sorted(ranking) == sorted(JOBS)
