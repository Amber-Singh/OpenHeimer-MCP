"""
server_debugger.py — MCP server for database debugging.
"""

import json

from mcp.server.fastmcp import FastMCP


mcp = FastMCP("database-debugger")


@mcp.tool()
def analyze_database_issue(
    question: str,
    schema: str = "",
    sql: str = "",
    result: str = "",
) -> str:
    """
    Analyze evidence collected from previous database tools
    and prepare information for diagnosing the database problem.

    Parameters:
        question:
            The user's original question, passed unchanged.

        schema:
            The exact output returned by get_database_schema().

        sql:
            The exact SQL returned by generate_read_sql().

        result:
            The exact output returned by execute_read_sql(),
            including query results or an execution error.
    """

    return json.dumps(
        {
            "task": "debug",
            "question": question,
            "evidence": {
                "schema": schema,
                "sql": sql,
                "result": result,
            },
        },
        indent=2,
    )


if __name__ == "__main__":
    mcp.run(transport="stdio")