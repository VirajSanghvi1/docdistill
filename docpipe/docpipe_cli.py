#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import os
import sys
import time
import urllib.request
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable

from extract import extract_file


DEFAULT_OLLAMA_URL = "http://127.0.0.1:11434"
DEFAULT_OLLAMA_MODEL = "llama3.2:3b"
DEFAULT_GATEWAY_URL = "http://127.0.0.1:18789"


@dataclass
class Prompts:
    execution_notes: str
    tool_summary: str


def build_prompts(*, token_cap_hint: str, exec_max_tokens: int, summary_max_tokens: int) -> Prompts:
    # Keep prompts short and execution-focused.
    execution_notes = f"""You are an expert at converting documentation into EXECUTION-RELEVANT notes for an AI agent that can run tools (CLI commands, HTTP calls, scripts, functions).

Return ONLY markdown.

OUTPUT FORMAT (Markdown):
- Title
- What this tool/service is
- When to use
- Inputs (required/optional)
- Outputs
- Preconditions / assumptions
- Golden path (numbered steps)
- Verification checks
- Common errors + fixes
- Safety/rollback notes

RULES:
- Be concise and operational; no marketing.
- Prefer concrete commands, flags, endpoints, example payloads.
- Keep within {token_cap_hint}. Target <= {exec_max_tokens} output tokens.

SOURCE (extracted text) below:
---
"""

    tool_summary = f"""You are an expert at writing ULTRA-CONDENSED, AI-readable TOOL INDEX entries.

Return ONLY markdown, and ONLY the filled-in TEMPLATE below.

TEMPLATE (replace the angle-bracket placeholders; keep headings verbatim):

# <TOOL_NAME>

**Purpose:** <ONE_SENTENCE>

**Capabilities:**
- <BULLET>
- <BULLET>
- <BULLET>

**Requires:**
- <BULLET_OR_Unknown>

**Entrypoints:**
- <BULLET_OR_Unknown>

**Limits / footguns:**
- <BULLET>

RULES:
- No table of contents, no prose summary, no history, no change log.
- Prefer symbols, backticks, and short bullets over sentences.
- If the doc describes multiple components, choose the primary "tool" and mention others only as bullets.
- Target <= {summary_max_tokens} output tokens.

SOURCE (extracted text) below:
---
"""

    return Prompts(execution_notes=execution_notes, tool_summary=tool_summary)


def iter_input_files(root: Path) -> Iterable[Path]:
    if root.is_file():
        yield root
        return

    exts = {".pdf", ".txt", ".md", ".html", ".htm"}
    for p in root.rglob("*"):
        if p.is_file() and p.suffix.lower() in exts:
            yield p


def post_json(url: str, data: dict, timeout: int = 120, headers: dict[str, str] | None = None) -> dict:
    body = json.dumps(data).encode("utf-8")
    h = {"Content-Type": "application/json"}
    if headers:
        h.update(headers)
    req = urllib.request.Request(url, data=body, headers=h)
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return json.loads(resp.read().decode("utf-8"))


def ollama_generate(*, ollama_url: str, model: str, prompt: str, num_predict: int) -> str:
    data = post_json(
        f"{ollama_url}/api/generate",
        {
            "model": model,
            "prompt": prompt,
            "stream": False,
            "options": {
                # cap output length
                "num_predict": num_predict,
                # keep things stable-ish
                "temperature": 0.2,
            },
        },
        timeout=600,
    )
    return (data.get("response") or "").strip()


def openclaw_generate(*, gateway_url: str, gateway_token: str, agent_id: str, prompt: str, max_output_tokens: int) -> str:
    # OpenResponses API served by the local OpenClaw Gateway.
    data = post_json(
        f"{gateway_url}/v1/responses",
        {
            "model": "openclaw",
            "input": prompt,
            "max_output_tokens": max_output_tokens,
        },
        timeout=600,
        headers={
            "Authorization": f"Bearer {gateway_token}",
            "x-openclaw-agent-id": agent_id,
        },
    )

    # Non-streaming response aggregates assistant output in output[].content[].text
    out_parts: list[str] = []
    for item in data.get("output") or []:
        if item.get("type") != "message":
            continue
        for part in item.get("content") or []:
            if part.get("type") == "output_text" and part.get("text"):
                out_parts.append(part["text"])

    return "\n".join(out_parts).strip()


def rel_output_path(input_path: Path, input_root: Path, out_root: Path, suffix: str) -> Path:
    if input_root.is_file():
        rel = input_path.name
    else:
        rel = str(input_path.relative_to(input_root))

    # normalize: replace extension with suffix
    base = Path(rel)
    out_rel = base.with_suffix("")
    return out_root / out_rel.parent / (out_rel.name + suffix)


