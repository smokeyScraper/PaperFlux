"""The two-host podcast script: schema for the writer, parser, and the TTS prompt.

The writer returns turns as JSON. We clean every line so a TTS voice can say it (no
markdown, LaTeX, URLs, or stage directions), enforce the length budget the TTS quota allows,
and build one multi-speaker prompt per episode.
"""
from __future__ import annotations

import re
from typing import Any, Optional, Sequence

from paperflux.src.agents.paper_insights.plan import extract_json

MIN_TURNS = 10
MAX_TURN_WORDS = 110
MIN_WORDS = 300
WORDS_PER_MINUTE = 140  # calm, unhurried delivery
LENGTH_TOLERANCE = 1.25  # 700 target words -> at most ~6 minutes, well inside one TTS reply

_STR = {"type": "STRING"}


def script_schema(hosts: Sequence[dict[str, str]]) -> dict[str, Any]:
    return {
        "type": "OBJECT",
        "properties": {
            "title": _STR,
            "summary": _STR,
            "turns": {
                "type": "ARRAY",
                "items": {
                    "type": "OBJECT",
                    "properties": {
                        "speaker": {"type": "STRING", "enum": [h["name"] for h in hosts]},
                        "text": _STR,
                    },
                    "required": ["speaker", "text"],
                },
            },
        },
        "required": ["title", "turns"],
    }


_SPOKEN = [
    (re.compile(r"https?://\S+|www\.\S+"), ""),
    (re.compile(r"\[[^\]]*\]"), ""),  # [laughs], [12]
    (re.compile(r"[*(]\s*(?:laughs?|chuckles?|sighs?|pauses?|smiles?|music[^)*]*)\s*[*)]", re.I), ""),
    (re.compile(r"\$+([^$]*)\$+"), r"\1"),  # inline LaTeX: keep the content
    (re.compile(r"\\(?:mathrm|text|mathbf|operatorname)\{([^}]*)\}"), r"\1"),
    (re.compile(r"\\([a-zA-Z]+)"), r"\1"),  # \alpha -> alpha
    (re.compile(r"[{}_^`#|]|\*\*?"), " "),
    (re.compile(r"\be\.g\.,?", re.I), "for example,"),
    (re.compile(r"\bi\.e\.,?", re.I), "that is,"),
    (re.compile(r"\s*&\s*"), " and "),
    (re.compile(r"(?<=\d)\s*[x×]\s*(?=\d)"), " by "),
    (re.compile(r"\s*×\s*"), " times "),
    (re.compile(r"≈|~(?=\s*\d)"), "about "),
    (re.compile(r"≥"), " at least "),
    (re.compile(r"≤"), " at most "),
    (re.compile(r"→|->"), " to "),
    (re.compile(r"\s+"), " "),
]


def speakable(text: str) -> str:
    out = str(text or "")
    for pattern, repl in _SPOKEN:
        out = pattern.sub(repl, out)
    out = re.sub(r"\s+([,.!?;:])", r"\1", out)
    return out.strip(" -")


def _clip_words(text: str, limit: int) -> str:
    words = text.split()
    if len(words) <= limit:
        return text
    clipped = " ".join(words[:limit])
    cut = max(clipped.rfind(". "), clipped.rfind("? "), clipped.rfind("! "))
    return clipped[: cut + 1] if cut > len(clipped) // 2 else clipped.rstrip(",;:") + "."


def _speaker(raw: Any, hosts: Sequence[dict[str, str]]) -> Optional[str]:
    value = str(raw or "").strip().lower()
    for host in hosts:
        if value in {host["name"].lower(), host["gender"]}:
            return host["name"]
    aliases = {"host a": 0, "a": 0, "host 1": 0, "speaker 1": 0, "host b": 1, "b": 1, "host 2": 1, "speaker 2": 1}
    return hosts[aliases[value]]["name"] if value in aliases else None


def parse_script(raw_text: str, hosts: Sequence[dict[str, str]], target_words: int
                 ) -> tuple[Optional[dict[str, Any]], list[str]]:
    """(script, problems). The script is None when it is not worth recording."""
    data = extract_json(raw_text)
    if data is None:
        return None, ["Output did not contain a JSON object"]
    problems: list[str] = []
    turns: list[dict[str, str]] = []
    for item in data.get("turns") or []:
        if not isinstance(item, dict):
            continue
        speaker = _speaker(item.get("speaker"), hosts)
        text = _clip_words(speakable(item.get("text")), MAX_TURN_WORDS)
        if speaker is None:
            problems.append(f"Unknown speaker {str(item.get('speaker'))[:30]!r}")
            continue
        if not text:
            continue
        if turns and turns[-1]["speaker"] == speaker:  # one voice twice in a row reads as one turn
            turns[-1]["text"] += " " + text
        else:
            turns.append({"speaker": speaker, "text": text})

    budget = int(target_words * LENGTH_TOLERANCE)
    if _words(turns) > budget and len(turns) > 2:  # keep the opening, the closing, and what fits between
        closing, kept = turns[-1], []
        for turn in turns[:-1]:
            if _words(kept) + len(turn["text"].split()) + len(closing["text"].split()) > budget:
                break
            kept.append(turn)
        if kept and kept[-1]["speaker"] == closing["speaker"]:
            kept.pop()  # keep the sign-off, alternate voices
        turns = kept + [closing]
        problems.append(f"Script was longer than {budget} words and was shortened")

    words = _words(turns)
    speakers = {t["speaker"] for t in turns}
    if len(turns) < MIN_TURNS:
        problems.append(f"Only {len(turns)} turns (need at least {MIN_TURNS})")
    if len(speakers) < 2:
        problems.append("Both hosts must speak")
    if words < MIN_WORDS:
        problems.append(f"Only {words} words (need at least {MIN_WORDS})")
    usable = len(turns) >= MIN_TURNS and len(speakers) == 2 and words >= MIN_WORDS
    script = {
        "title": speakable(data.get("title"))[:90] or "PaperFlux conversation",
        "summary": speakable(data.get("summary"))[:300],
        "turns": turns,
        "words": words,
        "minutes": round(words / WORDS_PER_MINUTE, 1),
    }
    return (script if usable else None), problems


def _words(turns: list[dict[str, str]]) -> int:
    return sum(len(t["text"].split()) for t in turns)


# Gemini 3.8 TTS speaks ``text`` verbatim, so this direction is attached as
# speech metadata on each turn and never written into the line itself.
DELIVERY = (
    "Calm, warm, and unhurried, like two friends talking about a research paper late in the evening. "
    "Soft emphasis, a relaxed pace, curious and sincere."
)


def speech_turns(script: dict[str, Any]) -> list[tuple[str, str]]:
    """(speaker, verbatim line) for each turn. The speaker is metadata, not part of the line."""
    turns = []
    for turn in script.get("turns") or []:
        text = " ".join(str(turn.get("text") or "").split())
        speaker = str(turn.get("speaker") or "").strip()
        if speaker and text:
            turns.append((speaker, text))
    return turns
