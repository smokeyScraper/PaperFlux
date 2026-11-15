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

PaperFlux fetches the most-upvoted Hugging Face Daily Papers and turns each one into an explainer: a layered written analysis, a TL;DR, an **animated core-concept walkthrough**, key numbers, and 2-3 insights with diagrams and charts built from the paper's own results.

## Features

- **Daily updates**: processes the top papers every weekday at ```8:00 AM UTC``` and never wipes yesterday's papers until today's are ready
- **Layered analysis**: TL;DR, big idea, how it works, plain-words math, an evidence table with sources, skeptical notes, glossary
- **Animated explainers**: the model plans the diagram (nodes, edges, narrated steps); PaperFlux lays it out deterministically and animates it. Clean, consistent figures
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
      I -->|JSON plan| R[Visual renderer: layout + animation]
      I -->|Upsert| F[(MongoDB)]
      F -->|Writeup + brief + pack| W[Podcast writer - Flash-Lite chain]
      W -->|Two-host script| T[Gemini TTS - 1 multi-speaker call]
      T -->|MP3| A2[(GridFS audio)]
      G[Streamlit UI] -->|Display| F
      G -->|Listen| A2
      G --> R
```

## How the insights work

Models are good at deciding *what* matters and bad at geometry, so the insights agent never draws. It returns a JSON plan (validated by a response schema): `tldr`, a `core_concept` with an animated `flow`, `key_numbers`, and 2-3 `insights`, each with a `flow` or `chart`. `paperflux/src/visuals/` turns that into an auto-laid-out animated SVG. Prompts live in `paperflux/src/agents/*/SKILL.md`.

The analyst ends its writeup with a **Visual Brief** (components and which ones are new, a running example with concrete values, baseline vs this paper, loops, transcribed results tables, ablations and cost/quality pairs). It costs no extra request: the processor splits it off before storing the writeup (readers never see it) and hands it to the planner ahead of the writeup, which matters most when the planner runs text-only.

What the renderer can show:

- **Flows**: nodes the paper introduces get a NEW tag (`novel`), each node can carry the running example's value (`example`) in a chip that appears as the data arrives, and a packet travels along every edge as it draws. Two groups with no edges between them become aligned **compare lanes** (baseline vs this paper).
- **Token animations** (`tokens`): a row of token cells that change state step by step (new, keep, drop, mask, focus, hidden, plus a small tag), for methods that act on tokens: speculative decoding, masked decoding, pruning, KV eviction, expert routing.
- **Charts**: `bar`, `line`, and `scatter` (cost vs quality, log axis for wide ranges), with the paper's method drawn in the accent colour (`highlight`) and baselines in grey.

Flows and token sequences animate inline. An insight with no diagram is simply omitted, so the text stands on its own.

## Podcasts

For the top papers, two calm hosts (Maya and Theo by default) talk the paper through in about 5 minutes: the hook, the problem, the idea, the running example, the evidence, the catch, and a takeaway. Both ask and both explain. A **Listen** panel on the paper page plays the episode, shows the transcript, and offers the MP3; the feed marks those papers with a Podcast badge.

How it is produced, after all of the day's analyses are stored (so podcasts never delay them):

1. **Script** (`agents/paper_podcast`): a text model writes JSON turns from the analyst writeup, its Visual Brief and the insights pack (no PDF, Flash-Lite first). `podcast/script.py` makes every line speakable (no markdown, LaTeX, URLs or stage directions), caps the length, and gives one repair pass. The script is stored before recording.
2. **Audio**: one multi-speaker Gemini TTS request per episode, with a calm delivery direction and two prebuilt voices (`Vindemiatrix`, "Gentle"; `Algieba`, "Smooth").
3. **Storage**: PCM is encoded to 64 kbps MP3 (`lameenc`, ~0.5 MB per minute) and kept in a GridFS bucket, outside the paper documents; it is pruned with its paper.

Free-tier TTS allows about 3 requests/min and 10/day, and failed requests count, so `services/podcast_producer.py`:

- claims each TTS request from a per-day counter in MongoDB (quota day = Pacific date) before making it, so restarts and manual runs share `PAPERFLUX_PODCASTS_PER_DAY` (default 10);
- does not write a script when today's budget is already spent;
- stops at a daily-quota error and leaves the rest pending (scripts are reused, never rewritten);
- rejects audio that is far shorter than its script (a cut-off reply), and gives up on a paper after 2 failed attempts.

Podcasts need their own key, `GEMINI_PODCAST_API_KEY`, from a **new Google Cloud project**, and stay off without one: they never borrow the analyst/insights keys, whose quota the daily papers need (a warning is logged if the same key is reused). That one key covers the script writer and TTS, which are separate quota buckets; `GEMINI_TTS_API_KEY` optionally gives TTS a project of its own.

Check the real setup once with `python tests/podcast_tts_check.py` (one TTS request, no database). Turn podcasts off with `PAPERFLUX_PODCASTS=0`.

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
7. **Podcasts**: scripts and audio for the top papers, within the daily TTS budget
8. **UI**: Abstract, Overview (TL;DR, core concept, numbers), Insights & Diagrams, Detailed Analysis, plus a Listen panel

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