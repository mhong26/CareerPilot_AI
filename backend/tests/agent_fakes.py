"""測試用假 agent 元件（Phase 7）。

``ScriptedPlanner``：照劇本回 ``AIMessage`` 序列的假聊天模型——劇本完全決定
graph 走哪條路，測試可重現。``bind_tools`` 回傳 self 並記錄收到的 tools 供
斷言「7 個都綁了」。劇本出完後回無 tool_calls 的收尾訊息（graph 視同完成
決策）；``loop_last=True`` 則永遠重複最後一則（鬼打牆情境）。
"""

from typing import Any

from langchain_core.language_models import BaseChatModel
from langchain_core.messages import AIMessage
from langchain_core.outputs import ChatGeneration, ChatResult
from pydantic import Field, PrivateAttr


def tool_call(name: str, call_id: str, args: dict[str, Any] | None = None) -> AIMessage:
    """劇本便利函式：一則只含單一工具呼叫的 AIMessage。"""
    return AIMessage(content="", tool_calls=[{"name": name, "args": args or {}, "id": call_id}])


class ScriptedPlanner(BaseChatModel):
    script: list[AIMessage] = Field(default_factory=list)
    loop_last: bool = False
    bound_tools: list[Any] = Field(default_factory=list)
    _cursor: int = PrivateAttr(default=0)
    _loops: int = PrivateAttr(default=0)

    @property
    def _llm_type(self) -> str:
        return "scripted-planner"

    def bind_tools(self, tools, **kwargs):
        self.bound_tools = list(tools)
        return self

    def _generate(self, messages, stop=None, run_manager=None, **kwargs) -> ChatResult:
        if self._cursor < len(self.script):
            message = self.script[self._cursor]
            if self.loop_last and self._cursor == len(self.script) - 1:
                # 每次都要是「新訊息」：add_messages 依 message.id 去重，重複回傳
                # 同一物件會被當成更新而非附加，鬼打牆就假不起來了。
                message = message.model_copy(update={"id": f"loop-{self._loops}"})
                self._loops += 1
            else:
                self._cursor += 1
        else:
            message = AIMessage(content="Done.")
        return ChatResult(generations=[ChatGeneration(message=message)])


class FailingPlanner(BaseChatModel):
    """每次呼叫都拋錯——驗證 planner 重試後降級、run 仍能收尾。"""

    @property
    def _llm_type(self) -> str:
        return "failing-planner"

    def bind_tools(self, tools, **kwargs):
        return self

    def _generate(self, messages, stop=None, run_manager=None, **kwargs) -> ChatResult:
        raise RuntimeError("planner network down")
