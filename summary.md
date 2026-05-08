# Local MCP-Style AI Agent System

> A fully local, production-grade AI agent built from scratch —  
> local LLM · MCP transport · sandboxed filesystem · multi-step orchestration

---

## What You Built

A real agentic system where a local language model reasons over tasks,
selects tools, executes them through a structured protocol, and continues
reasoning from results — all running entirely on your machine.

```
User Prompt
    ↓
AI Model (Qwen via Ollama)
    ↓
Structured Tool JSON  ←── schema validation
    ↓
MCP Client Runtime    ←── timeout, error handling, audit log
    ↓
MCP Server (FastMCP)  ←── sandboxed to MCP_ALLOWED_ROOT
    ↓
Filesystem Tools
    ↓
Structured Result
    ↓
AI Continues Reasoning
    ↓
Final Answer
```

---

## Capabilities

| Task                | Example Prompt                                                           |
| ------------------- | ------------------------------------------------------------------------ |
| Read a file         | `Read app.py`                                                            |
| List a directory    | `List files in current directory`                                        |
| Search text         | `Search for "import" in client.py`                                       |
| Create a file       | `Create notes.txt`                                                       |
| Write code          | `Write a Flask hello world app into app.py`                              |
| Multi-step planning | `Create test.py, write a function into it, analyse it, search for "def"` |

---

## System Components

### `client.py` — Agent Runtime

- Connects to the MCP server over stdio transport via `ClientSession`
- Reads live tool schemas from the server at startup
- Runs an iterative reasoning loop (up to `MAX_TOOL_STEPS` per prompt)
- Validates tool call JSON against schemas before execution
- Enforces 30-second timeout per tool call
- Writes full audit trail to `logs/audit.log`

### `server.py` — Tool Server

- Exposes 6 filesystem tools via `FastMCP`
- All paths sandboxed to `MCP_ALLOWED_ROOT` — escapes blocked at resolution time
- Validates `MCP_ALLOWED_ROOT` exists as a directory at startup
- Returns consistent structured responses: `{ success, data, error }`
- Atomic file creation (no TOCTOU race condition)
- Append and overwrite both require explicit `confirm_overwrite=true`
- Timestamped backups prevent silent backup overwrites
- `search_word` streams line-by-line — never loads full file into memory
- `analyze_python_file` counts both `def` and `async def` functions

---

## Tools Exposed

```
read_file(path)
list_files(path=".")
analyze_python_file(path)
search_word(path, word)
create_file(path, overwrite=False)
write_file(path, content, mode, confirm_overwrite, create_backup)
```

---

## Safety & Reliability

- Path escape blocked — no access outside `MCP_ALLOWED_ROOT`
- Atomic file creation — no race conditions
- Both write modes guarded — append is not a silent bypass
- Size limits — `MCP_MAX_READ_BYTES` enforced across read, analyze, search
- Symlinks reported separately — not silently treated as files or directories
- Timestamped backups — `.py.1718000000.bak` format, never overwritten
- Tool timeout — hung server cannot freeze the agent loop
- Argument validation — schema-checked before any tool is called
- Startup guard — misconfigured root fails immediately with a clear message

---

## Response Contract

Every tool returns one of:

```json
{ "success": true,  "data": { ... }, "error": null }
{ "success": false, "data": null,    "error": { "code": "...", "message": "..." } }
```

Error codes: `permission_denied` · `not_found` · `already_exists` ·
`encoding_error` · `syntax_error` · `invalid_input` · `timeout` ·
`unknown_tool` · `os_error` · `unexpected_error`

---

## Configuration (`.env`)

```
OLLAMA_MODEL=qwen2.5-coder:7b
MAX_TOOL_STEPS=10
MCP_ALLOWED_ROOT=./workspace
MCP_MAX_READ_BYTES=2000000
MCP_FILE_ENCODING=utf-8
MCP_TRANSPORT=stdio
AUDIT_LOG_PATH=logs/audit.log
```

> Set `MAX_TOOL_STEPS=10` for multi-step prompts (create + write + analyse + search
> needs at least 5 steps, leaving room for retries on bad model outputs).

---

## Engineering Milestones Reached

- Local LLM driving real tool use with no cloud dependency
- MCP stdio transport — the same protocol used by Claude, Cursor, and VS Code agents
- Schema-validated tool dispatch — not prompt hacking, actual contract enforcement
- Sandboxed filesystem — production-grade path isolation
- Iterative execution loop — the core pattern behind Devin, Cursor, and OpenAI Agents
- Structured error propagation — machine-readable, not string matching
- Audit logging — full trace of every model output and tool call

---

## The Bigger Picture

What you built is the foundation behind:

- **Cursor** — AI coding assistant with file read/write/search
- **Claude tools** — structured tool use over MCP
- **OpenAI Agents SDK** — multi-step tool orchestration loop
- **Devin** — autonomous coding agent with filesystem access

The architecture is identical in principle.  
The difference is scale, model quality, and production hardening —  
not a fundamentally different design.
