import asyncio
import os
from langchain_anthropic import ChatAnthropic
from langchain_mcp_adapters.client import MultiServerMCPClient
from langgraph.prebuilt import create_react_agent
from langchain_core.messages import HumanMessage, AIMessage

SYSTEM_PROMPT = """You are a web scraping assistant with access to open-scraper tools.

When the user asks to scrape a page:
1. Call run_scrape_page with the target URL
2. You will get a job_id — call get_job_status to check the status
3. Repeat get_job_status until status is "done" or "failed"
4. Call get_job_result to retrieve the data
5. Show the user: URL, status code, and the main content of the page"""


async def main():
    api_key = os.environ.get("ANTHROPIC_API_KEY")
    if not api_key:
        print("Error: set the ANTHROPIC_API_KEY environment variable")
        return

    llm = ChatAnthropic(model="claude-sonnet-4-6", api_key=api_key)

    client = MultiServerMCPClient({
        "open-scraper": {
            "url": "http://localhost:8000/mcp",
            "transport": "streamable_http",
        }
    })
    tools = await client.get_tools()
    print(f"Connected. Tools: {[t.name for t in tools]}\n")

    agent = create_react_agent(llm, tools, prompt=SYSTEM_PROMPT)

    history = []
    print("Agent ready. Enter your request (or 'exit' to quit)\n")

    while True:
        user_input = input("You: ").strip()
        if not user_input or user_input.lower() == "exit":
            break

        history.append(HumanMessage(content=user_input))

        result = await agent.ainvoke({"messages": history})
        messages = result["messages"]

        last_ai = next(
            (m for m in reversed(messages) if isinstance(m, AIMessage)),
            None
        )

        if last_ai:
            print(f"\nAgent: {last_ai.content}\n")
            history = messages


if __name__ == "__main__":
    asyncio.run(main())
