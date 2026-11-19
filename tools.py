"""Tools the agents can call: web search (Tavily) and page scraping (Requests + BeautifulSoup)."""

import os

import requests
from bs4 import BeautifulSoup
from dotenv import load_dotenv
from langchain.tools import tool
from tavily import TavilyClient

load_dotenv()  # read keys from the .env file (never hardcode them)

# Tavily client; the key comes from the TAVILY_API_KEY environment variable
tavily = TavilyClient(api_key=os.getenv("TAVILY_API_KEY"))


@tool
def web_search(query: str) -> str:
    """Search the web for recent and reliable information on a topic. Returns Titles, URLs and snippets."""
    try:
        results = tavily.search(query=query, max_results=5)
    except Exception as e:
        # Return the error as text so the agent (and pipeline) keeps running
        return f"WEB SEARCH FAILED: {str(e)}"

    # Format each result as Title / URL / Snippet (first 300 chars)
    out = []
    for r in results["results"]:
        out.append(f"Title:{r['title']}\nURL:{r['url']}\nSnippet:{r['content'][:300]}\n")
    return "\n-----\n".join(out)


@tool
def scrape_url(url: str) -> str:
    """Scrape and return clean text content from a given URL for deeper reading."""
    try:
        resp = requests.get(
            url,
            timeout=8,
            # A browser-like User-Agent: many sites block the default requests one
            headers={
                "User-Agent": (
                    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                    "AppleWebKit/537.36 (KHTML, like Gecko) "
                    "Chrome/151.0.0.0 Safari/537.36"
                )
            },
        )

        soup = BeautifulSoup(resp.text, "html.parser")

        # Remove non-content elements before extracting text
        for tag in soup(["script", "style", "nav", "footer"]):
            tag.decompose()

        # Keep only the first 3000 characters to stay within the LLM's context budget
        return soup.get_text(separator=" ", strip=True)[:3000]

    except Exception as e:
        return f"Could not scrape URL: {str(e)}"
