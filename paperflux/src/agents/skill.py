from pathlib import Path


def load_skill(skill_path: Path) -> tuple[dict[str, str], str]:
    """Load a SKILL.md file, returning (frontmatter, body)."""
    text = skill_path.read_text(encoding="utf-8")
    meta: dict[str, str] = {}
    body = text

    if text.startswith("---"):
        parts = text.split("---", 2)
        if len(parts) >= 3:
            for line in parts[1].splitlines():
                line = line.strip()
                if not line or ":" not in line:
                    continue
                key, value = line.split(":", 1)
                meta[key.strip()] = value.strip().strip("'\"")
            body = parts[2].strip()

    return meta, body
