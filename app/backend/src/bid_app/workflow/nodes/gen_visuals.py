"""LLM-3 章节可视化建议节点(v10 §4.5.3 / Spec §10.2 ``gen_visuals``)。

读 LLM-2 输出的章节正文,产出 ``{"items":[...]}`` JSON 建议清单,供下游
``merge_chapter`` 模板转换合并。

输入:``state._pending_chapter_text``
输出:``state._pending_visuals_json``(原始 JSON 字符串)

D-EH (2026-05-18) 锚点驱动改造:
- 先扫描正文里所有 ``对应时序图:<name>`` / ``对应架构图:<name>`` 锚点
- 每个锚点并发调一次 LLM-3,**强制 1:1 出图**(避免漏图 / 多图)
- 并发用 ``asyncio.Semaphore(3)`` 限速防限流
- 无任何锚点时(``normal`` 章 / 老格式) → 退回自由发现模式(原 v10 行为),
  ``image_only`` / ``table_only`` 跳过整步(它们没有 LLM-2 正文)
"""
from __future__ import annotations

import asyncio
import json
import re
from typing import Any

import structlog
from sqlalchemy import select

from ...db import session_factory
from ...services.llm import call_llm_json
from ..prompts.review_chapter_prompt import (
    build_architecture_messages,
    build_messages,
    build_sequence_messages,
)
from ..resolve import resolve_models
from ..state import WorkflowState
from ..sync import publish_event

log = structlog.get_logger()


# D-EH 锚点正则。LLM-2 写出的锚点格式固定:行首 `对应时序图:` 或 `对应架构图:`,
# 后跟流程名 / 图名;允许中英文冒号兼容(LLM 偶尔混)。
_SEQ_ANCHOR_RE = re.compile(r"^[ \t]*对应时序图[::]\s*(.+?)[ \t]*$", re.MULTILINE)
_ARCH_ANCHOR_RE = re.compile(r"^[ \t]*对应架构图[::]\s*(.+?)[ \t]*$", re.MULTILINE)


def _scan_anchors(chapter_text: str) -> tuple[list[str], list[str]]:
    """扫描正文里的时序图 / 架构图锚点名。

    返回 ``(seq_names, arch_names)``。两个列表保留出现顺序;同名只算一次。
    """
    seen_seq: set[str] = set()
    seq_names: list[str] = []
    for m in _SEQ_ANCHOR_RE.finditer(chapter_text):
        name = m.group(1).strip()
        if not name or name in seen_seq:
            continue
        seen_seq.add(name)
        seq_names.append(name)

    seen_arch: set[str] = set()
    arch_names: list[str] = []
    for m in _ARCH_ANCHOR_RE.finditer(chapter_text):
        name = m.group(1).strip()
        if not name or name in seen_arch:
            continue
        seen_arch.add(name)
        arch_names.append(name)
    return seq_names, arch_names


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


_VISUAL_CONCURRENCY = 3  # 防 DashScope 限流


async def _call_one_visual(
    *,
    messages: list[dict[str, Any]],
    model: str,
    api_key: str,
    user_id: int,
    project_id: int,
    run_id: int | None,
    sem: asyncio.Semaphore,
) -> dict[str, Any] | None:
    """调一次 LLM-3 → 返回 ``items[0]`` dict 或 None(失败)。"""
    async with sem:
        try:
            _parsed, sr = await call_llm_json(
                model=model,
                messages=messages,
                api_key=api_key,
                user_id=user_id,
                project_id=project_id,
                run_id=run_id,
                timeout_seconds=60,
                temperature=0.4,
                max_tokens=1024,
            )
        except Exception:
            log.exception(
                "gen_visuals_anchor_call_failed",
                project_id=project_id,
            )
            return None
    try:
        data = json.loads(sr.text or "{}")
        items = data.get("items") if isinstance(data, dict) else None
        if isinstance(items, list) and items and isinstance(items[0], dict):
            return items[0]
    except json.JSONDecodeError:
        log.warning(
            "gen_visuals_anchor_json_invalid",
            project_id=project_id,
            head=(sr.text or "")[:120],
        )
    return None


