"""
Streaming version of the pipeline.

It reuses the same agents and helpers as pipeline.py, in the same order, but is a
generator: it yields a progress event before and after each step instead of
returning everything at the end.

Event shapes:
  {"step": "search"|"reader"|"writer"|"critic", "status": "running"}
  {"step": ..., "status": "done", "data": <output of that step>}
  {"step": "pipeline", "status": "complete"}
"""

from agents import build_reader_agent, build_search_agent, critic_chain, writer_chain
from pipeline import get_tool_result, strip_think


def stream_research_pipeline(topic: str):
    state = {}

    # ---- Step 1: search agent ----
    yield {"step": "search", "status": "running"}
    search_agent = build_search_agent()
    search_result = search_agent.invoke(
        {"messages": [("user", f"Find recent, reliable and detailed information about: {topic}")]},
        config={"recursion_limit": 25},
    )
    state["search_results"] = get_tool_result(search_result)
    yield {"step": "search", "status": "done", "data": state["search_results"]}

    # ---- Step 2: reader agent ----
    yield {"step": "reader", "status": "running"}
    reader_agent = build_reader_agent()
    reader_result = reader_agent.invoke(
        {"messages": [("user",
            f"Based on the following search results about '{topic}', "
            f"pick the most relevant URL and scrape it for deeper content.\n\n"
            f"Search Results:\n{state['search_results'][:1500]}"
        )]},
        config={"recursion_limit": 25},
    )
    state["scraped_content"] = get_tool_result(reader_result)
    yield {"step": "reader", "status": "done", "data": state["scraped_content"]}

    # ---- Step 3: writer chain ----
    yield {"step": "writer", "status": "running"}
    research_combined = (
        f"SEARCH RESULTS : \n {state['search_results']} \n\n"
        f"DETAILED SCRAPED CONTENT : \n {state['scraped_content']}"
    )
    state["report"] = strip_think(writer_chain.invoke({
        "topic": topic,
        "research": research_combined,
    }))
    yield {"step": "writer", "status": "done", "data": state["report"]}

    # ---- Step 4: critic chain ----
    yield {"step": "critic", "status": "running"}
    state["feedback"] = strip_think(critic_chain.invoke({"report": state["report"]}))
    yield {"step": "critic", "status": "done", "data": state["feedback"]}

    # Final event so the client knows the whole pipeline finished
    yield {"step": "pipeline", "status": "complete"}
