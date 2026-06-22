"""Provider-agnostic LLM layer.

The rest of the app calls `generate_json(...)` and `generate_text(...)` without
caring which model answers. Provider is chosen in config:
    Gemini (default, free tier)  →  Claude (fallback)  →  templated (no key).

Every function fails soft: on any error or missing key it returns None/"" and
the calling engine substitutes a deterministic, still-useful result. That is
what lets the live demo keep working even if a key is missing or a network
call flakes mid-presentation.
"""
import json
import re
import time

import config

_TRANSIENT = ("503", "UNAVAILABLE", "429", "RESOURCE_EXHAUSTED",
              "overloaded", "500", "INTERNAL", "DEADLINE")

_gemini_client = None
_claude_client = None


def _gemini():
    global _gemini_client
    if _gemini_client is None:
        from google import genai
        _gemini_client = genai.Client(api_key=config.GEMINI_API_KEY)
    return _gemini_client


def _claude():
    global _claude_client
    if _claude_client is None:
        import anthropic
        _claude_client = anthropic.Anthropic(api_key=config.ANTHROPIC_API_KEY)
    return _claude_client


def _gemini_generate(prompt, system, temperature, max_tokens, json_mode):
    """Single Gemini call. Thinking is disabled: on Flash models, default
    'thinking' can consume the whole max_output_tokens budget and return empty
    text. We retry once without the knob in case a model rejects it."""
    from google.genai import types
    base = dict(system_instruction=system or None, temperature=temperature,
                max_output_tokens=max_tokens)
    if json_mode:
        base["response_mime_type"] = "application/json"
    # variant 0 disables 'thinking' (Flash budget guard); variant 1 is plain
    variants = [{"thinking_config": types.ThinkingConfig(thinking_budget=0)}, {}]
    vi, last = 0, None
    for attempt in range(4):
        try:
            resp = _gemini().models.generate_content(
                model=config.GEMINI_MODEL, contents=prompt,
                config=types.GenerateContentConfig(**base, **variants[vi]))
            text = resp.text or ""
            if text.strip():
                return text
            vi = min(vi + 1, len(variants) - 1)  # empty → try other variant
        except Exception as exc:
            last, s = exc, str(exc)
            if any(code in s for code in _TRANSIENT):
                time.sleep(1.0 + attempt)        # transient: back off and retry
                continue
            if "thinking" in s.lower() and vi == 0:
                vi = 1                            # model rejected the knob
                continue
            raise
    if last:
        raise last
    return ""


def _extract_json(text: str):
    """Parse a JSON object out of an LLM response, tolerating stray prose or
    ```json fences that some models still emit."""
    if not text:
        return None
    text = text.strip()
    try:
        return json.loads(text)
    except Exception:
        pass
    fenced = re.search(r"```(?:json)?\s*(\{.*?\})\s*```", text, re.DOTALL)
    if fenced:
        try:
            return json.loads(fenced.group(1))
        except Exception:
            pass
    brace = re.search(r"\{.*\}", text, re.DOTALL)
    if brace:
        try:
            return json.loads(brace.group(0))
        except Exception:
            pass
    return None


def generate_text(prompt: str, system: str = "", temperature: float = 0.6,
                  max_tokens: int = 1024) -> str:
    """Return free-form text, or "" on failure / no provider."""
    provider = config.LLM_PROVIDER
    try:
        if provider == "gemini":
            return _gemini_generate(prompt, system, temperature, max_tokens, False).strip()
        if provider == "claude":
            resp = _claude().messages.create(
                model=config.CLAUDE_MODEL, max_tokens=max_tokens,
                system=system or "",
                messages=[{"role": "user", "content": prompt}])
            return "".join(b.text for b in resp.content if b.type == "text").strip()
    except Exception as exc:
        print(f"[llm] generate_text error ({provider}): {exc}")
    return ""


def generate_json(prompt: str, system: str = "", temperature: float = 0.4,
                  max_tokens: int = 2048):
    """Return a parsed dict, or None on failure / no provider. The prompt
    should specify the exact JSON shape required."""
    provider = config.LLM_PROVIDER
    try:
        if provider == "gemini":
            return _extract_json(
                _gemini_generate(prompt, system, temperature, max_tokens, True))
        if provider == "claude":
            resp = _claude().messages.create(
                model=config.CLAUDE_MODEL, max_tokens=max_tokens,
                system=(system + "\n\nRespond with ONLY valid JSON, no prose.").strip(),
                messages=[{"role": "user", "content": prompt}])
            text = "".join(b.text for b in resp.content if b.type == "text")
            return _extract_json(text)
    except Exception as exc:
        print(f"[llm] generate_json error ({provider}): {exc}")
    return None
