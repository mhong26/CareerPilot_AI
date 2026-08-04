"""Dataset 情境檔驗證。

`load_all` 本身就會執行 schema 與一致性檢查（chunk_index 範圍、ranking 覆蓋、
tag 詞彙…），所以「載入成功」即通過大半驗證；此處再補 dataset 層級的約束。
任何人改動情境 parsed 內容或 chunking 邏輯導致標註失效時，這裡會在 CI 炸掉。
"""

import pytest

import eval._bootstrap  # noqa: F401  # isort: split
from eval.datasets.schema import load_all

SCENARIOS = load_all()
TARGET_SCENARIO_COUNT = 25  # ER-4：至少 25 組
MIN_FAMILY_COUNT = 8  # 設計文件承諾的兩大案例族最低數


def test_scenarios_load_and_validate():
    assert len(SCENARIOS) >= 2, "expect at least the two pilot scenarios"


@pytest.mark.parametrize("scenario", SCENARIOS, ids=[s.scenario_id for s in SCENARIOS])
def test_scenario_has_findable_target(scenario):
    # MRR 需要至少一個 relevant job；schema 已驗，這裡留一個顯式斷言當文件。
    assert scenario.relevant_job_keys


@pytest.mark.parametrize("scenario", SCENARIOS, ids=[s.scenario_id for s in SCENARIOS])
def test_gap_target_is_relevant_job(scenario):
    # skill gap 分析對「無關 job」沒有意義：gap 目標必須是 relevant（grade >= 2）。
    assert scenario.skill_gap.job_key in scenario.relevant_job_keys


@pytest.mark.parametrize("scenario", SCENARIOS, ids=[s.scenario_id for s in SCENARIOS])
def test_gap_target_has_hallucination_labels(scenario):
    # must_not_claim 是免 judge 的幻覺訊號來源，每個情境都要有。
    assert scenario.skill_gap.must_not_claim


def test_dataset_size_and_case_family_census():
    """完整 dataset 的規模與案例族承諾（未達 25 組前跳過，不擋 pilot 開發）。"""
    if len(SCENARIOS) < TARGET_SCENARIO_COUNT:
        pytest.skip(f"dataset incomplete: {len(SCENARIOS)}/{TARGET_SCENARIO_COUNT} scenarios")
    semantic = sum(1 for s in SCENARIOS if "semantic-no-keyword-overlap" in s.tags)
    trap = sum(1 for s in SCENARIOS if "keyword-trap" in s.tags)
    assert semantic >= MIN_FAMILY_COUNT
    assert trap >= MIN_FAMILY_COUNT
