import time
from tavily import TavilyClient
import config

_client = TavilyClient(api_key=config.TAVILY_API_KEY)


def search(query, max_results=5):
    """
    Search for news/current events via Tavily.

    Returns list of dicts with: title, url, content
    """
    try:
        response = _client.search(
            query=query,
            max_results=max_results,
            search_depth="basic",
        )
        time.sleep(0.5)
        return response.get("results", [])
    except Exception as e:
        print(f"  [news] Tavily search error: {e}")
        return []


def format_results(results):
    """Format search results as plain text for Claude."""
    if not results:
        return "No search results found."
    lines = []
    for r in results:
        lines.append(f"Source: {r.get('url', '')}")
        lines.append(r.get("content", "").strip())
        lines.append("")
    return "\n".join(lines)
