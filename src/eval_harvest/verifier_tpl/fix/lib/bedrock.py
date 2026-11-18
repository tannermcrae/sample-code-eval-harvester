"""A small tool-use loop on the Amazon Bedrock Converse API, shared by the validation agent and the judges.

The verifier runs this in its own container after the agent stops (separate verifier mode, ADR-4). The
verifier phase can reach only the Bedrock runtime endpoint (see [verifier] in task.toml). Only boto3 and
the standard library are used. boto3 is imported where a client is made, so the stages that call no
model (the hidden tests, patch bloat, aggregate) run without it.

Ported from notebook 03 (``tests/lib/bedrock.py``). The paths come from the environment, with the
container paths as defaults, so the tests can point them at temporary directories.

This file is data that eval-harvest copies into each coding task; the CLI never imports it.
"""

from __future__ import annotations

import json
import os
import subprocess  # nosec B404 - the verifier runs git and rg on the repository under test, argv-only
import sys
import tomllib
from collections.abc import Callable, Mapping
from pathlib import Path
from typing import Any  # Converse requests, responses, and tool inputs are arbitrary JSON.

TESTS = Path(os.environ.get("EVAL_HARVEST_TESTS_DIR", "/tests"))
REPO = Path(os.environ.get("EVAL_HARVEST_REPO_DIR", "/work/repo"))
BASE = Path(os.environ.get("EVAL_HARVEST_BASE_DIR", "/work/base"))
LOGS = Path(os.environ.get("EVAL_HARVEST_VERIFIER_LOGS", "/logs/verifier"))
MAX_TOOL_OUTPUT = 12_000

#: test.sh runs every stage with PYTHONPATH set to the verifier's own boto3 directory. A command that
#: runs the repository's code must not see it, or the verifier's boto3 shadows the repository's own.
VERIFIER_ONLY_VARIABLES = ("PYTHONPATH",)

MODELS: dict[str, Any] = tomllib.loads((TESTS / "judge" / "models.toml").read_text())

JsonObject = dict[str, Any]


def model_for(role: str) -> str:
    """The model of a stage: VERIFIER_MODEL_ID from the environment, or the default in models.toml."""
    return os.environ.get("VERIFIER_MODEL_ID") or str(MODELS[role]["model"])


def client() -> Any:  # noqa: ANN401 - a boto3 client is untyped (boto3 is Any to mypy, pyproject.toml)
    """A bedrock-runtime client with adaptive retries and a long read timeout."""
    import boto3  # noqa: PLC0415 - imported here so the stages that call no model run without boto3
    from botocore.config import Config  # noqa: PLC0415

    return boto3.client(
        "bedrock-runtime",
        config=Config(retries={"max_attempts": 8, "mode": "adaptive"}, read_timeout=300),
    )


def repository_environment() -> dict[str, str]:
    """The environment for a command that runs the repository's own code: without the verifier's variables."""
    return {name: value for name, value in os.environ.items() if name not in VERIFIER_ONLY_VARIABLES}


class Tool:
    """A tool that the model can call: a name, a description, a JSON schema, and an implementation."""

    def __init__(self, name: str, description: str, schema: JsonObject, implementation: Callable[[JsonObject], str]) -> None:
        self.name, self.description, self.schema, self.implementation = name, description, schema, implementation

    def spec(self) -> JsonObject:
        """The Converse toolSpec of this tool."""
        return {"toolSpec": {"name": self.name, "description": self.description, "inputSchema": {"json": self.schema}}}


def clip_tool_output(text: str) -> str:
    """Cut a tool output to MAX_TOOL_OUTPUT characters, and say how much was cut."""
    if len(text) <= MAX_TOOL_OUTPUT:
        return text
    return text[:MAX_TOOL_OUTPUT] + f"\n[... {len(text) - MAX_TOOL_OUTPUT} more characters not shown]"


def resolve_inside_roots(path: str, roots: list[Path]) -> Path:
    """Resolve `path` (relative to the repository) and refuse it when it is outside every root."""
    resolved = (REPO / path).resolve() if not path.startswith("/") else Path(path).resolve()
    if not any(resolved == root or root in resolved.parents for root in roots):
        raise ValueError(f"{path} is outside the readable directories: {', '.join(map(str, roots))}")
    return resolved


def read_only_tools(roots: list[Path]) -> list[Tool]:
    """Tools to read the repository and the verifier files. Relative paths are relative to the repository."""

    def read_file(arguments: JsonObject) -> str:
        path = resolve_inside_roots(arguments["path"], roots)
        lines = path.read_text(errors="replace").splitlines()
        start = max(int(arguments.get("start_line", 1)), 1)
        end = min(int(arguments.get("end_line", start + 399)), len(lines))
        return "\n".join(f"{number:5} {lines[number - 1]}" for number in range(start, end + 1)) or "[empty file]"

    def list_directory(arguments: JsonObject) -> str:
        path = resolve_inside_roots(arguments.get("path", "."), roots)
        return "\n".join(entry.name + ("/" if entry.is_dir() else "") for entry in sorted(path.iterdir()) if entry.name != ".git")

    def search_code(arguments: JsonObject) -> str:
        path = resolve_inside_roots(arguments.get("path", "."), roots)
        command = ["rg", "--line-number", "--max-count", "20", "--glob", "!.git", arguments["pattern"], str(path)]
        output = subprocess.run(command, capture_output=True, text=True, timeout=60, check=False).stdout  # nosec B607 - rg from the verifier image's PATH; argv-only
        return output or "[no matches]"

    return [
        Tool(
            "read_file",
            "Read lines of a file (default: 400 lines from start_line).",
            {
                "type": "object",
                "properties": {"path": {"type": "string"}, "start_line": {"type": "integer"}, "end_line": {"type": "integer"}},
                "required": ["path"],
            },
            read_file,
        ),
        Tool(
            "list_directory", "List a directory.", {"type": "object", "properties": {"path": {"type": "string"}}}, list_directory
        ),
        Tool(
            "search_code",
            "Search with a ripgrep regular expression.",
            {
                "type": "object",
                "properties": {"pattern": {"type": "string"}, "path": {"type": "string"}},
                "required": ["pattern"],
            },
            search_code,
        ),
    ]


