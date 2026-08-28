"""
sql_generator.py — MCP server that generates READ-ONLY SQL
from natural-language database questions.
"""

import os
import json
import re

import psycopg2
from dotenv import load_dotenv
from groq import Groq
from mcp.server.fastmcp import FastMCP

load_dotenv()

DATABASE_URL = os.environ.get(
    "DATABASE_URL",
    "postgresql://postgres:devpass@localhost:5432/ecommerce_relational",
)

groq_client = Groq()

mcp = FastMCP("sql-generator")

@mcp.tool()
def get_database_schema() -> str:
    """Read the current PostgreSQL database schema."""

    conn = psycopg2.connect(DATABASE_URL)
    cur = conn.cursor()

    try:
        cur.execute("""
            SELECT
                table_name,
                column_name,
                data_type
            FROM information_schema.columns
            WHERE table_schema = 'public'
            ORDER BY table_name, ordinal_position
        """)

        rows = cur.fetchall()

        schema = {}

        for table_name, column_name, data_type in rows:
            if table_name not in schema:
                schema[table_name] = []

            schema[table_name].append({
                "column": column_name,
                "type": data_type
            })

        return json.dumps(schema, indent=2)

    finally:
        cur.close()
        conn.close()


def generate_sql(question: str, schema: str) -> str:
    """Generate SQL using the database schema."""

    system_prompt = """
        You are a PostgreSQL SQL generation assistant.

        Your job is to convert the user's natural-language question
        into ONE PostgreSQL SQL query using the provided database schema.

        IMPORTANT:
        The database schema provided by the user is the COMPLETE and
        AUTHORITATIVE schema of the database.

        STRICT RULES:

        1. Use ONLY tables that appear in the provided schema.
        2. Use ONLY columns that appear under those tables in the provided schema.
        3. NEVER invent a table.
        4. NEVER invent a column.
        5. NEVER assume a column exists because it is common in other databases.
        6. Before generating SQL, verify every table and every column against
        the provided schema.
        7. If the requested information cannot be obtained using the available
        tables and columns, return exactly:
        CANNOT_GENERATE_SQL
        8. Do not suggest alternative column names.
        9. Do not add comments to the SQL.
        10. Do not explain the SQL.
        11. Return ONLY the SQL query.
        12. Generate PostgreSQL-compatible SQL.
        13. Generate READ-ONLY SQL only.
        14. Only SELECT or WITH statements are allowed.
        15. Never generate INSERT, UPDATE, DELETE, DROP, ALTER, TRUNCATE,
            CREATE, GRANT, REVOKE, or MERGE.
        16. Never execute the SQL.
        """

    user_prompt = f"""
        Database schema:

        {schema}

        User question:

        {question}
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

@mcp.tool()
def generate_read_sql(question: str) -> str:
    """Generate and validate a read-only SQL query."""

   #checking for clarity of the question before generating SQL
    clarity = check_query_clarity(question)
    if not clarity["clear"]:
        return json.dumps({
            "status": "clarification_required",
            "question": question,
            "clarification": clarity["clarification"],
            "options": clarity["options"]
        }, indent=2)
    
    
    
    # Get the latest database schema
    schema = get_database_schema()

    # Generate SQL using the schema
    sql = generate_sql(question, schema)

    # Validate that SQL is read-only
    valid, message = validate_read_only_sql(sql)

    if not valid:
        return json.dumps({
            "status": "error",
            "question": question,
            "error": message
        }, indent=2)

    # Validate tables, columns, syntax, etc. using PostgreSQL
    valid, message = validate_sql_with_database(sql)

    if not valid:
        return json.dumps({
            "status": "error",
            "question": question,
            "sql": sql,
            "error": message
        }, indent=2)

    # Return the validated SQL without executing it
    return json.dumps({
        "status": "success",
        "question": question,
        "sql": sql,
        "validation": "passed"
    }, indent=2)
    
    
@mcp.tool()
def execute_read_sql(sql: str) -> str:
    """Execute a validated read-only SQL query and return the results."""

    # Check SQL is still read-only
    valid, message = validate_read_only_sql(sql)

    if not valid:
        return json.dumps({
            "status": "error",
            "error": message
        }, indent=2)

    conn = psycopg2.connect(DATABASE_URL)
    cur = conn.cursor()

    try:
        # Execute the validated SQL
        cur.execute(sql)

        # Get column names
        columns = [desc[0] for desc in cur.description]

        # Get query results
        rows = cur.fetchall()

        # Convert rows into dictionaries
        results = [
            dict(zip(columns, row))
            for row in rows
        ]

        return json.dumps(results, indent=2, default=str)

    except Exception as e:
        return json.dumps({
            "status": "error",
            "error": str(e)
        }, indent=2)

    finally:
        cur.close()
        conn.close()
        
    
def validate_read_only_sql(sql: str) -> tuple[bool, str]:
    """Validate that SQL contains only SELECT or WITH statements."""

    sql = sql.strip()

    # Must start with SELECT or WITH
    if not re.match(r"^(SELECT|WITH)\b", sql, re.IGNORECASE):
        return False, "Only SELECT or WITH statements are allowed."

    # Dangerous SQL operations
    forbidden_keywords = [
        "INSERT",
        "UPDATE",
        "DELETE",
        "DROP",
        "ALTER",
        "TRUNCATE",
        "CREATE",
        "GRANT",
        "REVOKE",
        "MERGE"
    ]

    for keyword in forbidden_keywords:
        if re.search(rf"\b{keyword}\b", sql, re.IGNORECASE):
            return False, f"Forbidden SQL operation detected: {keyword}"

    return True, "SQL is read-only."



def validate_sql_with_database(sql: str) -> tuple[bool, str]:
    """Ask PostgreSQL to validate the generated SQL."""

    conn = psycopg2.connect(DATABASE_URL)
    cur = conn.cursor()

    try:
        # EXPLAIN validates the query without executing it
        cur.execute("EXPLAIN " + sql)

        return True, "SQL is valid."

    except Exception as e:
        return False, str(e)

    finally:
        cur.close()
        conn.close()
        
def check_query_clarity(question: str) -> dict:
    """Check whether the user's question is clear enough for SQL generation."""

    system_prompt = """
    You are a query-clarity checker.

    Your ONLY job is to determine whether the user's ORIGINAL question
    is specific enough to generate exactly ONE SQL query WITHOUT making
    assumptions.

    IMPORTANT RULES:

    1. Analyze the question EXACTLY as provided.
    2. NEVER rewrite, reinterpret, expand, or add meaning to the question.
    3. NEVER assume what the user means by an ambiguous word or phrase.
    4. If a word has multiple reasonable meanings, the question is NOT clear.
    5. Words such as:
       - best
       - worst
       - valuable
       - important
       - successful
       - popular
       - recent
       - active
       - top
       - good
       should be considered ambiguous unless the user defines how they
       should be measured.
    6. A question is clear only when there is one reasonable way to
       construct the SQL query.
    7. If multiple reasonable interpretations exist, return clear=false.
    8. Do NOT choose one interpretation on behalf of the user.
    9. Do NOT generate SQL.
    10. Return ONLY valid JSON.

    Required JSON format:

    {
        "clear": true or false,
        "clarification": "",
        "options": []
    }

    Example 1:

    Question:
    "Show the top 10 customers by number of orders."

    Response:
    {
        "clear": true,
        "clarification": "",
        "options": []
    }

    Example 2:

    Question:
    "Show the best customer."

    Response:
    {
        "clear": false,
        "clarification": "How should 'best customer' be measured?",
        "options": [
            "Highest number of orders",
            "Highest total revenue",
            "Highest average order value"
        ]
    }

    Example 3:

    Question:
    "Show the most valuable customers."

    Response:
    {
        "clear": false,
        "clarification": "How should 'valuable' be measured?",
        "options": [
            "Highest total revenue",
            "Highest number of orders",
            "Highest average order value"
        ]
    }

    Example 4:

    Question:
    "Show customers with more than 10 orders."

    Response:
    {
        "clear": true,
        "clarification": "",
        "options": []
    }
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
                "content": question
            }
        ],
        temperature=0
    )

    result = json.loads(
        response.choices[0].message.content.strip()
    )

    return result
    
if __name__ == "__main__":
    mcp.run(transport="stdio")