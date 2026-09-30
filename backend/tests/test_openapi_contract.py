"""Structural guarantees about the generated OpenAPI contract (what the frontend and API clients rely on)."""


async def test_no_read_only_operation_takes_a_request_body(client):
    """A bare `list[str]` parameter on a GET is read by FastAPI as a JSON *body*, so `?tags=a&tags=b` is silently
    ignored (this hid a dead tag filter on GET /files). Query lists must be declared with Query()."""
    spec = (await client.get("/openapi.json")).json()

    offenders = sorted(f"{method.upper()} {path}" for path, ops in spec["paths"].items()
                       for method, op in ops.items()
                       if method in ("get", "head", "delete") and "requestBody" in op)

    assert offenders == []


async def test_files_list_declares_tags_as_a_repeatable_query_parameter(client):
    spec = (await client.get("/openapi.json")).json()

    params = {p["name"]: p for p in spec["paths"]["/api/v1/files"]["get"]["parameters"]}

    assert params["tags"]["in"] == "query"
    assert "array" in str(params["tags"]["schema"])  # ?tags=a&tags=b
