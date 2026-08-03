"""build_kit_graph — planner 與工具的往返迴圈＋三個客製機制（Phase 7 Step 7）。

圖形（對應 phase7_plan.md §4.2；「completion check」的**判斷**落在 planner
之後的 conditional edge、**重提示動作**是 reprompt node——決策唯讀、寫入
歸 node，避免「判斷+寫入」同節點時 edge 無法分辨『剛重提示』與『已放棄』）：

    START → planner ──有 tool_calls──▶ execute_tools ──剛算完 match 且未發過
              ▲  │                        │  ▲          directive──▶ inject_directive
              │  │沒有 tool_calls          │  └──否則回 planner            │
              │  ▼                        └────────────────────────────────┘
       route_after_planner：三類已存或逾時或重提示用盡 → END
              │否則
              ▼
           reprompt（注入 [reminder] 訊息）→ planner

工具執行採**自訂 executor node** 而非 prebuilt ToolNode，因為要在同一處做
三件事：(a) 工具拋錯 → 錯誤 ToolMessage 降級不 crash（NFR-4）；(b) 把
compute_match 分數同步進 state 供 score routing（FR-58）；(c) 保存狀態以
ctx.saved（DB 為準）供完成檢查。

FR-66 合規：graph 沒有任何「工具 A 之後必然工具 B」的硬連線；唯二流程干預
是 score directive（FR-58 允許）與 step/timeout 防護（FR-66 允許）。directive
是「注入建議訊息」而非強制路徑，最終選擇權在 LLM。
"""

import time
from typing import Any

from langchain_core.language_models import BaseChatModel
from langchain_core.messages import AIMessage, AnyMessage, HumanMessage, SystemMessage, ToolMessage
from langchain_core.tools import BaseTool
from langgraph.graph import END, START, StateGraph
from langgraph.graph.state import CompiledStateGraph

from app.ai.agents.context import KIT_KINDS, KitRunContext
from app.ai.agents.state import KitState
from app.ai.llm.base import TokenUsage
from app.ai.prompts.kit import KIT_PLANNER_SYSTEM
from app.core.config import settings
from app.services.llm_call_log_service import record_call

# 同步請求的 agent 總時限（秒）；planner 每輪開工前檢查（timeout 防護）。
KIT_DEADLINE_SECONDS = 240
# 整張圖最多走幾步的保險絲（防 LLM 鬼打牆）；invoke 時傳入 config。
KIT_RECURSION_LIMIT = 50
# 完成檢查的重提示上限：點名缺漏最多兩次，仍不齊就 partial 收場。
_MAX_REPROMPTS = 2
# score routing 門檻（FR-58；與 MatchResult.match_score 註解、前端配色一致）。
_LOW_SCORE = 0.5
_HIGH_SCORE = 0.8


def build_initial_kit_state(*, job_title: str, job_company: str) -> KitState:
    """組初始 state：planner system prompt + 本次任務說明。"""
    task = (
        "Create the application kit for the job "
        f"'{job_title or '(unknown title)'}' at '{job_company or '(unknown company)'}': "
        "generate and save all three artifacts."
    )
    return {
        "messages": [
            SystemMessage(content=KIT_PLANNER_SYSTEM),
            HumanMessage(content=task),
        ],
        "match_score": None,
        "directive_issued": False,
        "reprompt_count": 0,
        "timed_out": False,
    }


def _messages_repr(messages: list[AnyMessage]) -> str:
    """把對話壓成字串——只供 record_call 取 sha256 指紋，不持久化原文。"""
    return "\n".join(f"[{m.type}] {m.content}" for m in messages)


