# Tests

Run everything (no network or API keys needed; Gemini/Mongo are mocked):

```bash
MONGODB_URI=mongodb://stub python -m unittest discover -s tests -p "test_*.py" -t .
```

- `test_visuals.py` – flow layout invariants, animated SVG, GIF export, charts, SVG sanitizer
- `test_insights_plan.py` – plan parser, prompt/parser drift guard, insights agent
- `test_llm_clients.py` – Gemini quota handling, fallback chain, OpenAI-compatible client
- `test_processor.py` – non-destructive, budgeted, resumable processing
- `test_app_smoke.py` – boots `app.py` and drives it like a visitor (feed, deep links, tab order, theme toggle, navigation, pending/legacy/empty/processing states)
- `test_ui_helpers.py` – sorting, section splitting, blurbs, schedule rules, time formatting
- `test_theme.py` – the dark-mode toggle still matches the installed Streamlit's theme storage (re-check on upgrades)
- `test_database_state.py` – stale processing-flag healing and progress storage
- `api_test.py`, `pdf_workflow_test.py` – manual scripts that call the real Gemini API (need keys)
