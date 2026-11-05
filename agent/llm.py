"""Thin wrapper around the Claude API with the server-side web search tool.

Handles `pause_turn` continuation, collects citations + the queries Claude ran,
and falls back to the basic web search tool version if the newer one is not
available on the account/model.
"""
from __future__ import annotations

import time
from dataclasses import dataclass, field

import anthropic

from .utils import extract_json, log

BASIC_SEARCH_TOOL = "web_search_20250305"


@dataclass
class LLMResult:
    text: str
    citations: list[dict] = field(default_factory=list)  # {url, title, cited_text}
    queries: list[str] = field(default_factory=list)
    result_urls: list[dict] = field(default_factory=list)  # every page the search returned

    def json(self):
        return extract_json(self.text)


class Claude:
    def __init__(self, search_tool: str = "web_search_20260318", max_tokens: int = 16000):
        self.client = anthropic.Anthropic()  # reads ANTHROPIC_API_KEY
        self.search_tool = search_tool
        self.max_tokens = max_tokens

    # ------------------------------------------------------------------
    def _tools(self, max_searches: int, blocked_domains: list[str] | None):
        tool = {"type": self.search_tool, "name": "web_search", "max_uses": max_searches}
        if blocked_domains:
            tool["blocked_domains"] = blocked_domains
        return [tool]

    def _create(self, **kwargs):
        for attempt in range(4):
            try:
                return self.client.messages.create(**kwargs)
            except anthropic.BadRequestError as e:
                # Older accounts/models may not have the newest tool version.
                if "tools" in kwargs and self.search_tool != BASIC_SEARCH_TOOL and "web_search" in str(e):
                    log(f"Search tool {self.search_tool} rejected; falling back to {BASIC_SEARCH_TOOL}")
                    self.search_tool = BASIC_SEARCH_TOOL
                    for t in kwargs["tools"]:
                        t["type"] = BASIC_SEARCH_TOOL
                    continue
                raise
            except (anthropic.RateLimitError, anthropic.APIConnectionError, anthropic.InternalServerError) as e:
                wait = 15 * (attempt + 1)
                log(f"API busy ({type(e).__name__}); retrying in {wait}s")
                time.sleep(wait)
        raise RuntimeError("Claude API kept failing after retries")

    # ------------------------------------------------------------------
    def ask(
        self,
        model: str,
        system: str,
        user: str,
        *,
        search: bool = True,
        max_searches: int = 10,
        blocked_domains: list[str] | None = None,
    ) -> LLMResult:
        messages = [{"role": "user", "content": user}]
        kwargs = dict(model=model, max_tokens=self.max_tokens, system=system, messages=messages)
        if search:
            kwargs["tools"] = self._tools(max_searches, blocked_domains)

        result = LLMResult(text="")
        final_text_parts: list[str] = []
        for _ in range(10):  # pause_turn continuations
            resp = self._create(**kwargs)
            final_text_parts = []
            for block in resp.content:
                btype = getattr(block, "type", "")
                if btype == "text":
                    final_text_parts.append(block.text)
                    for c in getattr(block, "citations", None) or []:
                        url = getattr(c, "url", None)
                        if url:
                            result.citations.append(
                                {"url": url, "title": getattr(c, "title", ""), "cited_text": getattr(c, "cited_text", "")}
                            )
                elif btype == "server_tool_use" and getattr(block, "name", "") == "web_search":
                    q = (getattr(block, "input", None) or {}).get("query")
                    if q:
                        result.queries.append(q)
                elif btype == "web_search_tool_result":
                    content = getattr(block, "content", None)
                    if isinstance(content, list):
                        for r in content:
                            if getattr(r, "url", None):
                                result.result_urls.append(
                                    {"url": r.url, "title": getattr(r, "title", ""), "page_age": getattr(r, "page_age", None)}
                                )
            if resp.stop_reason == "pause_turn":
                # Send the paused turn back unchanged so Claude can continue.
                messages.append({"role": "assistant", "content": resp.content})
                continue
            break
        result.text = "\n".join(final_text_parts)
        return result

    # ------------------------------------------------------------------
    def ask_json(self, model: str, system: str, user: str, **kw):
        """ask() and parse JSON; one repair attempt if the JSON is broken."""
        res = self.ask(model, system, user, **kw)
        try:
            return res.json(), res
        except ValueError:
            log("Model returned invalid JSON; asking it to repair")
            fix = self.ask(
                model,
                "You convert text into valid JSON. Output only one ```json fenced block.",
                "Turn this into valid JSON matching the structure it was meant to have. "
                "Do not add or invent facts.\n\n" + res.text,
                search=False,
            )
            return fix.json(), res


