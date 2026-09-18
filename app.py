"""
Advanced AI Agent (Provider-Independent, Local LLM)
Runs on Ollama - no API key, no cloud dependency, fully offline.
"""

from __future__ import annotations

import json
import logging
import math
import os
import re
import sys
from dataclasses import dataclass, field
from datetime import datetime
from typing import Callable, Dict, List, Optional

import requests

# ---------------------------------------------------------------------------
# CONFIGURATION
# ---------------------------------------------------------------------------

OLLAMA_URL = "http://localhost:11434/api/generate"
MODEL_NAME = "llama3.1"
MEMORY_FILE = "agent_memory.json"
MAX_STEPS = 8
REQUEST_TIMEOUT = 120

logging.basicConfig(
    level=logging.INFO,
    format="[%(asctime)s] %(levelname)s: %(message)s",
    datefmt="%H:%M:%S",
)
logger = logging.getLogger("agent")


# ---------------------------------------------------------------------------
# LOCAL LLM CLIENT
# ---------------------------------------------------------------------------

class LocalLLM:
    def __init__(self, model: str = MODEL_NAME, url: str = OLLAMA_URL):
        self.model = model
        self.url = url

    def generate(self, prompt: str, retries: int = 2) -> str:
        last_error: Optional[Exception] = None
        for attempt in range(1, retries + 1):
            try:
                response = requests.post(
                    self.url,
                    json={"model": self.model, "prompt": prompt, "stream": False},
                    timeout=REQUEST_TIMEOUT,
                )
                response.raise_for_status()
                return response.json()["response"].strip()
            except Exception as exc:
                last_error = exc
                logger.warning("LLM call failed (attempt %d/%d): %s", attempt, retries, exc)
        raise RuntimeError(f"Local LLM unreachable after {retries} attempts: {last_error}")


# ---------------------------------------------------------------------------
# PERSISTENT MEMORY
# ---------------------------------------------------------------------------

class Memory:
    def __init__(self, path: str = MEMORY_FILE):
        self.path = path
        self.data: Dict = {"facts": [], "conversation": []}
        self._load()

    def _load(self) -> None:
        if os.path.exists(self.path):
            with open(self.path, "r", encoding="utf-8") as f:
                self.data = json.load(f)

    def _save(self) -> None:
        with open(self.path, "w", encoding="utf-8") as f:
            json.dump(self.data, f, indent=2, ensure_ascii=False)

    def remember(self, fact: str) -> None:
        self.data["facts"].append({"fact": fact, "timestamp": datetime.now().isoformat()})
        self._save()

    def recall_all(self) -> List[str]:
        return [entry["fact"] for entry in self.data["facts"]]

    def log_turn(self, role: str, text: str) -> None:
        self.data["conversation"].append({"role": role, "text": text})
        self._save()

    def conversation_text(self, limit: int = 20) -> str:
        turns = self.data["conversation"][-limit:]
        return "\n".join(f"{t['role']}: {t['text']}" for t in turns)


# ---------------------------------------------------------------------------
# TOOLS
# ---------------------------------------------------------------------------

@dataclass
class Tool:
    name: str
    description: str
    func: Callable[[str], str]


def tool_calculator(expression: str) -> str:
    allowed = {"sqrt": math.sqrt, "pi": math.pi, "sin": math.sin, "cos": math.cos, "log": math.log}
    try:
        return str(eval(expression, {"__builtins__": {}}, allowed))
    except Exception as exc:
        return f"Error evaluating expression: {exc}"


def tool_current_time(_: str = "") -> str:
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S")


def make_memory_tools(memory: Memory) -> Dict[str, Tool]:
    def save_note(text: str) -> str:
        memory.remember(text.strip())
        return "Saved to long-term memory."

    def read_notes(_: str = "") -> str:
        facts = memory.recall_all()
        return "\n".join(facts) if facts else "No notes stored yet."

    return {
        "save_note": Tool("save_note", "Store a fact permanently. Usage: save_note[text]", save_note),
        "read_notes": Tool("read_notes", "Retrieve all stored facts. Usage: read_notes[]", read_notes),
    }


