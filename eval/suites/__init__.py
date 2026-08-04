"""評估 suite 的共用結果容器與持久化。

每個 suite 跑完把 ``SuiteResult`` 存進 ``eval/.cache/results/<name>.json``：
run_eval 可以分多天、分 suite 跑（judge 額度限制），report 渲染時合併
「每個 suite 的最新一份結果」——多次部分執行收斂成一份完整報告。
"""

import json
from dataclasses import asdict, dataclass, field
from datetime import UTC, datetime

from eval.config import EvalConfig


@dataclass
class SuiteResult:
    name: str
    # 呈現層：每張表 {"title": str, "headers": [...], "rows": [[...]]}。
    tables: list[dict] = field(default_factory=list)
    # 報告中的補充說明（caveat、樣本數、跳過原因…）。
    notes: list[str] = field(default_factory=list)
    skipped: bool = False
    partial: bool = False
    # 機器可讀彙總（測試與跨 suite 引用用；不直接進報告）。
    data: dict = field(default_factory=dict)
    generated_at: str = ""

    def save(self, cfg: EvalConfig) -> None:
        self.generated_at = datetime.now(UTC).isoformat(timespec="seconds")
        cfg.results_dir.mkdir(parents=True, exist_ok=True)
        path = cfg.results_dir / f"{self.name}.json"
        path.write_text(
            json.dumps(asdict(self), indent=2, ensure_ascii=False), encoding="utf-8"
        )


def load_result(cfg: EvalConfig, name: str) -> SuiteResult | None:
    """讀取某 suite 先前存檔的結果；不存在或壞檔回 None。"""
    path = cfg.results_dir / f"{name}.json"
    try:
        return SuiteResult(**json.loads(path.read_text(encoding="utf-8")))
    except (FileNotFoundError, json.JSONDecodeError, TypeError):
        return None


def fmt(value: float | None, digits: int = 3) -> str:
    """報告數字格式化：None → "n/a"。"""
    if value is None:
        return "n/a"
    return f"{value:.{digits}f}"


def improvement_pct(system: float, baseline: float) -> float | None:
    """(system - baseline) / baseline；baseline 為 0 時回 None（n/a）。"""
    if baseline == 0:
        return None
    return (system - baseline) / baseline * 100