# ---------------------------------------------------------------- subscription
class ClaudeCode:
    """Same interface as Claude, but runs on your Claude subscription through the
    Claude Code command line (`claude -p`) instead of the API key.

    Web search and page fetches are Claude Code's own tools; nothing else (no shell,
    no file edits) is allowed. ANTHROPIC_API_KEY is removed from its environment so
    it can never fall back to billing the API.
    """

    def __init__(self, cfg: dict):
        self.bin = find_claude_code(cfg)
        self.max_turns = cfg.get("llm", {}).get("max_turns", 40)

    def ask(self, model: str, system: str, user: str, *, search: bool = True, max_searches: int = 10,
            blocked_domains: list[str] | None = None) -> LLMResult:
        import json as _json
        import os
        import subprocess
        import tempfile

        rules = system
        if search:
            rules += (f"\n\nUse the WebSearch and WebFetch tools to look things up (at most about {max_searches} "
                      "searches). Only cite pages you actually read.")
            if blocked_domains:
                rules += f" Never use these domains: {', '.join(blocked_domains)}."
        tools = ["WebSearch", "WebFetch"] if search else []
        cmd = [str(self.bin), "-p", user, "--output-format", "json", "--model", model, "--system-prompt", rules,
               "--max-turns", str(self.max_turns),
               "--disallowedTools", "Bash", "Edit", "Write", "NotebookEdit", "Task", "Agent"]
        if tools:
            cmd += ["--allowedTools", *tools]
        env = {k: v for k, v in os.environ.items() if k not in ("ANTHROPIC_API_KEY", "ANTHROPIC_AUTH_TOKEN")}
        for attempt in range(3):
            with tempfile.TemporaryDirectory() as cwd:  # neutral folder: no project files steer it
                p = subprocess.run(cmd, capture_output=True, text=True, env=env, cwd=cwd, stdin=subprocess.DEVNULL)
            try:
                out = _json.loads(p.stdout)
            except ValueError:
                out = {"is_error": True, "result": (p.stdout or p.stderr)[-500:]}
            if not out.get("is_error") and out.get("result"):
                return LLMResult(text=out["result"])
            msg = str(out.get("result") or p.stderr)[-300:]
            if "limit" in msg.lower():
                raise RuntimeError(f"Claude subscription usage limit reached: {msg}. Try again later or use the API.")
            log(f"Claude Code call failed ({msg}); retrying")
            time.sleep(10 * (attempt + 1))
        raise RuntimeError("Claude Code kept failing; check you're signed in to the Claude app")

    ask_json = Claude.ask_json


def find_claude_code(cfg: dict):
    """The `claude` command: config llm.claude_code_path, then PATH, then the copy inside the Claude app."""
    import glob
    import shutil
    from pathlib import Path

    custom = cfg.get("llm", {}).get("claude_code_path")
    if custom and Path(custom).exists():
        return Path(custom)
    on_path = shutil.which("claude")
    if on_path:
        return Path(on_path)
    found = glob.glob(str(Path.home() / "Library/Application Support/Claude/claude-code/*/*/claude.app/Contents/MacOS/claude"))
    if found:
        def ver(p):
            return [int(x) for x in Path(p).parts[-6].split(".") if x.isdigit()]
        return Path(max(found, key=ver))
    raise RuntimeError("Claude Code not found. Open the Claude desktop app once, or set llm.claude_code_path in config.json.")


BRAINS = {"api": "API key - pay per use, no limits, works on a schedule",
          "subscription": "Claude subscription - included in your plan, uses its usage limits"}


def ask_brain(cfg: dict) -> str:
    """Ask who does research, fact-checking and writing. Enter keeps the config default;
    scheduled runs (no keyboard) use the default without asking."""
    import sys

    default = cfg.get("llm", {}).get("backend", "api")
    if not sys.stdin.isatty():
        return default
    names = list(BRAINS)
    print("\nWho should do the research, fact-checking and writing?")
    for i, n in enumerate(names, 1):
        print(f"  {i}. {BRAINS[n]}" + ("   (default)" if n == default else ""))
    while True:
        try:
            ans = input(f"Pick 1-{len(names)} · Enter = {default}: ").strip().lower()
        except EOFError:
            return default
        if not ans:
            return default
        if ans.isdigit() and 1 <= int(ans) <= len(names):
            return names[int(ans) - 1]
        if ans in BRAINS or ans in ("sub", "plan"):
            return "subscription" if ans in ("sub", "plan") else ans
        print("  Type 1 or 2, or press Enter.")


def make_claude(cfg: dict, brain: str | None = None):
    brain = brain or cfg.get("llm", {}).get("backend", "api")
    if brain == "subscription":
        log("Using your Claude subscription (Claude Code) for research, fact-checks and writing")
        return ClaudeCode(cfg)
    return Claude(search_tool=cfg["web_search_tool"])