def build_toolset(memory: Memory) -> Dict[str, Tool]:
    tools: Dict[str, Tool] = {
        "calculator": Tool(
            "calculator", "Evaluate a math expression. Usage: calculator[2 + 2 * sqrt(9)]", tool_calculator
        ),
        "get_current_time": Tool(
            "get_current_time", "Get the current date and time. Usage: get_current_time[]", tool_current_time
        ),
    }
    tools.update(make_memory_tools(memory))
    return tools


# ---------------------------------------------------------------------------
# AGENT (ReAct loop)
# ---------------------------------------------------------------------------

ACTION_PATTERN = re.compile(r"ACTION:\s*(\w+)\[(.*?)\]", re.DOTALL)
FINAL_PATTERN = re.compile(r"FINAL:\s*(.*)", re.DOTALL)


@dataclass
class Agent:
    llm: LocalLLM = field(default_factory=LocalLLM)
    memory: Memory = field(default_factory=Memory)
    max_steps: int = MAX_STEPS
    tools: Dict[str, Tool] = field(init=False)

    def __post_init__(self) -> None:
        self.tools = build_toolset(self.memory)

    def _system_prompt(self) -> str:
        tool_lines = "\n".join(f"- {t.name}: {t.description}" for t in self.tools.values())
        return f"""You are a careful, reasoning AI agent. You think step by step.

Available tools:
{tool_lines}

Rules:
- To use a tool, respond with exactly: ACTION: tool_name[input]
- When you know the final answer, respond with exactly: FINAL: <answer>
- Only take ONE action per turn.
- Never fabricate tool results - always wait for the real observation.
"""

    def _prompt(self, user_input: str) -> str:
        history = self.memory.conversation_text()
        return (
            f"{self._system_prompt()}\n\n"
            f"Conversation so far:\n{history}\n"
            f"User: {user_input}\n"
            f"Agent:"
        )

    def run(self, user_input: str) -> str:
        self.memory.log_turn("User", user_input)
        working_prompt = self._prompt(user_input)

        for step in range(1, self.max_steps + 1):
            raw = self.llm.generate(working_prompt)
            logger.info("Step %d raw output: %s", step, raw.replace("\n", " ")[:200])

            final_match = FINAL_PATTERN.search(raw)
            if final_match:
                answer = final_match.group(1).strip()
                self.memory.log_turn("Agent", answer)
                return answer

            action_match = ACTION_PATTERN.search(raw)
            if action_match:
                tool_name, tool_input = action_match.group(1), action_match.group(2)
                if tool_name in self.tools:
                    observation = self.tools[tool_name].func(tool_input)
                    logger.info("Tool '%s' called with '%s' -> %s", tool_name, tool_input, observation)
                    working_prompt += (
                        f"\nAgent: ACTION: {tool_name}[{tool_input}]"
                        f"\nObservation: {observation}\nAgent:"
                    )
                    continue
                else:
                    working_prompt += (
                        f"\nAgent: ACTION: {tool_name}[{tool_input}]"
                        f"\nObservation: Unknown tool '{tool_name}'.\nAgent:"
                    )
                    continue

            self.memory.log_turn("Agent", raw)
            return raw

        fallback = "I reached the maximum number of reasoning steps without a final answer."
        self.memory.log_turn("Agent", fallback)
        return fallback


# ---------------------------------------------------------------------------
# CLI ENTRY POINT
# ---------------------------------------------------------------------------

def main() -> None:
    agent = Agent()
    print("Advanced local AI agent ready. Type 'exit' to quit.\n")

    while True:
        try:
            user_input = input("You: ").strip()
        except (EOFError, KeyboardInterrupt):
            print("\nGoodbye.")
            sys.exit(0)

        if user_input.lower() in {"exit", "quit"}:
            print("Goodbye.")
            break
        if not user_input:
            continue

        answer = agent.run(user_input)
        print(f"Agent: {answer}\n")


if __name__ == "__main__":
    main()
