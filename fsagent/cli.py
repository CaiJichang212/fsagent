"""Command line entry point for the fast/plan runtime."""

import argparse
import asyncio
import os
import sys
import warnings
from collections.abc import Mapping
from typing import NoReturn

warnings.simplefilter("ignore", Warning)
warnings.showwarning = lambda *args, **kwargs: None  # noqa: ARG005  # keep CLI output focused on the agent response

from fsagent.runtime.model_config import FsAgentEnv, build_chat_qwen  # noqa: E402
from fsagent.slash_router import parse_slash_mode  # noqa: E402


def main() -> None:
    """Run the `fsagent` CLI."""
    parser = argparse.ArgumentParser(prog="fsagent")
    parser.add_argument("message", help="Request starting with /fast or /plan.")
    parser.add_argument("--model", default=None, help="Override the MODEL value from `.env`.")
    parser.add_argument("--env-file", default=None, help="Path to the `.env` file.")
    parser.add_argument("--mcp-config", default=None, help="Path to MCP config JSON.")
    parser.add_argument("--no-mcp", action="store_true", help="Disable MCP tool loading.")
    parser.add_argument(
        "--trust-project-mcp",
        action="store_true",
        help="Allow stdio MCP servers from the project MCP config to run.",
    )
    args = parser.parse_args()

    env = FsAgentEnv.from_sources(env_path=args.env_file, environ=os.environ)
    if args.model:
        env = FsAgentEnv(
            model=args.model,
            base_url=env.base_url,
            api_key=env.api_key,
            available_models_json=env.available_models_json,
            env_dir=env.env_dir,
        )
    if not env.api_key:
        _die("Set API_KEY in `.env` or the process environment.")

    routed = parse_slash_mode(args.message)
    from fsagent.runtime.graph import create_runtime  # noqa: I001, PLC0415  # keep `fsagent --help` lightweight
    from langchain_core.messages import HumanMessage  # noqa: PLC0415  # keep `fsagent --help` lightweight

    runtime = create_runtime(
        model=build_chat_qwen(env),
        mcp_config_path=args.mcp_config,
        no_mcp=args.no_mcp,
        trust_project_mcp=args.trust_project_mcp,
    )
    try:
        result = asyncio.run(runtime.ainvoke({"mode": routed.mode, "messages": [HumanMessage(content=routed.content)]}))
    except (FileNotFoundError, TypeError) as exc:
        message = format_runtime_error(exc)
        if message is None:
            raise
        _die(message)
    output = format_cli_output(result)
    if output:
        sys.stdout.write(f"{output}\n")


def format_cli_output(result: Mapping[str, object]) -> str | None:
    """Format runtime output for the non-interactive CLI.

    Args:
        result: Runtime invocation result.

    Returns:
        Text to print to stdout, or `None` when there is no displayable output.
    """
    final = result.get("final_response")
    if final:
        return str(final)

    interrupt = _first_interrupt_value(result.get("__interrupt__"))
    if isinstance(interrupt, Mapping) and interrupt.get("kind") == "plan_review":
        return _format_plan_review_interrupt(interrupt)
    return None


def format_runtime_error(error: Exception) -> str | None:
    """Format expected runtime errors as actionable CLI messages.

    Args:
        error: Runtime exception raised while invoking the graph.

    Returns:
        User-facing message for known errors, otherwise `None`.
    """
    text = str(error)
    if isinstance(error, FileNotFoundError):
        return text
    if isinstance(error, TypeError) and "null value for 'choices'" in text:
        return (
            "Model provider returned an invalid OpenAI-compatible response: `choices` was null. "
            "Check MODEL, BASE_URL, and API_KEY, or try another model with `--model`."
        )
    return None


def _first_interrupt_value(value: object) -> object | None:
    interrupt = value
    if isinstance(value, list | tuple):
        if not value:
            return None
        interrupt = value[0]
    return getattr(interrupt, "value", interrupt)


def _format_plan_review_interrupt(payload: Mapping[str, object]) -> str:
    lines = ["Plan review required."]
    meta = payload.get("plan_meta")
    if isinstance(meta, Mapping):
        goal = meta.get("goal")
        if goal:
            lines.extend(["", f"Goal: {goal}"])

    todos = payload.get("todos")
    if isinstance(todos, list) and todos:
        lines.extend(["", "Todos:"])
        for index, todo in enumerate(todos, start=1):
            if not isinstance(todo, Mapping):
                continue
            content = todo.get("content")
            status = todo.get("status", "pending")
            lines.append(f"{index}. [{status}] {content}")

    lines.extend(["", "Resume support is not implemented in this CLI yet."])
    return "\n".join(lines)


def _die(message: str) -> NoReturn:
    raise SystemExit(message)