def acknowledge_submission(arguments: JsonObject) -> str:
    """The implementation of every submit tool: the loop reads the input itself, so the tool only acknowledges."""
    del arguments
    return "submitted"


class Conversation:
    """One model conversation with tools. The model ends a turn by calling the submit tool."""

    def __init__(self, role: str, system: str, tools: list[Tool], submit_name: str, submit_schema: JsonObject) -> None:
        role_config = MODELS[role]
        self.role, self.model, self.max_turns = role, model_for(role), int(role_config["max_turns"])
        self.max_tokens, self.temperature = int(role_config.get("max_tokens", 8000)), float(role_config.get("temperature", 0))
        self.system, self.tools = system, {tool.name: tool for tool in tools}
        self.submit_name = submit_name
        self.submit = Tool(
            submit_name,
            "Submit your final result. Call this exactly once, when you are done.",
            submit_schema,
            acknowledge_submission,
        )
        self.messages: list[JsonObject] = []
        self.usage: JsonObject = {"input_tokens": 0, "output_tokens": 0, "turns": 0, "temperature": self.temperature}
        self.transcript = LOGS / f"{role}_transcript.jsonl"
        self.bedrock_client = client()

    def log_record(self, record: JsonObject) -> None:
        """Append one record to this role's transcript."""
        with self.transcript.open("a") as transcript_file:
            transcript_file.write(json.dumps(record, default=str) + "\n")

    def converse(self) -> JsonObject:
        """Call the model. Send the temperature only if the model accepts it."""
        from botocore.exceptions import ClientError  # noqa: PLC0415 - boto3 is imported only where a model is called

        inference_config: JsonObject = {"maxTokens": self.max_tokens}
        if self.usage["temperature"] is not None:
            inference_config["temperature"] = self.usage["temperature"]
        try:
            response: JsonObject = self.bedrock_client.converse(
                modelId=self.model,
                system=[{"text": self.system}],
                messages=self.messages,
                toolConfig={"tools": [tool.spec() for tool in [*self.tools.values(), self.submit]]},
                inferenceConfig=inference_config,
            )
        except ClientError as error:
            # Some models (for example Claude Haiku 5.5) reject the temperature parameter. Then use the
            # model's default, and record it: usage['temperature'] is None in the stage output.
            if "temperature" in inference_config and "temperature" in str(error):
                self.usage["temperature"] = None
                return self.converse()
            raise
        return response

    def run_tool_call(self, call: Mapping[str, Any]) -> str:
        """Run one tool call that is not the submit call. The model gets an error as text and can try again."""
        if call["name"] not in self.tools:
            return f"[error] unknown tool {call['name']}"
        try:
            return clip_tool_output(self.tools[call["name"]].implementation(call["input"]))
        except Exception as error:  # noqa: BLE001 - the model gets the error and can try again
            return f"[error] {error}"

    def ask(self, text: str) -> JsonObject:
        """Send a user message. Run tool calls until the model calls the submit tool. Return its input."""
        self.messages.append({"role": "user", "content": [{"text": text}]})
        self.log_record({"user": text})
        for turn in range(self.max_turns):
            submitted = self.run_one_turn(turn)
            if submitted is not None:
                self.log_record({"submitted": submitted})
                return submitted
        raise RuntimeError(f"{self.role}: no {self.submit_name} call after {self.max_turns} turns")

    def run_one_turn(self, turn: int) -> JsonObject | None:
        """One model turn: call the model, run its tool calls, and return the submit input if it submitted."""
        response = self.converse()
        self.usage["input_tokens"] += response["usage"]["inputTokens"]
        self.usage["output_tokens"] += response["usage"]["outputTokens"]
        self.usage["turns"] += 1
        message = response["output"]["message"]
        self.messages.append(message)
        self.log_record({"assistant": message["content"]})
        calls = [content["toolUse"] for content in message["content"] if "toolUse" in content]
        if not calls:
            self.messages.append(
                {"role": "user", "content": [{"text": f"Use the tools. When you are done, call {self.submit_name}."}]}
            )
            return None
        results: list[JsonObject] = []
        submitted: JsonObject | None = None
        for call in calls:
            if call["name"] == self.submit_name:
                submitted = call["input"]
                output = "submitted"
            else:
                output = self.run_tool_call(call)
            print(f"  [{self.role}] {call['name']} {json.dumps(call['input'])[:120]}", file=sys.stderr)
            results.append({"toolResult": {"toolUseId": call["toolUseId"], "content": [{"text": output}]}})
        if turn >= self.max_turns - 3:
            results.append({"text": f"You have {self.max_turns - turn - 1} turns left. Call {self.submit_name} now."})
        self.messages.append({"role": "user", "content": results})
        return submitted


def write_json(name: str, data: Any) -> None:  # noqa: ANN401 - each stage writes its own JSON shape
    """Write one stage file under LOGS."""
    (LOGS / name).write_text(json.dumps(data, indent=2, default=str))


def git(*arguments: str, check: bool = True) -> str:
    """Run git in the repository and return its standard output."""
    return subprocess.run(  # nosec B607 - git from the verifier image's PATH; argv-only, shell=False
        ["git", *arguments], cwd=REPO, capture_output=True, text=True, check=check
    ).stdout
