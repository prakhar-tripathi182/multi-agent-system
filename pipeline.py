"""Sequential research pipeline: search -> read -> write -> critique."""

import re

from langchain_core.messages import ToolMessage

from agents import build_reader_agent, build_search_agent, critic_chain, writer_chain


def get_tool_result(agent_response: dict) -> str:
    """
    An agent returns a list of messages, not just the tool output.
    The last message is the AI's own summary (which may drop details like URLs),
    so we search backwards for the ToolMessage: the raw, untouched tool output.
    """
    for msg in reversed(agent_response["messages"]):
        if isinstance(msg, ToolMessage):
            return msg.content
    return agent_response["messages"][-1].content  # fallback if no tool was called


def strip_think(text: str) -> str:
    """Safety net: remove any leftover <think>...</think> block from model output."""
    return re.sub(r"<think>.*?</think>", "", text, flags=re.DOTALL).strip()


def run_research_pipeline(topic: str) -> dict:
    """Run all four steps in order and return the state dict."""
    state = {}

    # Step 1 - search agent
    print("\nStep 1 - search agent is working ...")
    search_agent = build_search_agent()
    search_result = search_agent.invoke(
        {"messages": [("user", f"Find recent, reliable and detailed information about: {topic}")]},
        config={"recursion_limit": 25},
    )
    state["search_results"] = get_tool_result(search_result)  # raw output, URLs included
    print("\nSearch results:\n", state["search_results"])

    # Step 2 - reader agent (only gets the first 1500 chars of the search results)
    print("\nStep 2 - reader agent is scraping the top resource ...")
    reader_agent = build_reader_agent()
    reader_result = reader_agent.invoke(
        {"messages": [("user",
            f"Based on the following search results about '{topic}', "
            f"pick the most relevant URL and scrape it for deeper content.\n\n"
            f"Search Results:\n{state['search_results'][:1500]}"
        )]},
        config={"recursion_limit": 25},
    )
    state["scraped_content"] = get_tool_result(reader_result)  # raw scraped text, not the AI summary
    print("\nScraped content:\n", state["scraped_content"])

    # Step 3 - writer chain
    print("\nStep 3 - writer is drafting the report ...")
    research_combined = (
        f"SEARCH RESULTS : \n {state['search_results']} \n\n"
        f"DETAILED SCRAPED CONTENT : \n {state['scraped_content']}"
    )
    state["report"] = strip_think(writer_chain.invoke({
        "topic": topic,
        "research": research_combined,
    }))
    print("\nFinal report:\n", state["report"])

    # Step 4 - critic chain
    print("\nStep 4 - critic is reviewing the report ...")
    state["feedback"] = strip_think(critic_chain.invoke({"report": state["report"]}))
    print("\nCritic feedback:\n", state["feedback"])

    return state


if __name__ == "__main__":
    topic = input("\nEnter a research topic: ")
    run_research_pipeline(topic)
