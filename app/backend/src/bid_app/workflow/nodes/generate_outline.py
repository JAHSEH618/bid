"""LLM-1 提纲生成节点(v10 §4.3 / Spec §10.2 / D-K)。

读取 ``state.tech_spec_md`` / ``scoring_md`` / ``template_md``,调
``call_llm_json`` 拿到 outline JSON 字符串,落到 ``state._outline_json``
临时载体,下游 ``parse_outline`` 解析成结构化 chapters。

D-EF (2026-05-18):本节点根据 ``material_understanding.project_category``
选模版骨架包(``template_pack``),把骨架注入 LLM-1 prompt 作为强约束;
确定的 ``template_pack`` 写回 ``Project.template_pack`` 让 revise / resume
沿用同一份骨架。
"""
from __future__ import annotations

import sqlalchemy as sa
import structlog
from sqlalchemy import select

from ...config import settings
from ...db import session_factory
from ...services.blackboard_retrieval import (
    SEARCH_BLACKBOARD_TOOL,
    make_blackboard_tool_handler,
)
from ...services.llm import call_llm_json, call_llm_with_tools_json
from ..prompts.outline_prompt import build_messages
from ..resolve import resolve_models
from ..state import WorkflowState
from ..sync import publish_event

log = structlog.get_logger()


async def _resolve_api_key(project_id: int, run_id: int | None = None) -> str:
    """⭐ D-C 真快照 + R10 严格失败语义(REVIEW-2 🔴 修复)。

    生产路径(``run_id > 0``):snapshot 缺失 / decrypt 失败 / DB 异常都
    raise(worker 顶层 ``_fail_project_and_run``);**不**回退环境变量,
    防 .env 误注入 ``BID_APP_CLI_API_KEY`` 让 worker 偷换用户真快照。
    CLI 路径(``run_id <= 0``)允许 ``$BID_APP_CLI_API_KEY`` fallback。
    """
    import os

    is_production = run_id is not None and run_id > 0

    encrypted: bytes | None = None
    try:
        from ...core.crypto import decrypt_api_key
        from ...models import Project

        async with session_factory() as s:
            row = await s.execute(
                select(Project.encrypted_api_key_snapshot).where(
                    Project.id == project_id
                )
            )
            encrypted = row.scalar_one_or_none()
    except Exception as e:
        if is_production:
            raise RuntimeError(
                f"db error resolving api_key for project {project_id}: {e}"
            ) from e

    if encrypted is not None:
        try:
            return decrypt_api_key(encrypted)
        except Exception as e:
            if is_production:
                raise RuntimeError(
                    f"decrypt api_key failed for project {project_id} "
                    f"(master_key 与启动时不一致?R10 检查): {e}"
                ) from e

    if is_production:
        raise RuntimeError(
            f"project {project_id} has no api_key snapshot; did /start succeed?"
        )

    cli_key = os.environ.get("BID_APP_CLI_API_KEY")
    if cli_key:
        return cli_key
    raise RuntimeError(
        f"project {project_id} has no api_key snapshot; did /start succeed? "
        "(or set BID_APP_CLI_API_KEY for CLI mode)"
    )


async def _resolve_user_id(project_id: int) -> int:
    """``Project.api_key_owner`` 是 ``Mapped[int | None]``,行存在但字段
    NULL 时返 0(REVIEW-2 🟡 #3 fix)。"""
    try:
        from ...models import Project

        async with session_factory() as s:
            row = await s.execute(
                select(Project.api_key_owner).where(Project.id == project_id)
            )
            return row.scalar_one_or_none() or 0
    except Exception:
        return 0


