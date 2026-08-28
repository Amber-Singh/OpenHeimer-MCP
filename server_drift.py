"""
server_drift.py — MCP server that compares TWO Postgres databases
(e.g. a "prod" and a "staging" environment) and reports what's
different between them: tables, columns, row counts, and actual cell
values.

Every difference here is computed by comparing real query results in
Python/SQL — never left for the LLM to eyeball and guess at.
"""

import os
import json
import psycopg2
import psycopg2.extras
from mcp.server.fastmcp import FastMCP
from dotenv import load_dotenv

load_dotenv()

PROD_DATABASE_URL = os.environ.get(
    "PROD_DATABASE_URL",
    "postgresql://postgres:devpass@localhost:5432/ecommerce_relational",
)
STAGING_DATABASE_URL = os.environ.get(
    "STAGING_DATABASE_URL",
    "postgresql://postgres:devpass@localhost:5432/ecommerce_relation_staging",
)

MAX_CELLS_FOR_FULL_DIFF = 100  # rows x columns must not exceed this

mcp = FastMCP("environment-drift-comparator")


# ---------------------------------------------------------------------
# Small helpers used by get_environment_diff_report
# ---------------------------------------------------------------------

def _get_table_names(cur) -> set:
    cur.execute(
        "SELECT table_name FROM information_schema.tables WHERE table_schema = 'public'"
    )
    return {row[0] for row in cur.fetchall()}


def _get_columns(cur, table_name: str) -> dict:
    cur.execute(
        "SELECT column_name, data_type FROM information_schema.columns "
        "WHERE table_schema = 'public' AND table_name = %s",
        (table_name,),
    )
    return {row[0]: row[1] for row in cur.fetchall()}


def _get_row_count(cur, table_name: str) -> int:
    cur.execute(f'SELECT COUNT(*) FROM "{table_name}"')
    return cur.fetchone()[0]


# ---------------------------------------------------------------------
# Tool 1: whole-database structural scan
# ---------------------------------------------------------------------


@mcp.tool()
def detect_row_count_differences(table_name: str) -> str:
    """Compare row counts for a specific table between PROD and STAGING

    environments to identify mismatches. Just give the table name.
    """
    table_name = table_name.strip()

    prod_conn = psycopg2.connect(PROD_DATABASE_URL)
    staging_conn = psycopg2.connect(STAGING_DATABASE_URL)
    prod_cur = prod_conn.cursor()
    staging_cur = staging_conn.cursor()

    try:
        # Get count from Production
        prod_cur.execute(f'SELECT COUNT(*) FROM "public"."{table_name}"')
        prod_count = prod_cur.fetchone()[0]

        # Get count from Staging
        staging_cur.execute(f'SELECT COUNT(*) FROM "public"."{table_name}"')
        staging_count = staging_cur.fetchone()[0]

        # Calculate the absolute variance
        drift_difference = prod_count - staging_count

    except Exception as e:
        return json.dumps({"status": "error", "error": str(e)})
    finally:
        prod_cur.close()
        staging_cur.close()
        prod_conn.close()
        staging_conn.close()

    return json.dumps(
        {
            "status": "success",
            "table_name": table_name,
            "metrics": {
                "prod_row_count": prod_count,
                "staging_row_count": staging_count,
                "counts_match": prod_count == staging_count,
                "difference": drift_difference,  # Positive means PROD has more rows, negative means STAGING has more
            },
        },
        indent=2,
    )

@mcp.tool()
def detect_column_differences(table_name: str) -> str:
    """Compares column names of a table between PROD and STAGING

    to find structural schema drifts (missing or extra columns).
    """
    table_name = table_name.strip()

    prod_conn = psycopg2.connect(PROD_DATABASE_URL)
    staging_conn = psycopg2.connect(STAGING_DATABASE_URL)
    prod_cur = prod_conn.cursor()
    staging_cur = staging_conn.cursor()

    try:
        # SQL query to get only column names from information_schema
        name_query = """
            SELECT column_name 
            FROM information_schema.columns 
            WHERE table_name = %s AND table_schema = 'public'
        """

        # Fetch names from production
        prod_cur.execute(name_query, (table_name,))
        prod_cols = {row[0] for row in prod_cur.fetchall()}

        # Fetch names from staging
        staging_cur.execute(name_query, (table_name,))
        staging_cols = {row[0] for row in staging_cur.fetchall()}

        if not prod_cols and not staging_cols:
            return json.dumps(
                {
                    "status": "error",
                    "error": f"Table '{table_name}' was not found in either environment.",
                }
            )

        # Calculate name differences using Python sets
        only_in_prod = list(prod_cols - staging_cols)
        only_in_staging = list(staging_cols - prod_cols)
        shared_columns = list(prod_cols.intersection(staging_cols))

    except Exception as e:
        return json.dumps({"status": "error", "error": str(e)})
    finally:
        prod_cur.close()
        staging_cur.close()
        prod_conn.close()
        staging_conn.close()

    return json.dumps(
        {
            "status": "success",
            "table_name": table_name,
            "column_name_comparison": {
                "schema_matches": prod_cols == staging_cols,
                "columns_only_in_prod": only_in_prod,  # This will capture your extra production column
                "columns_only_in_staging": only_in_staging,
                "shared_columns_present_in_both": shared_columns,
            },
        },
        indent=2,
    )
   
