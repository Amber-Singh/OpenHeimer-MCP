"""
client.py — the full agent loop: ask Groq, execute any tool it requests,
feed the result back, repeat until Groq gives a final answer.
"""

import os
import json
import asyncio
from dotenv import load_dotenv
from groq import BadRequestError, Groq
from fastmcp import Client

load_dotenv()
groq_client = Groq()

with open("mcp_config.json", "r") as f:
    MCP_CONFIG = json.load(f)
    
MAX_TOOL_ROUNDS = 10 # safety cap so a confused agent can't loop forever

SYSTEM_PROMPT = (
    "Rules you must follow: "

    "1. Do not answer using your own knowledge or guesses — "
    "you have no information about this database. "

    "2. Always use the provided tools to get real results from the "
    "database first. "

    "3. Base your answer ONLY on what the tools actually return — "
    "never fill in, invent, or assume anything yourself. "

    "4. Decide which tool or tools are needed based on the user's "
    "question and the results of previous tool calls. "

    "5. You may call multiple tools. After receiving a tool result, "
    "analyze whether additional investigation is required before "
    "giving a final answer. "

    "6. Drift tools are ONLY for database comparison. Use them only when "
    "the user explicitly asks to compare databases or identify differences. "

    "7. Do not use drift tools for any other purpose. "

    "8. When using drift tools, report only the differences found. "
    "Do not perform any further analysis or action on those differences. "

    "9. When the investigation is complete, provide a clear summary "
    "based only on the collected tool results."
    
    "10. If the user's question is a read operation that cannot be answered "
    "by the existing database tools, use the SQL generation tool. "
    "The SQL generation tool must only generate and validate read-only SQL. "
    "Never execute generated SQL automatically."
    
    "11. If the user asks for SQL, asks to generate SQL, asks how to query "
    "the database, or asks a read question that requires information not "
    "available through the existing database tools, you MUST call the "
    "`generate_read_sql` tool. Do not generate SQL yourself. "
    
    "12. Pass the user's question to tools unchanged. Never interpret "
    "or modify it. If clarification is requested, return it to the user "
    "without further tool calls."
    
    "13. For debug, diagnose, or troubleshoot requests, first collect relevant "
    "database evidence using the available tools. Then use the appropriate "
    "debugging capability to analyze the evidence and provide the problem, "
    "evidence, likely cause, and suggestion. Do not invent unsupported facts. "
)

def mcp_tools_to_groq_format(mcp_tools):
    return [
        {
            "type": "function",
            "function": {
                "name": t.name,
                "description": t.description,
                "parameters": t.inputSchema,
            },
        }
        for t in mcp_tools
    ]


async def ask(question: str) -> str:
    """Send a question to Groq. If report_mode is True, ask for a
    structured report format; otherwise, just a plain direct answer."""

    async with Client(MCP_CONFIG) as mcp_client:
        # Step 1: get our real tools, converted to Groq's format
        mcp_tools = await mcp_client.list_tools()
        
        print("\n========== AVAILABLE MCP TOOLS ==========")

        for tool in mcp_tools:
            print("TOOL:", tool.name)

        print("=========================================\n")
        
        groq_tools = mcp_tools_to_groq_format(mcp_tools)

        # Step 2: start the conversation with just the user's question
        
        messages = [ 
                    {"role": "system", "content": SYSTEM_PROMPT},
                    {"role": "user", "content": question}
        ]

        # Step 3: loop — each pass is one round of "ask Groq, maybe run a tool"
        for round_num in range(MAX_TOOL_ROUNDS):
            
            try: 
                response = groq_client.chat.completions.create(
                    model="openai/gpt-oss-120b",
                    messages=messages,
                    tools=groq_tools,
                    tool_choice="required" if round_num == 0 else "auto",
                )
            
            except BadRequestError as e:
                return f"Error: {e}"

            except Exception as e:
                return f"Error while contacting AI model: {e}"
            
            message = response.choices[0].message
            print(f"Groq says: {message.content}{message.tool_calls}")
            # Step 4: if Groq did NOT ask for a tool, it's giving its final
            # answer — we're done
            if not message.tool_calls:
                return message.content

            # Step 5: Groq DID ask for a tool. First, record its request
            # in the conversation history (required — Groq needs to see
            # its own prior tool_calls when we send the result back)
            messages.append({
                    "role": "assistant",
                    "content": message.content,
                    "tool_calls": [
                        {
                            "id": call.id,
                            "type": "function",
                            "function": {
                                "name": call.function.name,
                                "arguments": call.function.arguments,
                            },
                        }
                        for call in message.tool_calls
                    ],
                })
            print(f"Groq requested {len(message.tool_calls)} tool calls.{message}")
            # Step 6: actually execute each requested tool call for real
            for call in message.tool_calls:
                tool_name = call.function.name
                tool_args = json.loads(call.function.arguments)

                print(f"  -> running tool: {tool_name}({tool_args})")

                result = await mcp_client.call_tool(tool_name, tool_args)
                result_text = result.content[0].text if result.content else ""

                # Step 7: add the tool's real result to the conversation,
                # tagged with tool_call_id so Groq knows which request
                # this result answers
                messages.append({
                    "role": "tool",
                    "tool_call_id": call.id,
                    "content": result_text,
                })

            # loop continues — next pass sends the tool result BACK to
            # Groq, which will either ask for another tool or answer

        return "Gave up after too many tool-call rounds — question may be too complex or ambiguous."