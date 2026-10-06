from paperflux.src.agents.paper_analyst.agent import PaperAnalystAgent


class PaperAnalyzer:
    """Compatibility wrapper around PaperAnalystAgent."""

    def __init__(self):
        self._agent = PaperAnalystAgent()
        self.api_keys = self._agent.client.api_keys

    def analyze_paper(self, pdf_path: str) -> str:
        return self._agent.run(pdf_path)