@mcp.tool()
def detect_all_row_count_differences() -> str:
    """Checks row count differences for all shared tables in PROD and STAGING."""

    prod_conn = psycopg2.connect(PROD_DATABASE_URL)
    staging_conn = psycopg2.connect(STAGING_DATABASE_URL)

    try:
        prod_cur = prod_conn.cursor()
        staging_cur = staging_conn.cursor()

        # Get all tables from PROD
        table_query = """
            SELECT table_name
            FROM information_schema.tables
            WHERE table_schema = 'public'
        """

        prod_cur.execute(table_query)
        prod_tables = {row[0] for row in prod_cur.fetchall()}

        # Get all tables from STAGING
        staging_cur.execute(table_query)
        staging_tables = {row[0] for row in staging_cur.fetchall()}

        # Tables present in both databases
        shared_tables = prod_tables & staging_tables

        results = {}

        # Compare row counts for every shared table
        for table_name in shared_tables:

            prod_cur.execute(
                f'SELECT COUNT(*) FROM "{table_name}"'
            )
            prod_count = prod_cur.fetchone()[0]

            staging_cur.execute(
                f'SELECT COUNT(*) FROM "{table_name}"'
            )
            staging_count = staging_cur.fetchone()[0]

            results[table_name] = {
                "prod_count": prod_count,
                "staging_count": staging_count,
                "difference": abs(prod_count - staging_count)
            }

        return json.dumps(results, indent=2)

    finally:
        prod_cur.close()
        staging_cur.close()
        prod_conn.close()
        staging_conn.close()

@mcp.tool()
def detect_all_column_differences() -> str:
    """Checks column differences for all shared tables in PROD and STAGING."""

    prod_conn = psycopg2.connect(PROD_DATABASE_URL)
    staging_conn = psycopg2.connect(STAGING_DATABASE_URL)

    try:
        prod_cur = prod_conn.cursor()
        staging_cur = staging_conn.cursor()

        table_query = """
            SELECT table_name
            FROM information_schema.tables
            WHERE table_schema = 'public'
        """

        prod_cur.execute(table_query)
        prod_tables = {row[0] for row in prod_cur.fetchall()}

        staging_cur.execute(table_query)
        staging_tables = {row[0] for row in staging_cur.fetchall()}

        shared_tables = prod_tables & staging_tables

        results = {}

        column_query = """
            SELECT column_name
            FROM information_schema.columns
            WHERE table_name = %s
            AND table_schema = 'public'
        """

        for table_name in shared_tables:

            prod_cur.execute(column_query, (table_name,))
            prod_cols = {row[0] for row in prod_cur.fetchall()}

            staging_cur.execute(column_query, (table_name,))
            staging_cols = {row[0] for row in staging_cur.fetchall()}

            results[table_name] = {
                "only_in_prod": list(prod_cols - staging_cols),
                "only_in_staging": list(staging_cols - prod_cols)
            }

        return json.dumps(results, indent=2)

    finally:
        prod_cur.close()
        staging_cur.close()
        prod_conn.close()
        staging_conn.close()
                
@mcp.tool()
def detect_table_differences() -> str:
    """Compares the list of tables between PROD and STAGING to find

    structural schema drifts (missing, extra, or mismatched tables).
    No arguments needed.
    """
    prod_conn = psycopg2.connect(PROD_DATABASE_URL)
    staging_conn = psycopg2.connect(STAGING_DATABASE_URL)
    prod_cur = prod_conn.cursor()
    staging_cur = staging_conn.cursor()

    try:
        # Use existing helper functions to fetch tables
        prod_tables = _get_table_names(prod_cur)
        staging_tables = _get_table_names(staging_cur)

        # Compute symmetric differences
        only_in_prod = list(prod_tables - staging_tables)
        only_in_staging = list(staging_tables - prod_tables)
        shared_tables = list(prod_tables.intersection(staging_tables))

    except Exception as e:
        return json.dumps({"status": "error", "error": str(e)})
    finally:
        prod_cur.close()
        staging_cur.close()
        prod_conn.close()
        staging_conn.close()

    return json.dumps(
        {
            "status": "success",
            "table_comparison": {
                "schema_matches": prod_tables == staging_tables,
                "tables_only_in_prod": only_in_prod,
                "tables_only_in_staging": only_in_staging,
                "shared_tables_present_in_both": shared_tables,
                "summary": {
                    "total_prod_tables": len(prod_tables),
                    "total_staging_tables": len(staging_tables)
                }
            },
        },
        indent=2,
    )
    
if __name__ == "__main__":
    mcp.run(transport="stdio")