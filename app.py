"""
Advanced AI Agent (Python)
==========================
Ek modular AI agent framework jisme ye features hain:

1. Tool-calling (function calling) - agent khud decide karta hai kaunsa tool use karna hai
2. Memory - conversation history yaad rakhta hai
3. ReAct-style reasoning loop (Think -> Act -> Observe -> repeat)
4. Multiple custom tools (calculator, web search placeholder, file read/write)
5. Anthropic Claude API use kiya hai (aap OpenAI ya kisi bhi LLM se replace kar sakte ho)

Requirements:
    pip install anthropic --break-system-packages

Environment variable set karo:
    export ANTHROPIC_API_KEY="your-key-here"
"""

import os
import json
import math
from datetime import datetime
from anthropic import Anthropic


# ---------------------------------------------------------
# 1. TOOL DEFINITIONS
# ---------------------------------------------------------
# Har tool ka ek schema (LLM ko batane ke liye) aur ek actual Python function hota hai.

def calculator_tool(expression: str) -> str:
    """Simple safe calculator."""
    try:
        allowed = {"sqrt": math.sqrt, "pi": math.pi, "sin": math.sin, "cos": math.cos}
        result = eval(expression, {"__builtins__": {}}, allowed)
        return str(result)
    except Exception as e:
        return f"Error: {e}"


def get_current_time_tool(_: str = "") -> str:
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S")


def note_writer_tool(content: str) -> str:
    """Agent ki apni memory file me note likhta hai."""
    with open("agent_notes.txt", "a", encoding="utf-8") as f:
        f.write(content.strip() + "\n")
    return "Note saved successfully."


def note_reader_tool(_: str = "") -> str:
    if not os.path.exists("agent_notes.txt"):
        return "No notes yet."
    with open("agent_notes.txt", "r", encoding="utf-8") as f:
        return f.read()


# Tool registry: naam -> (function, schema)
TOOLS = {
    "calculator": {
        "func": calculator_tool,
        "schema": {
            "name": "calculator",
            "description": "Mathematical expressions solve karta hai. Example: 'sqrt(16) + 5*2'",
            "input_schema": {
                "type": "object",
                "properties": {
                    "expression": {"type": "string", "description": "Math expression to evaluate"}
                },
                "required": ["expression"],
            },
        },
    },
    "get_current_time": {
        "func": get_current_time_tool,
        "schema": {
            "name": "get_current_time",
            "description": "Current date aur time return karta hai.",
            "input_schema": {"type": "object", "properties": {}},
        },
    },
    "save_note": {
        "func": note_writer_tool,
        "schema": {
            "name": "save_note",
            "description": "Kisi important information ko permanent note ke roop me save karta hai.",
            "input_schema": {
                "type": "object",
                "properties": {"content": {"type": "string", "description": "Note text"}},
                "required": ["content"],
            },
        },
    },
    "read_notes": {
        "func": note_reader_tool,
        "schema": {
            "name": "read_notes",
            "description": "Pehle se saved sabhi notes padhta hai.",
            "input_schema": {"type": "object", "properties": {}},
        },
    },
}


# ---------------------------------------------------------
# 2. AGENT CLASS
# ---------------------------------------------------------

class AdvancedAgent:
    def __init__(self, system_prompt: str = None, model: str = "claude-sonnet-4-5", max_steps: int = 8):
        self.client = Anthropic()  # ANTHROPIC_API_KEY env var se uthayega
        self.model = model
        self.max_steps = max_steps
        self.system_prompt = system_prompt or (
            "Tum ek helpful, careful AI agent ho. Tumhare paas tools hain jo tum "
            "step-by-step reasoning karke use kar sakte ho. Jab tak final answer "
            "ready na ho, tools ka use karte raho."
        )
        self.messages = []  # conversation memory
        self.tool_schemas = [t["schema"] for t in TOOLS.values()]

    def _call_model(self):
        return self.client.messages.create(
            model=self.model,
            max_tokens=1024,
            system=self.system_prompt,
            tools=self.tool_schemas,
            messages=self.messages,
        )

    def _execute_tool(self, tool_name: str, tool_input: dict) -> str:
        if tool_name not in TOOLS:
            return f"Unknown tool: {tool_name}"
        func = TOOLS[tool_name]["func"]
        # single-arg tools ke liye pehla value nikaal lo
        arg = next(iter(tool_input.values()), "")
        return func(arg)

    def run(self, user_input: str) -> str:
        self.messages.append({"role": "user", "content": user_input})

        for step in range(self.max_steps):
            response = self._call_model()

            # Agar model ne tool call kiya hai
            if response.stop_reason == "tool_use":
                self.messages.append({"role": "assistant", "content": response.content})

                tool_results = []
                for block in response.content:
                    if block.type == "tool_use":
                        print(f"[Agent Step {step+1}] Using tool: {block.name} -> {block.input}")
                        result = self._execute_tool(block.name, block.input)
                        tool_results.append({
                            "type": "tool_result",
                            "tool_use_id": block.id,
                            "content": result,
                        })

                self.messages.append({"role": "user", "content": tool_results})
                continue  # loop wapas jaayega, model final answer dega ya aur tool call karega

            # Final text answer mil gaya
            final_text = "".join(b.text for b in response.content if b.type == "text")
            self.messages.append({"role": "assistant", "content": final_text})
            return final_text

        return "Max steps reached, agent ruk gaya."


# ---------------------------------------------------------
# 3. USAGE EXAMPLE
# ---------------------------------------------------------

if __name__ == "__main__":
    agent = AdvancedAgent()

    print(agent.run("Mera naam Rahul hai, isko note kar lo."))
    print(agent.run("Ab current time batao aur sqrt(144) nikaalo."))
    print(agent.run("Tumhe mera naam yaad hai kya? Notes padh kar batao."))
