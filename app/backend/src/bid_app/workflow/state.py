"""LangGraph WorkflowState (§10.1 + PR-M7-1 v2 schema bump)。

⚠️ **不放 ``api_key``** (D-C):防止被 PostgresSaver 落库。
运行时通过 ``project_id`` → ``Project.encrypted_api_key_snapshot`` → AES-GCM 解密。

⭐ PR-M7-1:``schema_version`` 是 v2 graph 拒绝 v1 checkpoint 的版本闸门。
新启动的 workflow 由 ``build_initial_state`` 写入 ``schema_version=2``;
老 checkpoint 反序列化后没有这个 key,worker / 关键节点入口调用
``ensure_v2_state`` 校验,不匹配抛 ``WorkflowSchemaMismatch``。
``flush_running_workflows`` CLI 在 v2 上线时清退所有遗留的 v1 项目。
"""

from __future__ import annotations

from typing import Any, TypedDict

CURRENT_WORKFLOW_SCHEMA_VERSION = 2
"""每次 WorkflowState 不向后兼容地变更字段时,**必须**bump 本常量并实现
对应迁移 / flush 流程(D1)。"""


class WorkflowSchemaMismatch(Exception):
    """运行时检测到 checkpoint 与当前 graph schema 不兼容。

    ``current`` 是当前 graph 期望的 schema_version,``found`` 是 checkpoint
    实际带的版本(``None`` 表示老 checkpoint 完全没有这个字段)。
    上层 worker 收到该异常后把 ``Project.status`` 标 ``aborted_schema_v1``,
    UI 提示用户重建项目。
    """

    def __init__(self, *, current: int, found: int | None) -> None:
        super().__init__(
            f"workflow checkpoint schema_version={found} cannot resume on "
            f"graph schema_version={current}; please rebuild the project"
        )
        self.current = current
        self.found = found


class OutlineNode(TypedDict, total=False):
    """PR-M8-2 outline JSON 节点。本 PR 仅引入类型,实际生产由 M8-2 编译。"""

    id: str
    title: str
    level: int
    description: str
    children: list[OutlineNode]


class WorkflowState(TypedDict, total=False):
    # === Schema 版本闸门(PR-M7-1)===
    schema_version: int

    # === 输入(只读)===
    project_id: int  # ⭐ DB 查询入口
    run_id: int
    tech_spec_md: str
    scoring_md: str
    template_md: str
    pages_per_chapter: int
    max_retry_per_chapter: int

    # === v10 §3.3 五个 Loop 变量(命名严格对齐设计稿)===
    chapters: list[dict[str, Any]]
    current_index: int
    retry_count: int
    finalized_chapters: list[str]
    revision_feedback: str

    # === Human Review 临时载体(由 Command(resume=...) 注入)===
    _review_decision: str  # approve | revise | skip
    _review_feedback: str

    # === Outline 编辑临时载体(P4 提纲确认,D-K)===
    # 由 /confirm-outline 端点通过 Command(resume={...}) 注入。
    # 若为 None / [] 走"自动确认",直接用 LLM-1 生成的 chapters 进入循环。
    _outline_confirmed_chapters: list[dict[str, Any]] | None

    # === PR-M8-1 material_understanding 评审临时载体 ===
    _material_review_decision: str  # pass | revise | skip
    _material_review_feedback: str

    # === outline_review 评审临时载体(textarea TOC + revise 路径)===
    # confirm = 用户已在 textarea 编辑好,提交 chapters;revise = 用户给 LLM-1 反馈
    # 让其重新生成。conditional edge ``_route_after_outline_review`` 据此分支。
    _outline_review_decision: str  # confirm | revise
    _outline_revision_feedback: str  # 用户写给 LLM-1 的反馈,generate_outline 用

    # === 节点之间的临时载体 ===
    # generate_outline 输出 LLM-1 原始 JSON 字符串,parse_outline 消费。
    _outline_json: str
    # write_chapter 输出的章节正文,review_chapter (LLM-3 视觉)/merge_chapter 消费;
    # update_state 写完成后清空。
    _pending_chapter_text: str
    _pending_visuals_json: str

    # === PR-M7-3 / PR-M8-1 / PR-M8-2 / PR-M9-1 新增字段 ===
    # PR-M7-3:HTML 黑板节选,extract → blackboard 节点聚合后注入下游 prompt
    blackboard_excerpt: str
    # PR-M8-1:LLM-0 输出的结构化材料理解 JSON
    material_understanding: dict[str, Any] | None
    # PR-M8-2:结构化目录(替代 chapters dict 列表)
    outline_json: list[OutlineNode] | None
    # PR-M9-1:用户勾选要生成的章节 id 列表;空 / None → 全选
    selected_chapter_ids: list[str] | None

    # === 输出 ===
    final_proposal: str | None


def ensure_v2_state(state: WorkflowState) -> None:
    """在 worker / 关键节点入口校验 checkpoint 与当前 graph 兼容。

    校验失败抛 ``WorkflowSchemaMismatch``,worker 顶层捕获后把项目标
    ``aborted_schema_v1``。**不会修改 state**;v1 → v2 没有自动迁移路径
    (字段语义变化太大),只能由用户重建项目(D1 断旧续新)。

    ⚠️ LangGraph 0.6 在节点触发 ``interrupt()`` 时,``astream`` 会 yield
    一个只含 ``__interrupt__`` 键的 sentinel 状态(channel values 未一起
    冒泡)。这种 yield 不代表 checkpoint,跳过校验避免误判成 v1 不兼容。
    """
    # LangGraph 中断 sentinel — 不是真正的 state snapshot,跳过
    if "__interrupt__" in state:
        return
    found = state.get("schema_version")
    if found != CURRENT_WORKFLOW_SCHEMA_VERSION:
        raise WorkflowSchemaMismatch(
            current=CURRENT_WORKFLOW_SCHEMA_VERSION,
            found=found,
        )
