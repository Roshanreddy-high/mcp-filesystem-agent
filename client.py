from __future__ import annotations

import asyncio
import json
import logging
import os
import sys
from contextlib import AsyncExitStack
from pathlib import Path
from typing import Any

from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client
from ollama import chat


def load_env_file(env_path: Path) -> None:
    if not env_path.exists():
        return
    for line in env_path.read_text(encoding="utf-8").splitlines():
        stripped = line.strip()
        if not stripped or stripped.startswith("#") or "=" not in stripped:
            continue
        key, value = stripped.split("=", 1)
        key = key.strip()
        value = value.strip().strip('"').strip("'")
        os.environ.setdefault(key, value)


def remove_code_fences(raw: str) -> str:
    return raw.replace("```json", "").replace("```", "").strip()


def maybe_json(raw: str) -> dict[str, Any] | None:
    # fast path — full string is clean JSON
    cleaned = remove_code_fences(raw)
    try:
        parsed = json.loads(cleaned)
        if isinstance(parsed, dict):
            return parsed
    except json.JSONDecodeError:
        pass

    # fallback — extract first complete {...} block by brace counting
    depth = 0
    start = None
    for i, ch in enumerate(raw):
        if ch == '{':
            if depth == 0:
                start = i
            depth += 1
        elif ch == '}':
            depth -= 1
            if depth == 0 and start is not None:
                candidate = raw[start:i+1]
                try:
                    parsed = json.loads(candidate)
                    if isinstance(parsed, dict):
                        return parsed
                except json.JSONDecodeError:
                    pass
                start = None
    return None


def normalize_arguments(arguments: dict[str, Any]) -> dict[str, Any]:
    normalized = {}

    for key, value in arguments.items():

        if isinstance(value, str):

            lower = value.lower()

            if lower == "true":
                normalized[key] = True
                continue

            elif lower == "false":
                normalized[key] = False
                continue

        normalized[key] = value

    return normalized

def schema_validate(arguments: dict[str, Any], schema: dict[str, Any]) -> tuple[bool, str]:
    required = schema.get("required", [])
    props = schema.get("properties", {})

    for key in required:
        if key not in arguments:
            return False, f"Missing required argument: {key}"

    type_map = {
        "string": str,
        "boolean": bool,
        "integer": int,
        "number": (int, float),
        "object": dict,
        "array": list,
    }
    for key, value in arguments.items():
        if key not in props:
            return False, f"Unexpected argument: {key}"
        expected_type = props[key].get("type")
        if expected_type and expected_type in type_map and not isinstance(value, type_map[expected_type]):
            return False, f"Invalid type for '{key}': expected {expected_type}"
    return True, "ok"

def build_system_prompt(tool_schemas: dict[str, dict[str, Any]]) -> str:
    tool_lines = []

    for name, schema in sorted(tool_schemas.items()):
        required = ", ".join(schema.get("required", [])) or "none"
        props = ", ".join(sorted(schema.get("properties", {}).keys())) or "none"

        tool_lines.append(
            f"- {name} (required: {required}; args: {props})"
        )

    tool_block = "\n".join(tool_lines)

    return (
        "You are an assistant that can call MCP tools.\n\n"

        "IMPORTANT RULES:\n"
        '- write_file mode must ONLY be "overwrite" or "append"\n'
        '- NEVER use mode "w"\n'
        "- If overwriting existing files, ALWAYS set confirm_overwrite=true\n"
        "- Reply ONLY with ONE JSON object at a time\n"
        "- Never include explanations before or after JSON\n"
        "- Never invent arguments not defined in tool schemas\n\n"

        "If tool use is needed, reply ONLY with JSON in this exact shape:\n"
        '{"tool":"<tool_name>","arguments":{...}}\n\n'

        "Otherwise reply with a normal user-facing answer.\n\n"

        f"Available tools:\n{tool_block}\n\n"

        "VALID TOOL EXAMPLES:\n"

        '{"tool":"create_file","arguments":{"path":"test.py","overwrite":true}}\n'

        '{"tool":"write_file","arguments":{"path":"test.py","content":"print(\\"hello\\")","mode":"overwrite","confirm_overwrite":true}}\n'

        '{"tool":"read_file","arguments":{"path":"test.py"}}\n'

        '{"tool":"list_files","arguments":{"path":"."}}\n'
    )

