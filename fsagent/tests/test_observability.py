import json
import logging
from io import StringIO

from fsagent.observability import JsonLogFormatter, RunLogContext, RunLogger


def test_run_logger_writes_structured_events_with_session_context():
    stream = StringIO()
    handler = logging.StreamHandler(stream)
    handler.setFormatter(JsonLogFormatter())
    logger = logging.getLogger("fsagent.tests.observability.structured")
    logger.handlers = [handler]
    logger.propagate = False
    logger.setLevel(logging.INFO)
    run_logger = RunLogger(
        logger=logger,
        context=RunLogContext(
            session_id="session-1",
            thread_id="thread-1",
            mode="plan",
            model="fake:model",
        ),
    )

    assert run_logger.event("planner.started", "开始生成计划", todo_count=0)

    payload = json.loads(stream.getvalue())
    assert payload["level"] == "INFO"
    assert payload["event"] == "planner.started"
    assert payload["message"] == "开始生成计划"
    assert payload["session_id"] == "session-1"
    assert payload["thread_id"] == "thread-1"
    assert payload["mode"] == "plan"
    assert payload["model"] == "fake:model"
    assert payload["todo_count"] == 0


def test_run_logger_suppresses_duplicate_events():
    stream = StringIO()
    handler = logging.StreamHandler(stream)
    handler.setFormatter(JsonLogFormatter())
    logger = logging.getLogger("fsagent.tests.observability.dedupe")
    logger.handlers = [handler]
    logger.propagate = False
    logger.setLevel(logging.INFO)
    run_logger = RunLogger(
        logger=logger,
        context=RunLogContext(
            session_id="session-1",
            thread_id="thread-1",
            mode="plan",
            model="fake:model",
        ),
    )

    assert run_logger.event("planner.started", "开始生成计划", todo_count=0)
    assert not run_logger.event("planner.started", "开始生成计划", todo_count=0)
    assert run_logger.event("planner.started", "开始生成计划", todo_count=1)

    assert len(stream.getvalue().strip().splitlines()) == 2
