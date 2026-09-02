"""
server_debugger.py — MCP server for database debugging.
"""

from dotenv import load_dotenv
from groq import Groq
from mcp.server.fastmcp import FastMCP


load_dotenv()

mcp = FastMCP("database-debugger")

groq_client = Groq()


@mcp.tool()
def analyze_database_issue(
    question: str,
    schema: str = "",
    sql: str = "",
    result: str = "",
) -> str:
    """
    Analyze database evidence and return the final debugging answer.
    """

    system_prompt = """
    You are a database debugging assistant.

    Your job is to analyze the user's database question using ONLY
    the evidence provided by the database tools.

    IMPORTANT RULES:

    1. Analyze the user's ORIGINAL question exactly as provided.
    2. Use ONLY the provided schema, SQL, and result.
    3. Do NOT invent or assume database information.
    4. Identify the actual problem based on the evidence.
    5. Explain the evidence supporting your conclusion.
    6. Identify the likely cause when supported by the evidence.
    7. Suggest the appropriate next step.
    8. If the evidence is insufficient, clearly state that.
    9. Do NOT make unsupported assumptions.

    Your response should contain:

    - Problem
    - Evidence
    - Likely Cause
    - Suggested Next Step
    """

    user_prompt = f"""
    User Question:
    {question}

    Database Schema:
    {schema}

    Generated SQL:
    {sql}

    Query Result:
    {result}
    """

    response = groq_client.chat.completions.create(
        model="openai/gpt-oss-120b",
        messages=[
            {
                "role": "system",
                "content": system_prompt
            },
            {
                "role": "user",
                "content": user_prompt
            }
        ],
        temperature=0
    )

    return response.choices[0].message.content.strip()


if __name__ == "__main__":
    mcp.run(transport="stdio")