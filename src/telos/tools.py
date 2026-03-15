from typing import Dict, Any, Optional
from .interfaces import Tool
from .sandbox import SandboxManager

class BashTool(Tool):
    """Executes a bash command in the sandbox."""
    def __init__(self, sandbox: SandboxManager):
        self.sandbox = sandbox

    def execute(self, params: Dict[str, Any]) -> str:
        command = params.get("command")
        if not command:
            return "Error: No command provided."
        res = self.sandbox.execute_command(command)
        return f"Exit code: {res['exit_code']}\nOutput:\n{res['output']}"

    @property
    def definition(self) -> Dict[str, Any]:
        return {
            "type": "function",
            "function": {
                "name": "execute_command",
                "description": "Execute a bash command in the secure sandbox.",
                "parameters": {
                    "type": "object",
                    "properties": {
                        "command": {"type": "string", "description": "The command to run."}
                    },
                    "required": ["command"]
                }
            }
        }

class WriteFileTool(Tool):
    """Writes a file to the sandbox workspace."""
    def __init__(self, sandbox: SandboxManager):
        self.sandbox = sandbox

    def execute(self, params: Dict[str, Any]) -> str:
        path = params.get("path")
        content = params.get("content")
        if not path or content is None:
            return "Error: path and content are required."
        self.sandbox.write_file(path, content)
        return f"Successfully wrote to {path}."

    @property
    def definition(self) -> Dict[str, Any]:
        return {
            "type": "function",
            "function": {
                "name": "write_file",
                "description": "Write a file to the sandbox workspace.",
                "parameters": {
                    "type": "object",
                    "properties": {
                        "path": {"type": "string", "description": "Relative path to the file."},
                        "content": {"type": "string", "description": "Content to write."}
                    },
                    "required": ["path", "content"]
                }
            }
        }

class ReadFileTool(Tool):
    """Reads a file from the sandbox workspace."""
    def __init__(self, sandbox: SandboxManager):
        self.sandbox = sandbox

    def execute(self, params: Dict[str, Any]) -> str:
        path = params.get("path")
        if not path:
            return "Error: path is required."
        return self.sandbox.read_file(path)

    @property
    def definition(self) -> Dict[str, Any]:
        return {
            "type": "function",
            "function": {
                "name": "read_file",
                "description": "Read a file from the sandbox workspace.",
                "parameters": {
                    "type": "object",
                    "properties": {
                        "path": {"type": "string", "description": "Relative path to the file."}
                    },
                    "required": ["path"]
                }
            }
        }

class TaskCompleteTool(Tool):
    """エージェントがタスク完了を宣言するためのツール"""
    
    def execute(self, params: Dict[str, Any]) -> str:
        summary = params.get("summary", "Task completed.")
        return f"TASK_COMPLETE: {summary}"
    
    @property
    def definition(self) -> Dict[str, Any]:
        return {
            "type": "function",
            "function": {
                "name": "task_complete",
                "description": "Call this when the goal is fully achieved. Do not call any other tools after this.",
                "parameters": {
                    "type": "object",
                    "properties": {
                        "summary": {
                            "type": "string",
                            "description": "What was accomplished and where the output was saved."
                        }
                    },
                    "required": ["summary"]
                }
            }
        }

class ToolRegistry:
    def __init__(self, sandbox: SandboxManager):
        self._tools: Dict[str, Tool] = {}
        self.sandbox = sandbox
        self._register_defaults()

    def _register_defaults(self):
        self.register("execute_command", BashTool(self.sandbox))
        self.register("write_file", WriteFileTool(self.sandbox))
        self.register("read_file", ReadFileTool(self.sandbox))
        self.register("task_complete", TaskCompleteTool())

    def register(self, name: str, tool: Tool):
        self._tools[name] = tool

    def get(self, name: str) -> Optional[Tool]:
        return self._tools.get(name)

    def get_definitions(self) -> list[dict]:
        return [t.definition for t in self._tools.values()]

def get_standard_tool_definitions() -> list[dict]:
    """Returns a list of all standard tool definitions for LLM registration."""
    # Dummy registry to get definitions without a live sandbox
    registry = ToolRegistry(None)
    return registry.get_definitions()
