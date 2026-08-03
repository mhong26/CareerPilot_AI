"""KitState — graph 的共享狀態（Phase 7 Step 7）。

graph 每個 node 讀取 state、回傳「部分更新」，LangGraph 負責合併。
``messages`` 掛 ``add_messages`` reducer：node 回傳的新訊息**附加**到對話
歷史（而非覆蓋）——ReAct 迴圈的對話累積由此而來。

三類 artifact 的保存狀態**不**複製進 state：``ctx.saved`` 以 DB 為準，
completion check 直接讀它——單一事實來源，state 只放流程控制旗標。
"""

from typing import Annotated, TypedDict

from langchain_core.messages import AnyMessage
from langgraph.graph.message import add_messages


class KitState(TypedDict):
    # 對話累積（system / human / AI(tool_calls) / tool 訊息交錯）。
    messages: Annotated[list[AnyMessage], add_messages]
    # compute_match 成功後由 executor 同步進來，供 score routing 用（FR-58）。
    match_score: float | None
    # score directive 只發一次。
    directive_issued: bool
    # completion check 的重提示次數（上限見 graph._MAX_REPROMPTS）。
    reprompt_count: int
    # planner 逾時旗標：deadline 已過 → 不再呼叫 LLM、逕行收尾。
    timed_out: bool
