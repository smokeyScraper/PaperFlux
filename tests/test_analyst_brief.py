import unittest

from paperflux.src.agents.paper_analyst.brief import split_visual_brief
from paperflux.src.agents.skill import load_skill
from paperflux.src.config.settings import AGENTS_DIR


class SplitVisualBriefTest(unittest.TestCase):
    def test_brief_at_the_end_is_split_off(self):
        text = "# T\n\n## TL;DR\n- a\n\n## Visual Brief\n### Components\n- Encoder | new: yes\n"
        writeup, brief = split_visual_brief(text)
        self.assertEqual(brief, "### Components\n- Encoder | new: yes")
        self.assertEqual(writeup, "# T\n\n## TL;DR\n- a\n")

    def test_sections_after_the_brief_are_kept(self):
        writeup, brief = split_visual_brief("## A\nx\n\n## Visual Brief\n### Components\n- y\n\n## Glossary\n- z\n")
        self.assertEqual(brief, "### Components\n- y")
        self.assertIn("## Glossary\n- z", writeup)
        self.assertNotIn("Components", writeup)

    def test_no_brief_leaves_the_writeup_untouched(self):
        self.assertEqual(split_visual_brief("## A\nx\n"), ("## A\nx\n", ""))
        self.assertEqual(split_visual_brief(""), ("", ""))
        self.assertEqual(split_visual_brief(None), ("", ""))

    def test_split_is_idempotent(self):
        writeup, _ = split_visual_brief("## A\nx\n\n## Visual Brief\n- y\n")
        self.assertEqual(split_visual_brief(writeup), (writeup, ""))

    def test_analyst_prompt_asks_for_the_brief_last(self):
        _, body = load_skill(AGENTS_DIR / "paper_analyst" / "SKILL.md")
        headings = [line for line in body.splitlines() if line.startswith("## ")]
        self.assertEqual(headings[-1], "## Visual Brief")


if __name__ == "__main__":
    unittest.main()
