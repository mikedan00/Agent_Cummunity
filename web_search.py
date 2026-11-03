"""Web search for the AI agent: Tavily when an API key is set, DuckDuckGo otherwise or on failure."""

import requests
from ddgs import DDGS

TAVILY_URL = "https://api.tavily.com/search"
MAX_RESULTS = 5


def search(query: str, tavily_key: str | None = None, max_results: int = MAX_RESULTS) -> list[dict]:
    """Return [{"title", "url", "snippet"}, ...]."""
    if tavily_key:
        try:
            return _tavily(query, tavily_key, max_results)
        except Exception:
            pass  # fall back to DuckDuckGo
    return _duckduckgo(query, max_results)


def _tavily(query: str, key: str, max_results: int) -> list[dict]:
    resp = requests.post(TAVILY_URL, json={"query": query, "max_results": max_results},
                         headers={"Authorization": f"Bearer {key}"}, timeout=20)
    resp.raise_for_status()
    return [{"title": r.get("title", ""), "url": r.get("url", ""), "snippet": r.get("content", "")}
            for r in resp.json().get("results", [])]


def _duckduckgo(query: str, max_results: int) -> list[dict]:
    return [{"title": r.get("title", ""), "url": r.get("href", ""), "snippet": r.get("body", "")}
            for r in DDGS().text(query, max_results=max_results)]


def format_results(results: list[dict]) -> str:
    if not results:
        return "No results found."
    return "\n\n".join(f"[{i}] {r['title']}\n{r['url']}\n{r['snippet']}" for i, r in enumerate(results, 1))
