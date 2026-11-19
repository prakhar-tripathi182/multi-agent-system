"""LLM setup, the two tool-using agents (search, reader) and the two prompt chains (writer, critic)."""

from datetime import date

from dotenv import load_dotenv
from langchain.agents import create_agent
from langchain_core.output_parsers import StrOutputParser
from langchain_core.prompts import ChatPromptTemplate
from langchain_groq import ChatGroq

from tools import scrape_url, web_search

load_dotenv()  # GROQ_API_KEY is read from the environment by ChatGroq

# ---- Model setup (shared by all agents/chains) ----
llm = ChatGroq(
    model="openai/gpt-oss-20b",
    temperature=0,
    reasoning_effort="low",
    max_retries=5,
)


# ---- Agent 1: search agent (always searches the web, never answers from memory) ----
def build_search_agent():
    return create_agent(
        model=llm,
        tools=[web_search],
        system_prompt=(
            "You are a research search agent. Call the web_search tool exactly ONCE "
            "with a single clear query covering the topic, then immediately write your "
            "final answer using the tool's results. Never call the tool more than once. "
            "Your own training data can be outdated - trust the tool's results, not your "
            "memory. Return the raw results (titles, URLs, snippets) as-is, don't drop URLs."
        ),
    )


# ---- Agent 2: reader agent (scrapes one URL for deeper content) ----
def build_reader_agent():
    return create_agent(
        model=llm,
        tools=[scrape_url],
        system_prompt=(
            "You are a research reader agent. Pick the most relevant URL from the given "
            "search results and call scrape_url exactly ONCE, then immediately write your "
            "final answer using the result. Never call the tool more than once."
        ),
    )


# ---- Writer chain: prompt -> LLM -> plain string ----
writer_prompt = ChatPromptTemplate.from_messages([
    ("system", "You are an expert research writer. Write clear, structured and insightful reports."),
    ("human", """Write a detailed research report on the topic below.

Topic: {topic}

Research Gathered:
{research}

Structure the report as:
- Introduction
- Key Findings (minimum 3 findings, as a numbered list)
- Conclusion
- Sources (list all URLs found in the research)

Rules:
- Use specific numbers, percentages, and dates from the research wherever available.
- When you state a claim, mention which source it came from.
- Do NOT use tables anywhere in the report. Write Key Findings as a numbered list. Each item starts with a bold title, then 1-3 sentences of explanation, then the source and date in parentheses, e.g. (Source: Reuters, 30 Sep 2026).
- IMPORTANT: The research may contain results from DIFFERENT policy events/dates (e.g. one article about a rate cut, another about a rate hold on a different date). Do not blend numbers or claims from different events into a single narrative. If sources describe different dates/events, treat them separately and say so clearly.
- If the exact quarter/period asked about doesn't match what's in the sources (e.g. sources cover Q3 but the topic asks about Q4), say so explicitly instead of silently relabeling the data.
- Be detailed, factual and professional."""),
])

writer_chain = writer_prompt | llm | StrOutputParser()


# ---- Critic chain: reviews the report and returns a score + feedback ----
critic_prompt = ChatPromptTemplate.from_messages([
    ("system",
     f"You are a sharp and constructive research critic. Be honest and specific. "
     f"Today's date is {date.today():%B %Y}. Your own training data has a cutoff before this date, "
     f"so you will not personally recognize many recent events - that does NOT make them fake. "
     f"The report you're reviewing is built from live web search results with real URLs from "
     f"real domains (wsj.com, cnbc.com, etc) - treat these as legitimate unless the report text "
     f"itself is internally inconsistent. Do NOT deduct points, call something 'hallucinated', "
     f"or question a source's existence just because you don't personally recall the event or "
     f"because it's dated after your training cutoff. Judge the report only on: clarity, "
     f"structure, whether claims are properly tied to sources, internal consistency, and writing quality."),
    ("human", """Review the research report below and evaluate it strictly.

Report:
{report}

Respond in this exact format:

Score: X/10

Strengths:
- ...
- ...

Areas to Improve:
- ...
- ...

One line verdict:
..."""),
])

critic_chain = critic_prompt | llm | StrOutputParser()
