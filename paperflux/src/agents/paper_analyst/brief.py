"""The analyst's "Visual Brief": a machine-facing appendix for the diagram planner.

It is produced in the same model call as the writeup (no extra quota) and split off
before the writeup is stored, so readers never see it.
"""
import re

_BRIEF_HEADING = re.compile(r"^##[ \t]+Visual Brief[ \t]*#*[ \t]*$", re.IGNORECASE | re.MULTILINE)
_NEXT_SECTION = re.compile(r"^##[ \t]+\S", re.MULTILINE)


def split_visual_brief(markdown: str) -> tuple[str, str]:
    """Return (writeup without the brief, brief body). The brief is "" when absent."""
    text = markdown or ""
    heading = _BRIEF_HEADING.search(text)
    if not heading:
        return text, ""
    following = _NEXT_SECTION.search(text, heading.end())
    end = following.start() if following else len(text)
    brief = text[heading.end():end].strip()
    writeup = (text[: heading.start()].rstrip() + "\n\n" + text[end:].lstrip()).strip() + "\n"
    return writeup, brief
