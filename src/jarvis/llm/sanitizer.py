"""Defensive cleanup for assistant responses."""

import re


_THINK_BLOCK = re.compile(
    r"<think>.*?</think>",
    flags=re.IGNORECASE | re.DOTALL,
)
_THINK_CLOSING_TAG = re.compile(r"</think>", flags=re.IGNORECASE)
_RESPONSE_WRAPPER = re.compile(
    r"\s*<response>\s*(.*?)\s*</response>\s*",
    flags=re.IGNORECASE | re.DOTALL,
)


def sanitize_assistant_response(text: str) -> str:
    """Discard tagged reasoning and unwrap one external response tag."""
    closing_tags = list(_THINK_CLOSING_TAG.finditer(text))
    if closing_tags:
        text = text[closing_tags[-1].end():]
    else:
        text = _THINK_BLOCK.sub("", text)

    wrapper = _RESPONSE_WRAPPER.fullmatch(text)
    if wrapper is not None:
        text = wrapper.group(1)
    return text.strip()


sanitize_final_response = sanitize_assistant_response
