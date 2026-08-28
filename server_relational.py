"""
server_relational.py — MCP server for the NEW multi-table relational
database (ecommerce_relational): customers, products, orders, order_items.

Separate file from server.py on purpose — that one talks to the original
flat 'orders' table in the 'ecommerce' database; this one talks to the
new 4-table relational database. Two different databases, two servers.
"""

import os
import json
import psycopg2
import psycopg2.extras
from mcp.server.fastmcp import FastMCP
from dotenv import load_dotenv


load_dotenv()


# Note the different variable name and different database name at the end
# of the URL ('ecommerce_relational', not 'ecommerce') — this is what
# points this server at the NEW database specifically.
RELATIONAL_DATABASE_URL = os.environ.get(
    "RELATIONAL_DATABASE_URL",
    "postgresql://postgres:devpass@localhost:5432/ecommerce_relational",
)

mcp = FastMCP("relational-investigator")

def _get_connection():
    return psycopg2.connect(RELATIONAL_DATABASE_URL)

@mcp.tool()
def get_order_details(limit: int = 5) -> str:
    """Fetch order details joined across all 4 tables: who placed the
    order, what they bought, and the line-item cost. Most recent orders
    first."""

    conn = _get_connection()
    
    cur = conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)
    
    cur.execute(
        """
        SELECT 
            o.order_id,
            o.order_date,
            o.order_status,
            o.warehouse_region,
            c.country AS customer_country,
            c.customer_segment,
            p.product_name,
            p.category,
            oi.quantity,
            oi.unit_price_at_purchase,
            (oi.quantity * oi.unit_price_at_purchase) AS line_total
        FROM orders o
        JOIN customers c ON o.customer_id = c.customer_id
        JOIN order_items oi ON oi.order_id = o.order_id
        JOIN products p ON p.product_id = oi.product_id
        ORDER BY o.order_date DESC
        LIMIT %s
        """,
        (limit,),
    )

    results = cur.fetchall()
    conn.close()

    return json.dumps(results, default=str, indent=2)

@mcp.tool()
def get_customer_order_history(customer_id: str) -> str:
    """Fetch every order a specific customer has placed, with line-item
    detail AND the correct order total (computed by the database, not
    estimated). Use get_order_details first if you don't know a specific
    customer_id yet."""

    # Step 1: open a connection
    conn = _get_connection()

    # Step 2: labeled results
    cur = conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)

    # Step 3: same 4-table JOIN as get_order_details, but this time
    # filtered down to just ONE customer via WHERE — and customer_id is a
    # VALUE here, not a table/column name, so it safely goes in as %s
    # Query 1: line-item detail
    
    cur.execute(
        """
        SELECT 
            o.order_id,
            o.order_date,
            o.order_status,
            o.warehouse_region,
            c.customer_segment,
            p.product_name,
            p.category,
            oi.quantity,
            oi.unit_price_at_purchase,
            (oi.quantity * oi.unit_price_at_purchase) AS line_total
        FROM orders o
        JOIN customers c ON o.customer_id = c.customer_id
        JOIN order_items oi ON oi.order_id = o.order_id
        JOIN products p ON p.product_id = oi.product_id
        WHERE o.customer_id = %s
        ORDER BY o.order_date DESC
        """,
        (customer_id,),
    )

    line_items  = cur.fetchall()

    if not line_items:
        conn.close()
        return json.dumps({"found": False, "message": f"No orders found for customer_id '{customer_id}'."})


    
    # Query 2: order-level totals, computed by Postgres's SUM() —
    # GROUP BY order_id collapses all line items of one order into a
    # single row with the guaranteed-correct total - In short the total 
    # is computed by the database, not estimated by the agent. 
    # Query 2: order-level totals — SUM per order
    cur.execute(
        """
        SELECT 
            o.order_id,
            SUM(oi.quantity * oi.unit_price_at_purchase) AS order_total
        FROM orders o
        JOIN order_items oi ON oi.order_id = o.order_id
        WHERE o.customer_id = %s
        GROUP BY o.order_id
        """,
        (customer_id,),
    )
    
    totals_by_order = {row["order_id"]: row["order_total"] for row in cur.fetchall()}
    
    # Query 3: NEW — distinct order count, computed by SQL, not left for
    # the LLM to count JSON entries itself
    cur.execute(
        "SELECT COUNT(DISTINCT order_id) AS order_count FROM orders WHERE customer_id = %s",
        (customer_id,),
    )
    order_count = cur.fetchone()["order_count"]

    
    conn.close()
    
    return json.dumps(
        {
            "line_items": line_items,
            "order_count": order_count, # NEW — total number of orders for this customer, computed by SQL
            "order_totals": totals_by_order # NEW — dict of order_id -> total, computed by SQL
        },
        default=str,
        indent=2,
    )
    
@mcp.tool()
def get_revenue_by_category_all_time() -> str:
    """Get total revenue and order count broken down by product category,
    across ALL data (no date filter). All numbers computed by the
    database, not estimated."""

    conn = _get_connection()
    cur = conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)
    cur.execute(
        """
        SELECT 
            p.category,
            COUNT(DISTINCT o.order_id) AS order_count,
            SUM(oi.quantity * oi.unit_price_at_purchase) AS revenue
        FROM orders o
        JOIN order_items oi ON oi.order_id = o.order_id
        JOIN products p ON p.product_id = oi.product_id
        GROUP BY p.category
        ORDER BY revenue DESC
        """
    )
    rows = cur.fetchall()
    conn.close()
    return json.dumps(rows, default=str, indent=2)


@mcp.tool()
def get_revenue_by_category_in_range(start_date: str, end_date: str) -> str:
    """Get total revenue and order count broken down by product category,
    within a SPECIFIC date range (both dates required, format YYYY-MM-DD).
    All numbers computed by the database, not estimated."""

    conn = _get_connection()
    cur = conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)
    cur.execute(
        """
        SELECT 
            p.category,
            COUNT(DISTINCT o.order_id) AS order_count,
            SUM(oi.quantity * oi.unit_price_at_purchase) AS revenue
        FROM orders o
        JOIN order_items oi ON oi.order_id = o.order_id
        JOIN products p ON p.product_id = oi.product_id
        WHERE o.order_date BETWEEN %s AND %s
        GROUP BY p.category
        ORDER BY revenue DESC
        """,
        (start_date, end_date),
    )
    rows = cur.fetchall()
    conn.close()
    return json.dumps(rows, default=str, indent=2)

if __name__ == "__main__":
    mcp.run(transport="stdio")