from __future__ import annotations

from dataclasses import dataclass


@dataclass
class PromptPair:
    execution_notes_prompt: str
    tool_summary_prompt: str


def build_prompts(*, token_cap_hint: str = "~1200 tokens max") -> PromptPair:
    # These prompts are designed to be copy/pasted into Cosmo.
    execution_notes = f"""You are an expert at converting documentation into EXECUTION-RELEVANT notes for an AI agent that can run tools (CLI commands, HTTP calls, scripts, and functions).

OUTPUT FORMAT (Markdown):
- Title
- What this tool/service is
- When to use
- Inputs (required/optional)
- Outputs
- Preconditions / assumptions
- Step-by-step 'golden path' (numbered)
- Verification checks (how to confirm success)
- Common errors + fixes
- Safety/rollback notes (what not to do / how to undo)

RULES:
- Be concise and operational; no marketing.
- Prefer concrete commands, flags, endpoints, and example payloads.
- If the doc is long, extract only what is needed to execute.
- Keep the final output within {token_cap_hint}.

SOURCE DOCUMENT (extracted text) is below. Use it as the only source of truth.
---
"""

    tool_summary = f"""You are an expert at writing ultra-condensed TOOL INDEX summaries for an agent tool library.

Write a short Markdown file with:
- Tool name
- 1-sentence purpose
- Capabilities (3-7 bullets)
- Required environment/dependencies
- Primary entrypoints (commands/endpoints)
- Key limitations / footguns (1-3 bullets)

RULES:
- No fluff. Assume the reader is an executor agent.
- Keep within ~250-400 tokens.

SOURCE DOCUMENT (extracted text) is below. Use it as the only source of truth.
---
"""

    return PromptPair(execution_notes_prompt=execution_notes, tool_summary_prompt=tool_summary)
