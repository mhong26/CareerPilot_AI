"""本地 dataset → LangSmith datasets 同步（ER-8）。

**本地 JSON 是 source of truth**：同步採 upsert-by-name + 全量覆蓋
examples（先刪後建）——LangSmith 上的手動編輯會被下一次同步覆蓋，這是
刻意行為。三個 datasets：

- ``careerpilot-eval-matching``：ranking ground truth
- ``careerpilot-eval-skillgap``：chunk 標註 + expected gaps + must_not_claim
- ``careerpilot-eval-rubric``：rubric 受測情境索引

用法：``python -m eval.langsmith.sync_datasets``（或 run_eval --sync-langsmith）。
"""

from langsmith import Client

import eval._bootstrap  # noqa: F401  # isort: split
from eval.datasets.schema import Scenario, load_all

DATASET_MATCHING = "careerpilot-eval-matching"
DATASET_SKILLGAP = "careerpilot-eval-skillgap"
DATASET_RUBRIC = "careerpilot-eval-rubric"


def _matching_example(s: Scenario) -> dict:
    return {
        "inputs": {"scenario_id": s.scenario_id, "resume_summary": s.resume.summary},
        "outputs": {"ground_truth_ranking": s.ground_truth_ranking},
    }


def _skillgap_example(s: Scenario) -> dict:
    return {
        "inputs": {"scenario_id": s.scenario_id, "job_key": s.skill_gap.job_key},
        "outputs": {
            "relevant_chunks": s.skill_gap.relevant_chunks,
            "expected_gaps": [g.model_dump() for g in s.skill_gap.expected_gaps],
            "must_not_claim": s.skill_gap.must_not_claim,
        },
    }


def _rubric_example(s: Scenario) -> dict:
    return {
        "inputs": {"scenario_id": s.scenario_id},
        "outputs": {},
    }


_BUILDERS = {
    DATASET_MATCHING: _matching_example,
    DATASET_SKILLGAP: _skillgap_example,
    DATASET_RUBRIC: _rubric_example,
}


def _upsert_dataset(client: Client, name: str, scenarios: list[Scenario]) -> None:
    try:
        dataset = client.read_dataset(dataset_name=name)
    except Exception:
        dataset = client.create_dataset(
            dataset_name=name,
            description="CareerPilot AI eval (synced from eval/datasets — local JSON wins)",
        )
    # 全量覆蓋：local wins。先物化清單再刪——list_examples 是 lazy 分頁，
    # 邊刪邊迭代會使分頁位移、量大時漏刪。
    for example in list(client.list_examples(dataset_id=dataset.id)):
        client.delete_example(example.id)
    build = _BUILDERS[name]
    examples = [build(s) for s in scenarios]
    client.create_examples(
        dataset_id=dataset.id,
        inputs=[e["inputs"] for e in examples],
        outputs=[e["outputs"] for e in examples],
        metadata=[{"scenario_id": s.scenario_id} for s in scenarios],
    )
    print(f"[langsmith] synced {len(examples)} example(s) → {name}")


def sync_all(scenarios: list[Scenario] | None = None) -> None:
    scenarios = scenarios if scenarios is not None else load_all()
    client = Client()
    for name in _BUILDERS:
        _upsert_dataset(client, name, scenarios)


if __name__ == "__main__":
    sync_all()
