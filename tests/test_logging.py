"""Paper ids stay attached to log lines, including ones that never mention the paper."""
import io
import logging
import os
import unittest
from unittest.mock import patch

from paperflux.src.logsetup import PaperPrefix, bind_paper, configure_logging


class LoggingTest(unittest.TestCase):
    def test_bound_paper_prefixes_lines_that_omit_the_id(self):
        log = logging.getLogger("paperflux.logging_test")
        log.setLevel(logging.INFO)
        buf = io.StringIO()
        handler = logging.StreamHandler(buf)
        handler.addFilter(PaperPrefix())
        log.addHandler(handler)
        log.propagate = False
        try:
            log.info("server starting")
            with bind_paper("2611.00001"):
                log.info("Gemini gemini-3.8-flash responded in 1.2s")
                log.info("Analyst finished for 2611.00001")
            log.info("idle")
        finally:
            log.removeHandler(handler)
        lines = buf.getvalue().splitlines()
        self.assertEqual(lines[0], "server starting")
        self.assertEqual(lines[1], "[2611.00001] Gemini gemini-3.8-flash responded in 1.2s")
        self.assertEqual(lines[2], "Analyst finished for 2611.00001")
        self.assertEqual(lines[3], "idle")

    def test_level_comes_from_the_environment_and_setup_is_repeatable(self):
        pkg = logging.getLogger("paperflux")
        previous = pkg.level
        try:
            with patch.dict(os.environ, {"PAPERFLUX_LOG_LEVEL": "DEBUG"}):
                configure_logging()
                configure_logging()
            self.assertEqual(pkg.level, logging.DEBUG)
            root = logging.getLogger()
            prefixed = sum(isinstance(f, PaperPrefix) for h in root.handlers for f in h.filters)
            self.assertEqual(prefixed, len(root.handlers))
        finally:
            pkg.setLevel(previous)


if __name__ == "__main__":
    unittest.main()