async def run(state: WorkflowState) -> dict[str, str | None]:
    project_id = state["project_id"]
    run_id = state.get("run_id")

    api_key = await _resolve_api_key(project_id, run_id=run_id)
    user_id = await _resolve_user_id(project_id)

    # outline_review 的 revise 分支会把用户反馈塞到 ``_outline_revision_feedback``,
    # 下一轮 LLM-1 prompt 注入这一段做整体重设计。读完 generate_outline 一并
    # 清空(返回 ""),避免下次启动残留。
    revision_feedback = state.get("_outline_revision_feedback") or ""

    blackboard_entities = state.get("blackboard_entities")
    # Phase 2B (2026-05-16):tool calling 路径要求实体黑板已就绪 +
    # settings 开关打开。LLM 通过 search_blackboard 自主检索黑板,而不是
    # build_messages 把 50k 桶 dump 全塞进 prompt。
    use_tool_calling = (
        settings.llm_tool_calling_enabled
        and bool(blackboard_entities)
        and any(blackboard_entities.values())  # type: ignore[union-attr]
    )

    # D-EF:挑模版骨架包 — material_understanding.project_category → pack id。
    # 已经选定过(revise 路径上一轮写入)则沿用,否则按 LLM-0 分类挑;
    # 都没有时回落到 DEFAULT_PACK_ID。
    from ..templates import DEFAULT_PACK_ID, pick_pack

    template_pack = state.get("template_pack")
    skeleton: list[dict[str, str]] | None = None
    pack_id: str | None = template_pack
    try:
        mu = state.get("material_understanding") or {}
        category = None
        if isinstance(mu, dict):
            cat_val = mu.get("project_category")
            if isinstance(cat_val, str) and cat_val:
                category = cat_val
        pack = pick_pack(category) if not template_pack else None
        if pack is None:
            # template_pack 已存在 → 直接 load
            from ..templates import load_pack

            assert template_pack is not None
            try:
                pack = load_pack(template_pack)
            except FileNotFoundError:
                pack = pick_pack(None)  # 回落
        skeleton = pack.get("skeleton") if isinstance(pack, dict) else None
        pack_id = pack.get("id") if isinstance(pack, dict) else DEFAULT_PACK_ID
    except Exception:
        log.exception("generate_outline_pick_pack_failed", project_id=project_id)
        skeleton = None
        pack_id = pack_id or DEFAULT_PACK_ID

    messages = build_messages(
        tech_spec_md=state.get("tech_spec_md", ""),
        scoring_md=state.get("scoring_md", ""),
        template_md=state.get("template_md", ""),
        revision_feedback=revision_feedback,
        blackboard_entities=blackboard_entities,
        tool_calling_enabled=use_tool_calling,
        skeleton=skeleton,
    )

    # D-EF hotfix:有骨架时强制关掉 tool calling。骨架已经把结构约束写满,
    # LLM 不需要再调 search_blackboard 反复采集;tool 循环只会让 LLM 重复
    # 查同一桶不收口(上次用户碰到 480s+ 才出)。build_messages 内同名
    # 变量影响 prompt 文案,这里影响实际 LLM 调用走向,两处必须一致。
    if skeleton:
        use_tool_calling = False

    await publish_event(project_id, "outline_started")
    models = await resolve_models(project_id)

    if use_tool_calling:
        log.info(
            "outline_tool_calling_enabled",
            project_id=project_id,
            model=models.outline_model,
            max_rounds=settings.llm_tool_max_rounds,
            template_pack=pack_id,
        )
        tool_handler = make_blackboard_tool_handler(blackboard_entities)
        _parsed, sr = await call_llm_with_tools_json(
            model=models.outline_model,
            messages=messages,
            api_key=api_key,
            user_id=user_id,
            project_id=project_id,
            run_id=run_id,
            tools=[SEARCH_BLACKBOARD_TOOL],
            tool_handler=tool_handler,
            max_tool_rounds=settings.llm_tool_max_rounds,
            timeout_seconds=settings.llm_outline_timeout_seconds,
            # tool calling 不传 response_format(DashScope 二选一);system
            # prompt 已经强引导 JSON 输出
            max_tokens=16384,
        )
    else:
        _parsed, sr = await call_llm_json(
            model=models.outline_model,
            messages=messages,
            api_key=api_key,
            user_id=user_id,
            project_id=project_id,
            run_id=run_id,
            timeout_seconds=settings.llm_outline_timeout_seconds,
            # 显式给到 16384:prompt 要求 25-50 个叶子 + 每叶 summary/key_points/
            # matched_scoring_items,JSON 体积容易撞默认 max_tokens (DashScope flash
            # 档常见 2-4k) 被截断;v4-pro 深目录 4 级展开过 8k 也撞过限,放到 16k。
            # LiteLLM 会按模型实际上限自动 clamp,不会超过模型能力。
            max_tokens=16384,
        )

    # D-EF:持久化 pack_id(只在生产路径写,CLI run_id<=0 跳过)
    if isinstance(run_id, int) and run_id > 0:
        await _persist_template_pack(project_id, pack_id)

    return {
        "_outline_json": sr.text,
        "_outline_revision_feedback": "",
        "template_pack": pack_id,
    }


async def _persist_template_pack(project_id: int, pack_id: str | None) -> None:
    """把选定的 ``template_pack`` 写回 ``Project`` 表(D-EF)。

    幂等:已存在相同值则跳过。失败不抛(state 已存,DB 一致性可在下次 resume
    时再对齐),只记一行 warning。
    """
    if not pack_id:
        return
    try:
        async with session_factory() as s:
            await s.execute(
                sa.text(
                    "UPDATE projects SET template_pack=:pk "
                    "WHERE id=:p AND (template_pack IS NULL OR template_pack <> :pk)"
                ),
                {"pk": pack_id, "p": project_id},
            )
            await s.commit()
    except Exception:
        log.exception(
            "generate_outline_persist_pack_failed",
            project_id=project_id,
            pack=pack_id,
        )
