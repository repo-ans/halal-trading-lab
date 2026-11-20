"""The optional LLM mode. One attempt, no retry inside this module -- the
retry-once and the daily/attempts cap both live in the orchestration layer
(app/services/ai_summary.py), since both need to see every attempt across
every call, not just this one. The API key is read once by the caller and
passed in as a plain string for this one call; this module never logs it,
never echoes it in an exception message, and never returns it."""
import anthropic

MAX_RESPONSE_TOKENS = 600


class LlmCallError(Exception):
    """Any failure calling the LLM -- timeout, network error, empty response.
    Deliberately generic: the caller (app/services/ai_summary.py) treats every
    LlmCallError the same way, by falling back to the template."""


def _build_prompt(allowed_placeholders: frozenset[str]) -> str:
    placeholder_list = ", ".join(sorted(f"{{{p}}}" for p in allowed_placeholders))
    return (
        "Write a 3-5 sentence plain-language summary of a paper-trading backtest "
        "run (simulated, not a real trade). Use ONLY the following placeholders, "
        "written exactly as shown, for every number in the text -- never write a "
        f"digit or a number word yourself: {placeholder_list}. Do not use any "
        "other placeholder, and do not use curly braces for any other purpose. "
        "Never give advice, a recommendation, a prediction, a forecast, or a "
        "halal/haram ruling. Never use words like: should, recommend, advise, "
        "likely, expect, forecast, opportunity, predict, guarantee, profit, "
        "earned, halal, haram. Never include a URL or a web address. Write only "
        "the summary prose itself, nothing else -- no preamble, no heading."
    )


def call_llm(*, api_key: str, model: str, timeout_seconds: float,
            allowed_placeholders: frozenset[str]) -> str:
    """Returns the raw (pre-substitution, pre-guard) text. Raises LlmCallError
    on any failure -- never returns a partial or best-effort string."""
    client = anthropic.Anthropic(api_key=api_key, timeout=timeout_seconds)
    prompt = _build_prompt(allowed_placeholders)
    try:
        response = client.messages.create(
            model=model, max_tokens=MAX_RESPONSE_TOKENS,
            messages=[{"role": "user", "content": prompt}],
        )
    except Exception as exc:
        # Never str(exc) into a log line or a stored/returned message -- the
        # SDK's own exception text is not something this module inspects or
        # repeats, specifically so there is no path by which request/response
        # detail (headers included) could ever surface.
        raise LlmCallError("LLM call failed") from exc

    text = "".join(block.text for block in response.content if block.type == "text")
    if not text.strip():
        raise LlmCallError("LLM returned no text")
    return text
