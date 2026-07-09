"""System-prompt section alignment vs 2.1.88 (default/non-ant)."""

from hare.constants.prompts import get_system_prompt


def _prompt():
    return get_system_prompt(tools=[], main_loop_model="claude-sonnet-4-20250514")


def test_tone_before_output_efficiency():
    """2.1.88 static order is Tone-and-style THEN Output-efficiency."""
    p = _prompt()
    # locate by stable phrases from each section
    tone = p.find("Tone and style") if "Tone and style" in p else p.lower().find("tone")
    eff = p.lower().find("output without mental overhead")
    if eff == -1:
        eff = p.lower().find("concise")
    assert tone != -1 and eff != -1
    assert tone < eff, "Tone-and-style should precede Output-efficiency (2.1.88 order)"


def test_summarize_tool_results_section_present():
    """2.1.88 includes an unconditional 'write down important tool-result info'
    instruction; hare was missing it."""
    p = _prompt()
    assert "may be cleared later" in p or "write down any important information" in p.lower()