class MCPBridge:
    def __init__(self, server_script: Path):
        self.server_script = server_script
        self.exit_stack = AsyncExitStack()
        self.session: ClientSession | None = None

    async def connect(self) -> None:
        params = StdioServerParameters(command=sys.executable, args=[str(self.server_script.resolve())])
        read_stream, write_stream = await self.exit_stack.enter_async_context(stdio_client(params))
        self.session = await self.exit_stack.enter_async_context(ClientSession(read_stream, write_stream))
        await self.session.initialize()

    async def close(self) -> None:
        await self.exit_stack.aclose()

    async def get_tool_schemas(self) -> dict[str, dict[str, Any]]:
        if self.session is None:
            raise RuntimeError("MCP session is not connected")
        tools = await self.session.list_tools()
        return {tool.name: tool.inputSchema for tool in tools.tools}

    async def call_tool(self, name: str, arguments: dict[str, Any]) -> dict[str, Any]:
        if self.session is None:
            raise RuntimeError("MCP session is not connected")
        result = await self.session.call_tool(name, arguments)
        return result.model_dump()


async def run_chat() -> None:
    project_root = Path(__file__).resolve().parent
    load_env_file(project_root / ".env")

    model_name = os.getenv("OLLAMA_MODEL", "qwen2.5-coder:7b")
    max_tool_steps = int(os.getenv("MAX_TOOL_STEPS", "6"))
    server_script = Path(os.getenv("MCP_SERVER_SCRIPT", str(project_root / "server.py")))
    log_path = Path(os.getenv("AUDIT_LOG_PATH", str(project_root / "logs" / "audit.log")))
    log_path.parent.mkdir(parents=True, exist_ok=True)

    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(message)s",
        handlers=[logging.FileHandler(log_path, encoding="utf-8")],
    )
    logger = logging.getLogger("mcp-client")

    bridge = MCPBridge(server_script)
    try:
        await bridge.connect()
    except Exception as e:
        print(f"[ERROR] Failed to connect to MCP server: {e}")
        return

    tool_schemas = await bridge.get_tool_schemas()
    system_prompt = build_system_prompt(tool_schemas)

    print("Simple MCP Agent Started")
    print("Type 'exit' to quit\n")

    try:
        while True:
            user_input = input("You: ").strip()
            if user_input.lower() == "exit":
                break

            logger.info("user_input=%s", user_input)
            messages: list[dict[str, str]] = [
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": user_input},
            ]

            final_answer = None
            for _ in range(max_tool_steps):
                response = chat(model=model_name, messages=messages)
                content = response.message.content  # ✅ Fix #1
                logger.info("model_output=%s", content)

                tool_call = maybe_json(content)
                if not tool_call:
                    final_answer = content
                    break

                tool_name = tool_call.get("tool")
                arguments = tool_call.get("arguments")

                # ✅ Fix #2 — validate before normalize
                if not isinstance(tool_name, str) or not isinstance(arguments, dict):
                    final_answer = "I could not execute a tool call because the JSON format was invalid."
                    break

                arguments = normalize_arguments(arguments)

                if tool_name not in tool_schemas:
                    tool_result = {"success": False, "error": {"code": "unknown_tool", "message": tool_name}}
                else:
                    valid, reason = schema_validate(arguments, tool_schemas[tool_name])
                    if not valid:
                        tool_result = {"success": False, "error": {"code": "invalid_arguments", "message": reason}}
                    else:
                        try:
                            # ✅ Fix #4 — timeout guard
                            tool_result = await asyncio.wait_for(
                                bridge.call_tool(tool_name, arguments), timeout=30.0
                            )
                        except asyncio.TimeoutError:
                            tool_result = {"success": False, "error": {"code": "timeout", "message": "Tool timed out"}}

                logger.info("tool_call=%s args=%s result=%s", tool_name, arguments, tool_result)

                messages.append({"role": "assistant", "content": remove_code_fences(content)})
                messages.append({
                    "role": "user",
                    "content": (
                        "Tool result (JSON):\n"
                        f"{json.dumps(tool_result, ensure_ascii=True)}\n"
                        "Tool execution completed.\n"
                        "If another tool is needed, return EXACTLY ONE next tool JSON object.\n"
                        "Do NOT return multiple tool calls together.\n"
                        "If task is complete, return a normal final response."
                    ),
                })

            if final_answer is None:
                logger.warning("MAX_TOOL_STEPS=%d exhausted for: %s", max_tool_steps, user_input)  # ✅ Fix #6
                final_answer = "I hit MAX_TOOL_STEPS before completing. Please narrow the request."

            print("\nAssistant:")
            print(final_answer)
            print()
    finally:
        await bridge.close()

if __name__ == "__main__":
    asyncio.run(run_chat())
