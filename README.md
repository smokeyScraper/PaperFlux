---
title: PaperFlux
emoji: 📚
colorFrom: blue
colorTo: indigo
sdk: streamlit
sdk_version: 1.30.0
app_file: app.py
pinned: true
---

# PaperFlux: AI Research Paper Insights

PaperFlux is a Streamlit app that fetches Hugging Face Daily Papers, then runs two Gemini agents: one that explains the PDF in depth, and one that produces 2–3 insights with diagrams.

## Features

- **Daily Updates**: Automatically fetches and processes new papers every weekday at ```8:00 AM UTC```
- **Analyst agent**: Gemini Flash reads the native PDF and writes a technical breakdown
- **Insights agent**: A second Gemini key (separate quota) turns the paper + writeup into 2–3 visual diagrams (SVG or Mermaid)
- **Paper Library**: Browse processed papers in Streamlit
- **Original PDFs**: Direct arXiv download links

## System Architecture

PaperFlux follows a robust architecture for fetching, processing, and displaying research papers:

```mermaid
   flowchart TD
      A[Scheduler] -->|Daily trigger| B[Paper Processor]
      B -->|Fetch papers| C[Hugging Face API]
      B -->|Download PDFs| D[arXiv]
      B -->|Analyze PDF| E[Analyst agent — Gemini Flash]
      E -->|Explanation + PDF| I[Insights agent — Gemini Flash]
      I -->|Store data| F[(MongoDB)]
      G[Streamlit UI] -->|Display papers| F
      H[User] -->|View papers| G

```

## System Flow

1. **Scheduled Polling**: Every weekday at 8:00 AM UTC, the scheduler checks if papers need to be processed
2. **Data Collection**: The application fetches the latest papers from Hugging Face's API
3. **PDF Processing**: Papers are downloaded from arXiv and stored temporarily
4. **Analyst agent**: Each PDF is explained in depth using Gemini (native PDF input)
5. **Insights agent**: A second agent (separate API key) produces 2–3 insights and diagrams
6. **Data Storage**: Results are stored in MongoDB for quick access
7. **User Interface**: Users browse papers, insights, and full writeups in Streamlit

## Installation

### Prerequisites

- Python 3.8 or higher
- MongoDB database
- Google Gemini API keys (one for the analyst agent, one for the insights agent)
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
   GEMINI_ANALYST_MODEL=gemini-3.5-flash
   GEMINI_INSIGHTS_MODEL=gemini-3.5-flash
   ```

5. Run the Streamlit app with Poetry:
   ```bash
   poetry run streamlit run app.py
   ```

## Contributing

Contributions are welcome! Please feel free to submit a Pull Request.

## License

This project is licensed under the MIT License - see the LICENSE file for details.