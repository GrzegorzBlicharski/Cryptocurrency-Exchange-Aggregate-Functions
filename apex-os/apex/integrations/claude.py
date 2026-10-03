"""Claude (Anthropic API) client + a bounded agentic loop shared by every LLM feature.

- official `anthropic` SDK, model from APEX_CLAUDE_MODEL (default claude-opus-5-5)
- adaptive thinking, effort per call site
- server-side refusal fallbacks (`fallbacks: "default"`), refusal stop reason handled
- prompt caching on the stable prefix (tools + system)
- web_search / web_fetch run on Anthropic's servers; every URL they return is recorded as a
  "seen source" so agents can only save findings whose URL they actually looked at
- `pause_turn` is resumed, steps are capped, client tool errors go back as is_error results
"""
from __future__ import annotations

import json
import logging
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any

from ..config import get_settings

log = logging.getLogger("apex.claude")

FALLBACK_BETA = "server-side-fallback-2026-07-01"
WEB_SEARCH = "web_search_20260209"
WEB_FETCH = "web_fetch_20260209"

_client: Any = None


class ClaudeUnavailable(Exception):
    pass


def set_client(client: Any) -> None:
    """Test hook / custom transport."""
    global _client
    _client = client


def available() -> bool:
    return _client is not None or bool(get_settings().anthropic_api_key)


def client():
    global _client
    if _client is not None:
        return _client
    s = get_settings()
    if not s.anthropic_api_key:
        raise ClaudeUnavailable("ANTHROPIC_API_KEY not configured")
    import anthropic

    _client = anthropic.Anthropic(api_key=s.anthropic_api_key, max_retries=2, timeout=600)
    return _client


def web_tools(max_searches: int = 8, max_fetches: int = 8, blocked_domains: list[str] | None = None) -> list[dict]:
    search = {"type": WEB_SEARCH, "name": "web_search", "max_uses": max_searches}
    fetch = {"type": WEB_FETCH, "name": "web_fetch", "max_uses": max_fetches}
    if blocked_domains:
        search["blocked_domains"] = blocked_domains
        fetch["blocked_domains"] = blocked_domains
    return [search, fetch]


@dataclass
class LoopResult:
    stop: str  # done | step_limit | refused | max_tokens | error
    final_text: str = ""
    steps: list[dict] = field(default_factory=list)
    sources: list[dict] = field(default_factory=list)
    input_tokens: int = 0
    output_tokens: int = 0
    model: str = ""
    error: str = ""


def _blocks_to_params(content) -> list:
    """Echo assistant content back verbatim (thinking, server tool blocks, fallback markers...)."""
    out = []
    for b in content:
        out.append(b.model_dump(exclude_none=True) if hasattr(b, "model_dump") else b)
    return out


def _collect_sources(content, sources: list[dict], seen: set[str]) -> None:
    for b in content:
        t = getattr(b, "type", "")
        if t == "web_search_tool_result":
            items = getattr(b, "content", None)
            if isinstance(items, list):  # error results are a single object, not a list
                for it in items:
                    url = getattr(it, "url", None)
                    if url and url not in seen:
                        seen.add(url)
                        sources.append({"url": url, "title": getattr(it, "title", "") or ""})
        elif t == "web_fetch_tool_result":
            res = getattr(b, "content", None)
            url = getattr(res, "url", None)
            if url and url not in seen:
                seen.add(url)
                sources.append({"url": url, "title": ""})


def _short(v: Any, n: int = 300) -> str:
    s = v if isinstance(v, str) else json.dumps(v, ensure_ascii=False, default=str)
    return s if len(s) <= n else s[: n - 1] + "…"


