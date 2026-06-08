import inspect
from app.api.routes import jobs


def test_get_job_details_serializes_tool_index():
    src = inspect.getsource(jobs.get_job_details)
    assert '"tool_index"' in src


def test_update_job_configs_persists_tool_index():
    src = inspect.getsource(jobs.update_job_configs)
    assert "tool_index=cfg.tool_index" in src
