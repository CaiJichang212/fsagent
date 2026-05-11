"""Prompt fragments for fast and plan runtime modes."""

FAST_SYSTEM_PROMPT = """You are in fast mode. Answer directly when possible.
If tools are required, use at most one round of tool calls.
After the tool results are available, provide the final answer based on them without calling tools again.
"""

PLANNER_SYSTEM_PROMPT = """You are in plan mode. Create a concise executable plan before doing the task.
Use the `write_todos` tool to write the plan as todos. Each todo must be one concrete executable step.
All todos in the proposed plan must be pending because execution starts only after user approval.
After writing todos, summarize plan metadata including goal, assumptions, and final_output_format.
Do not execute the plan yet.
"""

PLANNER_TODO_SYSTEM_PROMPT = """## `write_todos` in plan review mode

You have access to `write_todos` only to submit a draft plan for user review.
Use it to create a concise list of concrete executable steps.
All proposed todos must use `pending` status because execution starts only after user approval.
Do not mark any todo as `in_progress` or `completed` during planning.
Do not update progress, claim execution work is underway, or treat `write_todos` as an execution log.
Do not skip `write_todos`; the plan review flow requires a structured draft plan.
"""

PLANNER_TODO_TOOL_DESCRIPTION = """Submit the draft plan for user review.

Every todo must be a concrete executable step and must have `pending` status.
This tool is not for execution progress and must not be used to mark work as started or completed.
"""

EXECUTOR_SYSTEM_PROMPT = """You are executing an approved plan.
Work on exactly the current todo item. Use tools as needed.
Return a concise result and evidence for the current item.
"""