def run_loop(
    *,
    system: str,
    messages: list,
    tools: list[dict],
    handlers: dict[str, Callable[[dict, "LoopContext"], Any]],
    max_steps: int,
    effort: str | None = None,
    max_tokens: int = 16000,
    token_budget_left: int | None = None,
) -> LoopResult:
    """Run Claude with tools until it finishes, refuses, or hits a limit. Never raises on model behavior."""
    s = get_settings()
    res = LoopResult(stop="done", model=s.claude_model)
    ctx = LoopContext(res)
    seen: set[str] = set()
    try:
        c = client()
    except ClaudeUnavailable as exc:
        res.stop, res.error = "error", str(exc)
        return res
    for step in range(max_steps):
        if token_budget_left is not None and res.input_tokens + res.output_tokens >= token_budget_left:
            res.stop = "budget"
            break
        try:
            resp = c.beta.messages.create(
                model=s.claude_model,
                max_tokens=max_tokens,
                system=system,
                messages=messages,
                tools=tools,
                thinking={"type": "adaptive"},
                output_config={"effort": effort or s.claude_effort},
                cache_control={"type": "ephemeral"},
                betas=[FALLBACK_BETA],
                fallbacks="default",
            )
        except Exception as exc:  # SDK retries 429/5xx itself; anything left ends this run cleanly
            log.warning("claude request failed: %s", exc)
            res.stop, res.error = "error", f"{type(exc).__name__}: {str(exc)[:300]}"
            return res
        usage = getattr(resp, "usage", None)
        res.input_tokens += int(getattr(usage, "input_tokens", 0) or 0) + int(
            getattr(usage, "cache_creation_input_tokens", 0) or 0)
        res.output_tokens += int(getattr(usage, "output_tokens", 0) or 0)
        res.model = getattr(resp, "model", res.model)
        content = resp.content
        _collect_sources(content, res.sources, seen)
        text = "".join(getattr(b, "text", "") for b in content if getattr(b, "type", "") == "text").strip()
        if text:
            res.final_text = text
        stop = resp.stop_reason
        if stop == "refusal":
            res.stop = "refused"
            details = getattr(resp, "stop_details", None)
            res.error = f"declined ({getattr(details, 'category', None) or 'unspecified'})"
            return res
        messages.append({"role": "assistant", "content": _blocks_to_params(content)})
        if stop == "pause_turn":  # server tool loop paused; resend as-is to resume
            continue
        if stop == "max_tokens":
            res.stop = "max_tokens"
            return res
        tool_uses = [b for b in content if getattr(b, "type", "") == "tool_use"]
        if stop != "tool_use" or not tool_uses:
            res.stop = "done"
            return res
        results = []
        for tu in tool_uses:
            fn = handlers.get(tu.name)
            inp = tu.input if isinstance(tu.input, dict) else {}
            try:
                if fn is None:
                    raise ToolError(f"unknown tool {tu.name}")
                out = fn(inp, ctx)
                ok = True
            except ToolError as exc:
                out, ok = f"Error: {exc}", False
            except Exception as exc:  # a tool bug must not kill the run; Claude sees the error
                log.exception("tool %s failed", tu.name)
                out, ok = f"Error: internal failure in {tu.name}: {type(exc).__name__}", False
            payload = out if isinstance(out, str) else json.dumps(out, ensure_ascii=False, default=str)
            results.append({"type": "tool_result", "tool_use_id": tu.id, "content": payload[:20000],
                            **({} if ok else {"is_error": True})})
            res.steps.append({"step": step, "tool": tu.name, "input": _short(inp), "result": _short(payload), "ok": ok})
            if ctx.stop_requested:
                break
        messages.append({"role": "user", "content": results})
        if ctx.stop_requested:
            res.stop = "done"
            return res
    else:
        res.stop = "step_limit"
    return res


class ToolError(Exception):
    """Raised by a tool handler to return a clean error message to Claude."""


class LoopContext:
    def __init__(self, res: LoopResult):
        self.res = res
        self.stop_requested = False

    def seen_url(self, url: str) -> bool:
        u = url.rstrip("/")
        return any(s["url"].rstrip("/") == u for s in self.res.sources)


def structured(system: str, prompt: str, schema: dict, effort: str = "low", max_tokens: int = 8000) -> dict:
    """Single call with a guaranteed-JSON response (output_config.format)."""
    s = get_settings()
    c = client()
    try:
        resp = c.beta.messages.create(
            model=s.claude_model, max_tokens=max_tokens, system=system,
            messages=[{"role": "user", "content": prompt}],
            thinking={"type": "adaptive"},
            output_config={"effort": effort, "format": {"type": "json_schema", "schema": schema}},
            betas=[FALLBACK_BETA], fallbacks="default",
        )
    except Exception as exc:
        raise ClaudeUnavailable(f"request failed: {type(exc).__name__}: {str(exc)[:200]}") from None
    if resp.stop_reason == "refusal":
        raise ClaudeUnavailable("declined by the model")
    text = next((b.text for b in resp.content if getattr(b, "type", "") == "text"), "")
    try:
        return json.loads(text)
    except ValueError:
        raise ClaudeUnavailable("invalid JSON in response") from None