def build_kit_graph(
    planner_model: BaseChatModel, tools: list[BaseTool], ctx: KitRunContext
) -> CompiledStateGraph:
    """編譯 kit agent graph；invoke 時請帶 ``config={"recursion_limit": KIT_RECURSION_LIMIT}``。"""
    tools_by_name = {t.name: t for t in tools}
    planner_runnable = planner_model.bind_tools(tools)

    def planner(state: KitState) -> dict[str, Any]:
        """呼叫 LLM 決定下一步；逾時不再呼叫；失敗重試一次後降級（partial 收場）。"""
        if time.monotonic() > ctx.deadline:
            return {"timed_out": True}
        messages = state["messages"]
        start = time.perf_counter()
        response = None
        last_exc: Exception | None = None
        for _ in range(2):  # 網路等暫時性錯誤重試一次
            try:
                response = planner_runnable.invoke(messages)
                break
            except Exception as exc:
                last_exc = exc
        if response is None:
            ctx.errors.append(f"planner failed after retry: {last_exc}")
            return {}
        # planner 呼叫記帳（FR-65）；usage_metadata 缺失時記零值。
        metadata = getattr(response, "usage_metadata", None) or {}
        usage = TokenUsage(
            prompt_tokens=metadata.get("input_tokens", 0),
            completion_tokens=metadata.get("output_tokens", 0),
            total_tokens=metadata.get("total_tokens", 0),
        )
        record_call(
            ctx.db,
            provider="gemini",
            model=settings.gemini_model,
            operation="agent_planner",
            prompt=_messages_repr(messages),
            usage=usage,
            latency_ms=int((time.perf_counter() - start) * 1000),
            status="success",
            user_id=ctx.user.id,
        )
        return {"messages": [response]}

    def route_after_planner(state: KitState) -> str:
        """有 tool_calls → 執行；否則做完成檢查：全存/逾時/重提示用盡 → END，缺 → reprompt。"""
        last = state["messages"][-1]
        if not state["timed_out"] and isinstance(last, AIMessage) and last.tool_calls:
            return "execute_tools"
        missing = [k for k in KIT_KINDS if k not in ctx.saved]
        if not missing or state["timed_out"] or state["reprompt_count"] >= _MAX_REPROMPTS:
            return END
        return "reprompt"

    def execute_tools(state: KitState) -> dict[str, Any]:
        """逐一執行 tool_calls；失敗轉錯誤 ToolMessage 降級（NFR-4）；同步 match 分數。"""
        last = state["messages"][-1]
        assert isinstance(last, AIMessage)
        updates: dict[str, Any] = {}
        out: list[AnyMessage] = []
        for call in last.tool_calls:
            name = call["name"]
            tool = tools_by_name.get(name)
            if tool is None:
                content = f"[tool_error] unknown tool: {name}"
                ctx.errors.append(content)
            else:
                try:
                    content = tool.invoke(call["args"])
                except Exception as exc:
                    # rollback 讓共享 session 脫離 pending-rollback 狀態——工具
                    # 失敗若發生在 DB 交易中（IntegrityError、斷線…），不 rollback
                    # 則下一次 record_call / 工具查詢會拋 PendingRollbackError，
                    # 降級鏈整條變 500（NFR-4 就破功了）。
                    ctx.db.rollback()
                    content = f"[tool_error] {name} failed: {exc}"
                    ctx.errors.append(f"{name} failed: {exc}")
                else:
                    if name == "compute_match" and ctx.match_result is not None:
                        updates["match_score"] = ctx.match_result.match_score
            out.append(ToolMessage(content=content, tool_call_id=call["id"], name=name))
        updates["messages"] = out
        return updates

    def route_after_execute(state: KitState) -> str:
        """compute_match 剛完成且未發過指令 → 注入 directive；否則回 planner。"""
        if state["match_score"] is not None and not state["directive_issued"]:
            return "inject_directive"
        return "planner"

    def inject_directive(state: KitState) -> dict[str, Any]:
        """依 score 門檻注入 [directive] 訊息（FR-58）——建議而非強制路徑（FR-66）。

        用 HumanMessage 前綴 [directive]，不用 mid-conversation SystemMessage
        （Gemini 的 system 轉換對中途插入不友善）。
        """
        score = state["match_score"] or 0.0
        if score < _LOW_SCORE:
            text = (
                f"[directive] Match score is low ({score:.2f}). Call retrieve_job_evidence "
                "first to confirm what the job demands and where the gaps are, then "
                "generate the tailored resume with suggestions focused on closing them."
            )
        elif score >= _HIGH_SCORE:
            text = (
                f"[directive] Match score is high ({score:.2f}). You may skip extra "
                "evidence retrieval and generate all three artifacts directly."
            )
        else:
            text = (
                f"[directive] Match score is moderate ({score:.2f}). Use your judgment "
                "on whether retrieving more job evidence would improve the artifacts."
            )
        return {"messages": [HumanMessage(content=text)], "directive_issued": True}

    def reprompt(state: KitState) -> dict[str, Any]:
        """點名缺漏的 [reminder] 訊息（最多 _MAX_REPROMPTS 次）→ 回 planner。"""
        missing = [k for k in KIT_KINDS if k not in ctx.saved]
        reminder = (
            f"[reminder] Not all artifacts are saved yet. Missing: {', '.join(missing)}. "
            "Generate each missing artifact and call save_artifact for it."
        )
        return {
            "messages": [HumanMessage(content=reminder)],
            "reprompt_count": state["reprompt_count"] + 1,
        }

    graph = StateGraph(KitState)
    graph.add_node("planner", planner)
    graph.add_node("execute_tools", execute_tools)
    graph.add_node("inject_directive", inject_directive)
    graph.add_node("reprompt", reprompt)
    graph.add_edge(START, "planner")
    graph.add_conditional_edges(
        "planner",
        route_after_planner,
        {"execute_tools": "execute_tools", "reprompt": "reprompt", END: END},
    )
    graph.add_conditional_edges(
        "execute_tools",
        route_after_execute,
        {"inject_directive": "inject_directive", "planner": "planner"},
    )
    graph.add_edge("inject_directive", "planner")
    graph.add_edge("reprompt", "planner")
    return graph.compile()