async def _gen_anchor_driven(
    *,
    chapter: dict[str, Any],
    chapter_text: str,
    seq_names: list[str],
    arch_names: list[str],
    template_pack: str | None,
    model: str,
    api_key: str,
    user_id: int,
    project_id: int,
    run_id: int | None,
) -> str:
    """D-EH:为每个锚点并发调一次 LLM-3,产出 1:1 visuals JSON。"""
    # 架构图层名:优先从 chapter.required_anchors 取(architecture 章已有),
    # 否则尝试从骨架包读 architecture_layers,最后回落到 rule.md 默认七层。
    default_layers = [
        "接入层", "网关层", "业务服务层", "能力中心层",
        "集成接口层", "数据服务层", "基础设施层",
    ]
    layers: list[str] = list(chapter.get("required_anchors") or [])
    if not layers and template_pack:
        try:
            from ..templates import load_pack

            pack = load_pack(template_pack)
            pack_layers = pack.get("architecture_layers") or []
            if isinstance(pack_layers, list) and pack_layers:
                layers = [str(x) for x in pack_layers]
        except FileNotFoundError:
            pass
    if not layers:
        layers = default_layers

    sem = asyncio.Semaphore(_VISUAL_CONCURRENCY)
    chapter_title = str(chapter.get("title") or "")

    tasks: list[asyncio.Task[dict[str, Any] | None]] = []
    fallback_items: list[dict[str, Any]] = []  # 标记失败的锚点用占位回退到 merge

    for name in seq_names:
        msg = build_sequence_messages(
            flow_name=name,
            chapter_title=chapter_title,
            chapter_body_md=chapter_text,
        )
        tasks.append(
            asyncio.create_task(
                _call_one_visual(
                    messages=msg, model=model, api_key=api_key, user_id=user_id,
                    project_id=project_id, run_id=run_id, sem=sem,
                )
            )
        )
    seq_task_count = len(tasks)
    for _name in arch_names:
        msg = build_architecture_messages(
            layers=layers,
            chapter_title=chapter_title,
            chapter_body_md=chapter_text,
        )
        tasks.append(
            asyncio.create_task(
                _call_one_visual(
                    messages=msg, model=model, api_key=api_key, user_id=user_id,
                    project_id=project_id, run_id=run_id, sem=sem,
                )
            )
        )

    results = await asyncio.gather(*tasks)

    items: list[dict[str, Any]] = []
    # 把 None 的位置补一个占位 anchor,merge_chapter 会渲为「渲染失败,请人工补图」提示
    for idx_anchor, name in enumerate(seq_names):
        item = results[idx_anchor]
        if item is None:
            fallback_items.append(
                {
                    "title": f"{name} 时序图",
                    "type": "mermaid",
                    "anchor": f"对应时序图:{name}",
                    "position": "after",
                    "content": (
                        f'sequenceDiagram\n'
                        f'    note over A,B: {name} 时序图自动生成失败,请人工补图'
                    ),
                }
            )
        else:
            # 锚点照抄保护:LLM 偶尔写半角冒号,merge 端用全角匹配,统一在这里 normalize
            item["anchor"] = f"对应时序图:{name}"
            item.setdefault("position", "after")
            item.setdefault("type", "mermaid")
            items.append(item)
    for offset, _name in enumerate(arch_names):
        item = results[seq_task_count + offset]
        if item is None:
            fallback_items.append(
                {
                    "title": "总体架构",
                    "type": "mermaid",
                    "anchor": "对应架构图:总体架构",
                    "position": "after",
                    "content": (
                        "flowchart TD\n"
                        "    L1[\"架构图渲染失败\"] --> L2[\"请人工补图\"]"
                    ),
                }
            )
        else:
            item["anchor"] = "对应架构图:总体架构"
            item.setdefault("position", "after")
            item.setdefault("type", "mermaid")
            items.append(item)

    items.extend(fallback_items)
    return json.dumps({"items": items}, ensure_ascii=False)


async def run(state: WorkflowState) -> dict[str, Any]:
    project_id = state["project_id"]
    run_id = state.get("run_id")
    idx = state["current_index"]
    chapter = state["chapters"][idx]
    chapter_text = state.get("_pending_chapter_text", "")
    chapter_type = str(chapter.get("chapter_type") or "normal")

    if not chapter_text.strip():
        log.warning(
            "gen_visuals_empty_text",
            project_id=project_id,
            chapter_index=idx,
        )
        return {"_pending_visuals_json": '{"items": []}'}

    # D-EG:image_only / table_only 没有 LLM-2 正文需要配图,直接返空
    if chapter_type in ("image_only", "table_only"):
        return {"_pending_visuals_json": '{"items": []}'}

    api_key = await _resolve_api_key(project_id, run_id=run_id)
    user_id = await _resolve_user_id(project_id)
    models = await resolve_models(project_id)

    # D-EH:先扫锚点;有锚点走 1:1 强制出图,无锚点走旧自由发现
    seq_names, arch_names = _scan_anchors(chapter_text)
    if seq_names or arch_names:
        try:
            text = await _gen_anchor_driven(
                chapter=chapter,
                chapter_text=chapter_text,
                seq_names=seq_names,
                arch_names=arch_names,
                template_pack=state.get("template_pack"),
                model=models.visuals_model,
                api_key=api_key,
                user_id=user_id,
                project_id=project_id,
                run_id=run_id,
            )
            await publish_event(
                project_id, "chapter_visuals_ready", chapter_index=idx
            )
            return {"_pending_visuals_json": text}
        except Exception:
            log.exception(
                "gen_visuals_anchor_driven_failed",
                project_id=project_id,
                chapter_index=idx,
            )
            return {"_pending_visuals_json": '{"items": []}'}

    # 兜底:无锚点 → 旧自由发现模式
    messages = build_messages(
        chapter_title=chapter.get("title", ""),
        chapter_body_md=chapter_text,
    )

    try:
        _parsed, sr = await call_llm_json(
            model=models.visuals_model,
            messages=messages,
            api_key=api_key,
            user_id=user_id,
            project_id=project_id,
            run_id=run_id,
            timeout_seconds=60,
            temperature=0.4,
        )
        await publish_event(
            project_id, "chapter_visuals_ready", chapter_index=idx
        )
        return {"_pending_visuals_json": sr.text}
    except Exception:
        # ⭐ LLM-3 是非关键路径(只是补可视化),失败不能中断章节流程
        log.exception(
            "gen_visuals_failed",
            project_id=project_id,
            chapter_index=idx,
        )
        return {"_pending_visuals_json": '{"items": []}'}
