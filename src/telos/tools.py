from typing import Dict, Any
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

def get_standard_tool_definitions() -> list[dict]:
    """Returns a list of all standard tool definitions for LLM registration."""
    # Dummy sandbox to avoid actual initialization
    dummy_sandbox = None 
    return [
        BashTool(dummy_sandbox).definition,
        WriteFileTool(dummy_sandbox).definition,
        ReadFileTool(dummy_sandbox).definition,
        TaskCompleteTool().definition
    ]
