"""CareerPilot AI — Evaluation Layer（Phase 8，ER-1~9 / FR-59 / NFR-1、5）。

本 package 獨立於 backend app：透過 ``eval._bootstrap`` 把 ``backend/`` 加進
``sys.path`` 後直接呼叫真實 production 服務（matcher / RAG / 生成），
不重新實作任何業務邏輯。入口：``python eval/run_eval.py``。

Bootstrap 掛在 package ``__init__``：任何 ``import eval.X`` 都先經過這裡，
子模組內的 ``from app...`` import 因此不受 import 排序工具影響（子模組頂端
的 ``import eval._bootstrap`` 屬多餘保險，留著無害）。
"""

from eval import _bootstrap  # noqa: F401
