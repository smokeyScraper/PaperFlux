---
title: PaperFlux
emoji: 📚
colorFrom: blue
colorTo: indigo
sdk: streamlit
sdk_version: 1.67.0
python_version: "3.12"
app_file: app.py
pinned: true
---

# PaperFlux: AI Research Paper Insights

PaperFlux fetches the most-upvoted Hugging Face Daily Papers and turns each one into an explainer: a layered written analysis, a TL;DR, an **animated core-concept walkthrough** (exportable as a GIF), key numbers, and 2-3 insights with diagrams and charts built from the paper's own results.

## Features

- **Daily updates**: processes the top papers every weekday at ```8:00 AM UTC``` and never wipes yesterday's papers until today's are ready
- **Layered analysis**: TL;DR, big idea, how it works, plain-words math, an evidence table with sources, skeptical notes, glossary
- **Animated explainers**: the model plans the diagram (nodes, edges, narrated steps); PaperFlux lays it out deterministically and animates it. Clean, consistent figures, and a one-click **GIF export**
- **Charts from real numbers**: the model reports the paper's numbers, PaperFlux builds the chart
- **Free-tier friendly**: quota-aware Gemini client with a per-model fallback chain, client-side rate limiting, a daily paper budget, and resumable runs
- **Optional second provider**: set `GROQ_API_KEY` to let the text-only planner fall back to Groq's free tier when Gemini quota is spent
- **Original PDFs**: direct arXiv links

## Reading experience

- **Feed first**: today's papers as cards ranked by upvotes, each with its TL;DR and a status badge
- **Deep links**: the URL is the source of truth (`?paper=<id>&view=insights`), so every paper and tab is shareable and survives a refresh; a Share button gives the link
- **One view at a time**: Overview, Insights, Analysis, Abstract. Only the selected view is rendered, so pages stay fast
- **Keyboard**: `J` next paper, `K` previous paper
- **Live status**: the sidebar shows real progress while papers are processed and updates by itself; new papers appear without a refresh. "Next update" accounts for weekends and your timezone
- **Never an empty screen**: papers whose diagrams are still pending (free-tier quota) show their TL;DR and analysis immediately, and the diagrams appear on their own later
- **Long reads made scannable**: the analysis is split into collapsible sections with a reading time, and downloadable as Markdown

## System Architecture

```mermaid
   flowchart TD
      A[Scheduler] -->|Hourly tick, once per day| B[Paper Processor]
      B -->|Top N by upvotes| C[Hugging Face API]
      B -->|Download PDFs| D[arXiv]
      B -->|PDF| E[Analyst agent - Gemini chain]
      E -->|Visual brief + writeup + PDF| I[Insights planner - Gemini chain / optional Groq]
      I -->|JSON plan| R[Visual renderer: layout + animation + GIF]
      I -->|Upsert| F[(MongoDB)]
      G[Streamlit UI] -->|Display| F
      G --> R
```

## How the insights work

Models are good at deciding *what* matters and bad at geometry, so the insights agent never draws. It returns a JSON plan (validated by a response schema): `tldr`, a `core_concept` with an animated `flow`, `key_numbers`, and 2-3 `insights`, each with a `flow` or `chart`. `paperflux/src/visuals/` turns that into an auto-laid-out animated SVG (or a GIF). Prompts live in `paperflux/src/agents/*/SKILL.md`.

The analyst ends its writeup with a **Visual Brief** (components and which ones are new, a running example with concrete values, baseline vs this paper, loops, transcribed results tables, ablations and cost/quality pairs). It costs no extra request: the processor splits it off before storing the writeup (readers never see it) and hands it to the planner ahead of the writeup, which matters most when the planner runs text-only.

What the renderer can show:

- **Flows**: nodes the paper introduces get a NEW tag (`novel`), each node can carry the running example's value (`example`) in a chip that appears as the data arrives, and a packet travels along every edge as it draws. Two groups with no edges between them become aligned **compare lanes** (baseline vs this paper).
- **Token animations** (`tokens`): a row of token cells that change state step by step (new, keep, drop, mask, focus, hidden, plus a small tag), for methods that act on tokens: speculative decoding, masked decoding, pruning, KV eviction, expert routing.
- **Charts**: `bar`, `line`, and `scatter` (cost vs quality, log axis for wide ranges), with the paper's method drawn in the accent colour (`highlight`) and baselines in grey.

Every flow, token animation and chart has a **Make GIF** button. Chart GIFs reveal the baselines first, then the paper's method, then the gap to the best baseline (only when the y-axis label says which direction is better). Drawing is deterministic Pillow (`visuals/raster.py`), so GIFs need no browser.

## Free-tier budgeting (Gemini)

Limits are enforced **per Google Cloud project and per model**, and they change, so check `https://ai.dev/rate-limit`. At the time of writing the free Flash models allow about 5 requests/min and 20/day each, and the Flash-Lite models about 500/day. PaperFlux therefore:

- spends ~2 requests per paper and processes only `PAPERFLUX_MAX_PAPERS` (default 10) top papers
- falls back along `GEMINI_ANALYST_MODELS` / `GEMINI_INSIGHTS_MODELS` when a model's daily quota is spent or a model id disappears
- waits the server's `retryDelay` on per-minute limits, and stops spending requests once everything is exhausted
- keeps the day open when quota ran out mid-run; the hourly tick finishes the remaining papers after the quota resets (midnight Pacific) and reuses analyses it already paid for

Extra API keys only add capacity if they belong to **different projects**.

## System Flow

1. **Scheduled polling**: the scheduler checks hourly whether today's papers need processing
2. **Selection**: top papers by upvotes from Hugging Face, up to the daily budget
3. **PDFs**: downloaded from arXiv, only for papers that still need work
4. **Analyst agent**: layered explanation from the native PDF, plus a Visual Brief for the planner
5. **Insights planner**: structured plan; one automatic repair pass if the plan is unusable
6. **Storage**: each paper is upserted as it finishes; old papers are pruned only after new ones exist
7. **UI**: Overview (TL;DR, core concept, numbers), Insights & Diagrams, Detailed Analysis, Abstract

## Installation

### Prerequisites

- Python 3.12 or higher
- MongoDB database
- Google Gemini API keys (free tier works; see budgeting above)
- Poetry (dependency management)

### Local Setup with Poetry

1. Clone the repository:
   ```bash
   git clone https://github.com/yourusername/paperflux.git
   cd paperflux
   ```

2. Install dependencies using Poetry:
   ```bash
   # Install Poetry if you haven't already
   # curl -sSL https://install.python-poetry.org | python3 -
   
   # Install dependencies
   poetry install
   ```

3. Create a `.env` file with your credentials (copy from `.env.example`):
   ```bash
   cp .env.example .env
   # Edit .env with your credentials
   ```

4. Configure your environment variables:
   ```
   MONGODB_URI=mongodb+srv://username:password@cluster.mongodb.net/paperflux
   GEMINI_ANALYST_API_KEY=your_analyst_gemini_key
   GEMINI_INSIGHTS_API_KEY=your_insights_gemini_key
   # optional: GEMINI_ANALYST_MODELS / GEMINI_INSIGHTS_MODELS (fallback chains), GROQ_API_KEY
   ```

5. Run the Streamlit app with Poetry:
   ```bash
   poetry run streamlit run app.py
   ```

## Contributing

Contributions are welcome! Please feel free to submit a Pull Request.

## License

This project is licensed under the MIT License - see the LICENSE file for details.