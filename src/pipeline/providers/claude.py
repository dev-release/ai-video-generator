"""Claude: idea + lines -> ScriptDraft (structured output). Spec: .claude/specs/script.md."""

from __future__ import annotations

import copy
from pathlib import Path

import anthropic

from pipeline.config import Settings
from pipeline.providers import SpecProvider
from pipeline.registry import ModelSpec
from pipeline.retry import submit_retry
from pipeline.schema import Brief, ScriptDraft
from pipeline.voices import ConfigError

SYSTEM = (Path(__file__).parents[1] / "prompts" / "script.md").read_text()
FALLBACK_BETA = "server-side-fallback-2026-07-01"
# Constraints checked by pydantic after the response (and the fix loop), not by the JSON schema:
# structured outputs do not support every JSON Schema keyword.
_UNSUPPORTED = {"pattern", "minLength", "maxLength", "minItems", "maxItems", "default", "title"}


class ScriptRefused(RuntimeError):
    pass


def output_schema() -> dict:
    def strip(node, names: bool = False):
        # names=True is a properties map: its keys are FIELD NAMES (e.g. the script's `title`) and
        # stay; only schema keywords are stripped (a live smoke once lost the `title` field).
        if isinstance(node, dict):
            return {k: strip(v, k == "properties" and not names) for k, v in node.items()
                    if names or k not in _UNSUPPORTED}  # fmt: skip
        if isinstance(node, list):
            return [strip(v) for v in node]
        return node

    schema = strip(copy.deepcopy(ScriptDraft.model_json_schema()))
    for obj in [schema, *schema.get("$defs", {}).values()]:
        if obj.get("type") == "object":
            obj["required"] = list(obj["properties"])
            obj["additionalProperties"] = False
    # Gender: null is allowed only for old saved scripts; the model must state it.
    gender = schema["$defs"]["Character"]["properties"]["gender"]
    gender.update(next(b for b in gender.pop("anyOf") if b.get("type") != "null"))
    return schema


def actual_cost(spec: ModelSpec, usage) -> float | None:
    """Actual cost from usage x the entry's token prices (thinking counts as output tokens)."""
    p = spec.price
    if usage is None or p.in_per_mtok is None or p.out_per_mtok is None:
        return None
    tokens_in = (usage.input_tokens or 0) + (getattr(usage, "cache_creation_input_tokens", 0) or 0)
    cached = getattr(usage, "cache_read_input_tokens", 0) or 0
    return round(
        (tokens_in * p.in_per_mtok + cached * p.in_per_mtok * 0.1 + (usage.output_tokens or 0)
         * p.out_per_mtok) / 1e6, 6)  # fmt: skip


def user_prompt(brief: Brief, feedback: str | None) -> str:
    lines = "\n".join(f"{i + 1}. {ln}" for i, ln in enumerate(brief.lines))
    msg = f"Idea: {brief.idea}\n\nLines ({len(brief.lines)}, one shot each):\n{lines}"
    if feedback:
        msg += f"\n\nPrevious attempt was invalid:\n{feedback}"
    return msg


class ClaudeScriptWriter(SpecProvider):
    """anthropic transport for any Claude model in the registry (model id = spec.endpoint)."""

    def __init__(
        self, spec: ModelSpec, settings: Settings, client: anthropic.Anthropic | None = None
    ) -> None:
        self.spec = spec
        self.settings = settings
        self._client = client
        self.last_cost_usd: float | None = None  # actual cost of the last call (usage)

    @property
    def client(self) -> anthropic.Anthropic:
        # Lazy client: the provider builds without a key, so preflight can list all problems.
        if self._client is None:
            if not self.settings.anthropic_api_key:
                raise ConfigError("ANTHROPIC_API_KEY is not set — see .env.example")
            self._client = anthropic.Anthropic(
                api_key=self.settings.anthropic_api_key,
                max_retries=0,
                timeout=self.spec.timeout_s,
            )
        return self._client

    def ready(self) -> None:
        _ = self.client

    @submit_retry
    def _call(self, brief: Brief, feedback: str | None):
        return self.client.beta.messages.create(
            model=self.spec.endpoint,
            max_tokens=16000,
            system=SYSTEM,
            messages=[{"role": "user", "content": user_prompt(brief, feedback)}],
            output_config={
                "effort": "medium",
                "format": {"type": "json_schema", "schema": output_schema()},
            },
            betas=[FALLBACK_BETA],
            fallbacks="default",
        )

    def write(self, brief: Brief, feedback: str | None) -> ScriptDraft:
        resp = self._call(brief, feedback)
        self.last_cost_usd = actual_cost(self.spec, resp.usage)
        if resp.stop_reason == "refusal":
            cat = resp.stop_details.category if resp.stop_details else None
            raise ScriptRefused(f"the model refused (category: {cat}) — change the idea")
        text = next((b.text for b in resp.content if b.type == "text"), "")
        # A ValidationError goes to the script node's fix loop, not to a network retry.
        return ScriptDraft.model_validate_json(text)
