import subprocess
import sys


def test_api_service_import_does_not_emit_langchain_allowed_objects_warning():
    result = subprocess.run(
        [sys.executable, "-W", "default", "-c", "import fsagent.api.service"],
        check=False,
        capture_output=True,
        text=True,
    )

    assert result.returncode == 0
    assert "The default value of `allowed_objects` will change" not in result.stderr