def main() -> int:
    ap = argparse.ArgumentParser(description="DocPipe CLI: extract docs -> execution-notes.md + tool-summary.md")
    ap.add_argument("input", help="File or directory to process")
    ap.add_argument("--out", default="./docpipe_out", help="Output directory")
    ap.add_argument("--engine", choices=["ollama", "openclaw"], default="ollama", help="Generation engine")

    ap.add_argument("--ollama-url", default=DEFAULT_OLLAMA_URL)
    ap.add_argument("--ollama-model", default=DEFAULT_OLLAMA_MODEL)

    ap.add_argument("--gateway-url", default=DEFAULT_GATEWAY_URL)
    ap.add_argument("--gateway-token", default=os.environ.get("OPENCLAW_GATEWAY_TOKEN", ""))
    ap.add_argument("--gateway-agent-id", default="main")

    ap.add_argument("--exec-max-tokens", type=int, default=1200)
    ap.add_argument("--summary-max-tokens", type=int, default=350)
    ap.add_argument("--max-chars", type=int, default=180_000, help="Max extracted chars per file")
    ap.add_argument("--sleep-ms", type=int, default=0, help="Sleep between files (throttle)")
    ap.add_argument("--skip-existing", action="store_true")
    args = ap.parse_args()

    input_root = Path(args.input).expanduser().resolve()
    out_root = Path(args.out).expanduser().resolve()
    out_root.mkdir(parents=True, exist_ok=True)

    gateway_token = args.gateway_token
    if args.engine == "openclaw" and not gateway_token:
        # Best-effort: read local gateway token from the OpenClaw config.
        try:
            cfg_path = Path("~/.openclaw/openclaw.json").expanduser()
            cfg = json.loads(cfg_path.read_text(encoding="utf-8"))
            gateway_token = str(cfg.get("gateway", {}).get("auth", {}).get("token", ""))
        except Exception:
            gateway_token = ""

    if args.engine == "openclaw" and not gateway_token:
        print(
            "ERROR: --gateway-token (or OPENCLAW_GATEWAY_TOKEN) is required for engine=openclaw",
            file=sys.stderr,
        )
        return 2

    prompts = build_prompts(
        token_cap_hint=f"~{args.exec_max_tokens} tokens max",
        exec_max_tokens=args.exec_max_tokens,
        summary_max_tokens=args.summary_max_tokens,
    )

    files = list(iter_input_files(input_root))
    if not files:
        print("No matching files found.", file=sys.stderr)
        return 2

    for p in files:
        try:
            extracted = extract_file(p)
            text = extracted.text
            if len(text) > args.max_chars:
                text = text[: args.max_chars] + "\n\n[TRUNCATED]"

            exec_out = rel_output_path(p, input_root, out_root, ".execution-notes.md")
            sum_out = rel_output_path(p, input_root, out_root, ".tool-summary.md")
            exec_out.parent.mkdir(parents=True, exist_ok=True)

            if args.skip_existing and exec_out.exists() and sum_out.exists():
                continue

            if args.engine == "ollama":
                exec_md = ollama_generate(
                    ollama_url=args.ollama_url,
                    model=args.ollama_model,
                    prompt=prompts.execution_notes + text,
                    num_predict=args.exec_max_tokens,
                )
                sum_md = ollama_generate(
                    ollama_url=args.ollama_url,
                    model=args.ollama_model,
                    prompt=prompts.tool_summary + text,
                    num_predict=args.summary_max_tokens,
                )
            else:
                exec_md = openclaw_generate(
                    gateway_url=args.gateway_url,
                    gateway_token=gateway_token,
                    agent_id=args.gateway_agent_id,
                    prompt=prompts.execution_notes + text,
                    max_output_tokens=args.exec_max_tokens,
                )
                sum_md = openclaw_generate(
                    gateway_url=args.gateway_url,
                    gateway_token=gateway_token,
                    agent_id=args.gateway_agent_id,
                    prompt=prompts.tool_summary + text,
                    max_output_tokens=args.summary_max_tokens,
                )

            exec_out.write_text(exec_md + "\n", encoding="utf-8")
            sum_out.write_text(sum_md + "\n", encoding="utf-8")

            print(f"OK {p} -> {exec_out.relative_to(out_root)} , {sum_out.relative_to(out_root)}")

            if args.sleep_ms:
                time.sleep(args.sleep_ms / 1000.0)

        except Exception as e:
            print(f"ERROR {p}: {e}", file=sys.stderr)

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
