# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## What this repo is

This is **not a software project**. It is a single-file design/build guide (Chinese) for a Dify Workflow that automates technical proposal (投标技术方案) generation with a human-review feedback loop.

- Sole artifact: `技术方案自动生成工作流 — Dify 搭建指南(含人工审核).md` (≈1555 lines)
- Target platform: **Dify v1.13.0+** (self-hosted recommended), Workflow app type
- Current document version: **v10.0-Dify**
- No build, no tests, no lint — edits are to the Markdown only

There is no `package.json`, `Makefile`, CI config, or executable code. Python snippets and Jinja templates inside the doc are *configuration payloads* meant to be pasted into Dify nodes, not run locally.

## Document architecture (what the guide describes)

Future edits almost always land inside one of these structural pieces, so know the shape before editing:

1. **Main canvas (DAG)**: Start → 3× Document Extractor (parallel) → LLM-1 outline → Code (parse outline) → **Loop node** → Template Transform (full assembly) → End.
2. **Loop subgraph (the v10 core)**: a single Loop node simultaneously drives chapter iteration *and* rewrite-until-approved. Subgraph is fully DAG (no back-edges) — state is carried via Loop variables, not loops-within-loops.
3. **Loop variables (state machine)** — these names are load-bearing throughout the doc; rename in §3.3, §4.5, §4.5.7, and §4.6 together or things break:
   - `current_index` (Number) — only the Pass/Skip branches +1 it; Revise keeps it
   - `retry_count` (Number)
   - `finalized_chapters` (Array[String]) — main output to assembly
   - `revision_feedback` (String) — fed back into LLM-2 prompt next round
   - `chapters_array` (Array[Object]) — only Loop var with external source (`parse_outline.chapters`)
4. **Loop subgraph nodes** (§4.5.1–§4.5.7): pick_chapter (Code) → LLM-2 → LLM-3 → Template Transform merge → **Human Input** (3-button: pass/revise/skip) → 3 marker Code nodes → Variable Aggregator → update-state Code node. The update-state node's output variable names **must equal Loop variable names exactly** for write-back to work.

## Conventions specific to this guide

These are the rules the doc enforces on itself; preserve them when editing:

- **Dify Jinja2 rule**: LLM / Template Transform / Human Input nodes cannot reference cross-node paths like `extract_tech_spec.text` inside `{{ }}`. Every variable used in a template must first be declared in that node's **Input Variables (jinja2_variables)** panel under a short alias. Object variables expose `.field` access; nested fields don't need separate bindings. When the doc mentions a Jinja template, the corresponding "Input Variables" table must list every alias used.
- **Code node output typing is strict**: declared output type must match the Python return exactly (`Array[Object]` ↔ `list[dict]`, `Number` ↔ `int`/`float`). Any code snippet in the doc must include a matching "输出变量" table.
- **Loop termination**: `current_index ≥ total_chapters`, with `Maximum Loop Count` as a backstop (default 60 = 10 chapters × ~4 retries + headroom).
- **Three-branch aggregation**: Human Input's pass/revise/skip outputs each go through a marker Code node, then into a single Variable Aggregator that produces `{decision, new_feedback}` for the state-update node. Don't collapse the markers — the doc explicitly chose this shape for readability over a single-node switch.
- **Version history is preserved**: §十二 lists v5.0–v10.0 lineage. When introducing a new version, add an entry; don't rewrite history. The §十一 "v9 vs v10 contrast table" is the canonical changelog format.
- **Self-hosted env vars** (§7.1) are the recommended timeout/step knobs — `WORKFLOW_MAX_EXECUTION_TIME`, `HUMAN_INPUT_GLOBAL_TIMEOUT_SECONDS`, etc. Cloud Dify cannot change these; the doc calls this out and should keep doing so.
- **Language**: Chinese (Simplified) prose with English/identifier code. Keep that mix; node IDs are always English snake_case (`chapter_loop`, `pick_chapter`, `extract_tech_spec`).
- **Mermaid diagrams** use a 4-class palette (`llm` purple, `changed` orange, `io` green, `neutral` gray). Reuse these classes rather than introducing new colors.

## Editing workflow

- Edit the `.md` file directly with `Edit`/`Write`. There is nothing to run or validate beyond visual review of the Markdown.
- When changing a Loop variable, node ID, or section number, search the whole file — these names are referenced from cross-reference tables (§一、§十一) and the build-order checklist (§六、§十三). A rename in one place without the others creates silent inconsistency.
- The "搭建顺序" (build order) sections (§6.1, §十三) are the doc's test plan; if you add or remove a node, update both.
