import json

import httpx
from fastapi.testclient import TestClient

import main


client = TestClient(main.app)


def test_health_returns_ok():
    response = client.get("/health")

    assert response.status_code == 200
    assert response.json() == {"status": "ok"}


def test_create_todo_requires_monday_api_token(monkeypatch):
    monkeypatch.delenv("TIMMENY_OS_API_KEY", raising=False)
    monkeypatch.delenv("MONDAY_API_TOKEN", raising=False)
    monkeypatch.setenv("TODO_BOARD_ID", "8962223984")

    response = client.post("/todos", json={"title": "TEST - Railway"})

    assert response.status_code == 500
    assert response.json() == {
        "detail": "MONDAY_API_TOKEN environment variable is not configured."
    }


def test_create_todo_requires_board_id(monkeypatch):
    monkeypatch.delenv("TIMMENY_OS_API_KEY", raising=False)
    monkeypatch.setenv("MONDAY_API_TOKEN", "test-token")
    monkeypatch.delenv("TODO_BOARD_ID", raising=False)

    response = client.post("/todos", json={"title": "TEST - Railway"})

    assert response.status_code == 500
    assert response.json() == {
        "detail": "TODO_BOARD_ID environment variable is not configured."
    }


def test_create_todo_creates_monday_item(monkeypatch):
    requests = []

    class FakeAsyncClient:
        def __init__(self, timeout):
            self.timeout = timeout

        async def __aenter__(self):
            return self

        async def __aexit__(self, exc_type, exc, traceback):
            return False

        async def post(self, url, json, headers):
            requests.append({"url": url, "json": json, "headers": headers})
            request = httpx.Request("POST", url)
            return httpx.Response(
                200,
                json={"data": {"create_item": {"id": "12331184429"}}},
                request=request,
            )

    monkeypatch.setenv("MONDAY_API_TOKEN", "test-token")
    monkeypatch.delenv("TIMMENY_OS_API_KEY", raising=False)
    monkeypatch.setenv("TODO_BOARD_ID", "8962223984")
    monkeypatch.setattr(main.httpx, "AsyncClient", FakeAsyncClient)

    response = client.post("/todos", json={"title": " TEST - Railway Deploy "})

    assert response.status_code == 200
    assert response.json() == {
        "success": True,
        "item_id": "12331184429",
        "title": "TEST - Railway Deploy",
        "list": "todo",
        "action_group": None,
        "action_date": None,
        "action": None,
        "annual_objective": None,
        "initiative_project": None,
    }
    assert requests == [
        {
            "url": main.MONDAY_API_URL,
            "json": {
                "query": """
    mutation CreateTodo($board_id: ID!, $group_id: String, $item_name: String!, $column_values: JSON) {
      create_item(board_id: $board_id, group_id: $group_id, item_name: $item_name, column_values: $column_values) {
        id
      }
    }
    """,
                "variables": {
                    "board_id": "8962223984",
                    "group_id": None,
                    "item_name": "TEST - Railway Deploy",
                    "column_values": None,
                },
            },
            "headers": {
                "Authorization": "test-token",
                "Content-Type": "application/json",
            },
        }
    ]


def test_create_todo_handles_monday_graphql_errors(monkeypatch):
    class FakeAsyncClient:
        def __init__(self, timeout):
            self.timeout = timeout

        async def __aenter__(self):
            return self

        async def __aexit__(self, exc_type, exc, traceback):
            return False

        async def post(self, url, json, headers):
            request = httpx.Request("POST", url)
            return httpx.Response(
                200,
                json={"errors": [{"message": "Board not found"}]},
                request=request,
            )

    monkeypatch.setenv("MONDAY_API_TOKEN", "test-token")
    monkeypatch.delenv("TIMMENY_OS_API_KEY", raising=False)
    monkeypatch.setenv("TODO_BOARD_ID", "bad-board")
    monkeypatch.setattr(main.httpx, "AsyncClient", FakeAsyncClient)

    response = client.post("/todos", json={"title": "TEST - Railway"})

    assert response.status_code == 502
    assert response.json() == {
        "detail": {
            "message": "Monday.com GraphQL request failed.",
            "errors": [{"message": "Board not found"}],
        }
    }


def test_create_todo_can_target_gs_list(monkeypatch):
    requests = []

    class FakeAsyncClient:
        def __init__(self, timeout):
            self.timeout = timeout

        async def __aenter__(self):
            return self

        async def __aexit__(self, exc_type, exc, traceback):
            return False

        async def post(self, url, json, headers):
            requests.append(json)
            request = httpx.Request("POST", url)
            return httpx.Response(
                200,
                json={"data": {"create_item": {"id": "12331184430"}}},
                request=request,
            )

    monkeypatch.setenv("MONDAY_API_TOKEN", "test-token")
    monkeypatch.delenv("TIMMENY_OS_API_KEY", raising=False)
    monkeypatch.setenv("GS_TODO_BOARD_ID", "111222333")
    monkeypatch.setenv("GS_TODO_GROUP_ID", "topics")
    monkeypatch.setattr(main.httpx, "AsyncClient", FakeAsyncClient)

    response = client.post(
        "/todos",
        json={"title": "GS follow-up", "list": "gs"},
    )

    assert response.status_code == 200
    assert response.json() == {
        "success": True,
        "item_id": "12331184430",
        "title": "GS follow-up",
        "list": "gs",
        "action_group": None,
        "action_date": None,
        "action": None,
        "annual_objective": None,
        "initiative_project": None,
    }
    assert requests[0]["variables"] == {
        "board_id": "111222333",
        "group_id": "topics",
        "item_name": "GS follow-up",
        "column_values": None,
    }


def test_create_todo_requires_gs_board_id(monkeypatch):
    monkeypatch.setenv("MONDAY_API_TOKEN", "test-token")
    monkeypatch.delenv("TIMMENY_OS_API_KEY", raising=False)
    monkeypatch.delenv("GS_TODO_BOARD_ID", raising=False)

    response = client.post(
        "/todos",
        json={"title": "GS follow-up", "list": "gs"},
    )

    assert response.status_code == 500
    assert response.json() == {
        "detail": "GS_TODO_BOARD_ID environment variable is not configured."
    }


def test_create_todo_accepts_configured_api_key(monkeypatch):
    requests = []

    class FakeAsyncClient:
        def __init__(self, timeout):
            self.timeout = timeout

        async def __aenter__(self):
            return self

        async def __aexit__(self, exc_type, exc, traceback):
            return False

        async def post(self, url, json, headers):
            requests.append(json)
            request = httpx.Request("POST", url)
            return httpx.Response(
                200,
                json={"data": {"create_item": {"id": "12331184432"}}},
                request=request,
            )

    monkeypatch.setenv("TIMMENY_OS_API_KEY", "app-key")
    monkeypatch.setenv("MONDAY_API_TOKEN", "test-token")
    monkeypatch.setenv("TODO_BOARD_ID", "8962223984")
    monkeypatch.setattr(main.httpx, "AsyncClient", FakeAsyncClient)

    response = client.post(
        "/todos",
        json={"title": "Protected todo"},
        headers={"X-API-Key": "app-key"},
    )

    assert response.status_code == 200
    assert requests[0]["variables"]["item_name"] == "Protected todo"


def test_create_todo_accepts_bearer_api_key(monkeypatch):
    requests = []

    class FakeAsyncClient:
        def __init__(self, timeout):
            self.timeout = timeout

        async def __aenter__(self):
            return self

        async def __aexit__(self, exc_type, exc, traceback):
            return False

        async def post(self, url, json, headers):
            requests.append(json)
            request = httpx.Request("POST", url)
            return httpx.Response(
                200,
                json={"data": {"create_item": {"id": "12331184433"}}},
                request=request,
            )

    monkeypatch.setenv("TIMMENY_OS_API_KEY", "app-key")
    monkeypatch.setenv("MONDAY_API_TOKEN", "test-token")
    monkeypatch.setenv("TODO_BOARD_ID", "8962223984")
    monkeypatch.setattr(main.httpx, "AsyncClient", FakeAsyncClient)

    response = client.post(
        "/todos",
        json={"title": "Bearer protected todo"},
        headers={"Authorization": "Bearer app-key"},
    )

    assert response.status_code == 200
    assert requests[0]["variables"]["item_name"] == "Bearer protected todo"


def test_create_todo_rejects_missing_api_key_when_configured(monkeypatch):
    monkeypatch.setenv("TIMMENY_OS_API_KEY", "app-key")
    monkeypatch.setenv("MONDAY_API_TOKEN", "test-token")
    monkeypatch.setenv("TODO_BOARD_ID", "8962223984")

    response = client.post("/todos", json={"title": "Protected todo"})

    assert response.status_code == 401
    assert response.json() == {"detail": "Invalid or missing API key."}


def test_create_todo_can_set_action_metadata(monkeypatch):
    requests = []

    class FakeAsyncClient:
        def __init__(self, timeout):
            self.timeout = timeout

        async def __aenter__(self):
            return self

        async def __aexit__(self, exc_type, exc, traceback):
            return False

        async def post(self, url, json, headers):
            requests.append(json)
            request = httpx.Request("POST", url)
            if "GetBoardColumns" in json["query"]:
                return httpx.Response(
                    200,
                    json={
                        "data": {
                            "boards": [
                                {
                                    "columns": [
                                        {
                                            "id": "text_mkp",
                                            "title": "Action Group",
                                            "type": "text",
                                        },
                                        {
                                            "id": "date_mkp",
                                            "title": "Action Date",
                                            "type": "date",
                                        },
                                        {
                                            "id": "dropdown_mkp",
                                            "title": "Action",
                                            "type": "dropdown",
                                        },
                                        {
                                            "id": "status_mkp",
                                            "title": "Status",
                                            "type": "status",
                                        },
                                    ]
                                }
                            ]
                        }
                    },
                    request=request,
                )
            return httpx.Response(
                200,
                json={"data": {"create_item": {"id": "12331184434"}}},
                request=request,
            )

    monkeypatch.setenv("MONDAY_API_TOKEN", "test-token")
    monkeypatch.delenv("TIMMENY_OS_API_KEY", raising=False)
    monkeypatch.setenv("TODO_BOARD_ID", "todo-board")
    monkeypatch.setattr(main.httpx, "AsyncClient", FakeAsyncClient)

    response = client.post(
        "/todos",
        json={
            "title": "Clarify launch owner",
            "list": "todo",
            "action_group": "Launch",
            "action_date": "2026-06-21",
            "action": "Decision",
        },
    )

    assert response.status_code == 200
    assert response.json() == {
        "success": True,
        "item_id": "12331184434",
        "title": "Clarify launch owner",
        "list": "todo",
        "action_group": "Launch",
        "action_date": "2026-06-21",
        "action": "Decision",
        "annual_objective": None,
        "initiative_project": None,
    }
    assert requests[0]["variables"] == {"board_id": "todo-board"}
    assert json.loads(requests[1]["variables"]["column_values"]) == {
        "text_mkp": "Launch",
        "date_mkp": {"date": "2026-06-21"},
        "dropdown_mkp": {"labels": ["Decision"]},
        "status_mkp": {"label": "Not Yet Started"},
    }


def test_update_todo_action_metadata(monkeypatch):
    requests = []

    class FakeAsyncClient:
        def __init__(self, timeout):
            self.timeout = timeout

        async def __aenter__(self):
            return self

        async def __aexit__(self, exc_type, exc, traceback):
            return False

        async def post(self, url, json, headers):
            requests.append(json)
            request = httpx.Request("POST", url)
            if "GetBoardColumns" in json["query"]:
                return httpx.Response(
                    200,
                    json={
                        "data": {
                            "boards": [
                                {
                                    "columns": [
                                        {
                                            "id": "text_mkp",
                                            "title": "Action Group",
                                            "type": "text",
                                        },
                                        {
                                            "id": "date_mkp",
                                            "title": "Action Date",
                                            "type": "date",
                                        },
                                        {
                                            "id": "dropdown_mkp",
                                            "title": "Action",
                                            "type": "dropdown",
                                        },
                                        {
                                            "id": "status_mkp",
                                            "title": "Status",
                                            "type": "status",
                                        },
                                    ]
                                }
                            ]
                        }
                    },
                    request=request,
                )
            return httpx.Response(
                200,
                json={"data": {"change_multiple_column_values": {"id": "existing-1"}}},
                request=request,
            )

    monkeypatch.setenv("MONDAY_API_TOKEN", "test-token")
    monkeypatch.delenv("TIMMENY_OS_API_KEY", raising=False)
    monkeypatch.setenv("GS_TODO_BOARD_ID", "gs-board")
    monkeypatch.setattr(main.httpx, "AsyncClient", FakeAsyncClient)

    response = client.patch(
        "/todos/existing-1/action-metadata",
        json={
            "list": "gs",
            "action_group": "Partnerships",
            "action_date": "2026-06-21",
            "action": "Decision",
        },
    )

    assert response.status_code == 200
    assert response.json() == {
        "success": True,
        "item_id": "existing-1",
        "list": "gs",
        "action_group": "Partnerships",
        "action_date": "2026-06-21",
        "action": "Decision",
        "annual_objective": None,
        "initiative_project": None,
    }
    assert requests[1]["variables"]["board_id"] == "gs-board"
    assert requests[1]["variables"]["item_id"] == "existing-1"
    assert json.loads(requests[1]["variables"]["column_values"]) == {
        "text_mkp": "Partnerships",
        "date_mkp": {"date": "2026-06-21"},
        "dropdown_mkp": {"labels": ["Decision"]},
        "status_mkp": {"label": "Not Yet Started"},
    }


def test_update_todo_action_metadata_supports_status_action_column(monkeypatch):
    requests = []

    class FakeAsyncClient:
        def __init__(self, timeout):
            self.timeout = timeout

        async def __aenter__(self):
            return self

        async def __aexit__(self, exc_type, exc, traceback):
            return False

        async def post(self, url, json, headers):
            requests.append(json)
            request = httpx.Request("POST", url)
            if "GetBoardColumns" in json["query"]:
                return httpx.Response(
                    200,
                    json={
                        "data": {
                            "boards": [
                                {
                                    "columns": [
                                        {
                                            "id": "status_mkp",
                                            "title": "Action",
                                            "type": "status",
                                        },
                                        {
                                            "id": "todo_status_mkp",
                                            "title": "Status",
                                            "type": "status",
                                        },
                                    ]
                                }
                            ]
                        }
                    },
                    request=request,
                )
            return httpx.Response(
                200,
                json={"data": {"change_multiple_column_values": {"id": "existing-1"}}},
                request=request,
            )

    monkeypatch.setenv("MONDAY_API_TOKEN", "test-token")
    monkeypatch.delenv("TIMMENY_OS_API_KEY", raising=False)
    monkeypatch.setenv("TODO_BOARD_ID", "todo-board")
    monkeypatch.setattr(main.httpx, "AsyncClient", FakeAsyncClient)

    response = client.patch(
        "/todos/existing-1/action-metadata",
        json={"list": "todo", "action": "Decision"},
    )

    assert response.status_code == 200
    assert json.loads(requests[1]["variables"]["column_values"]) == {
        "status_mkp": {"label": "Decision"},
        "todo_status_mkp": {"label": "Not Yet Started"},
    }


def test_update_todo_action_metadata_only_sets_status_for_decisions(monkeypatch):
    requests = []

    class FakeAsyncClient:
        def __init__(self, timeout):
            self.timeout = timeout

        async def __aenter__(self):
            return self

        async def __aexit__(self, exc_type, exc, traceback):
            return False

        async def post(self, url, json, headers):
            requests.append(json)
            request = httpx.Request("POST", url)
            if "GetBoardColumns" in json["query"]:
                return httpx.Response(
                    200,
                    json={
                        "data": {
                            "boards": [
                                {
                                    "columns": [
                                        {
                                            "id": "action_mkp",
                                            "title": "Action",
                                            "type": "status",
                                        },
                                        {
                                            "id": "status_mkp",
                                            "title": "Status",
                                            "type": "status",
                                        },
                                    ]
                                }
                            ]
                        }
                    },
                    request=request,
                )
            return httpx.Response(
                200,
                json={"data": {"change_multiple_column_values": {"id": "existing-1"}}},
                request=request,
            )

    monkeypatch.setenv("MONDAY_API_TOKEN", "test-token")
    monkeypatch.delenv("TIMMENY_OS_API_KEY", raising=False)
    monkeypatch.setenv("TODO_BOARD_ID", "todo-board")
    monkeypatch.setattr(main.httpx, "AsyncClient", FakeAsyncClient)

    response = client.patch(
        "/todos/existing-1/action-metadata",
        json={"list": "todo", "action": "Follow Up"},
    )

    assert response.status_code == 200
    assert json.loads(requests[1]["variables"]["column_values"]) == {
        "action_mkp": {"label": "Follow Up"},
    }


def test_update_todo_action_metadata_requires_known_column(monkeypatch):
    class FakeAsyncClient:
        def __init__(self, timeout):
            self.timeout = timeout

        async def __aenter__(self):
            return self

        async def __aexit__(self, exc_type, exc, traceback):
            return False

        async def post(self, url, json, headers):
            request = httpx.Request("POST", url)
            return httpx.Response(
                200,
                json={
                    "data": {
                        "boards": [
                            {
                                "columns": [
                                    {
                                        "id": "text_mkp",
                                        "title": "Action Group",
                                        "type": "text",
                                    }
                                ]
                            }
                        ]
                    }
                },
                request=request,
            )

    monkeypatch.setenv("MONDAY_API_TOKEN", "test-token")
    monkeypatch.delenv("TIMMENY_OS_API_KEY", raising=False)
    monkeypatch.setenv("TODO_BOARD_ID", "todo-board")
    monkeypatch.setattr(main.httpx, "AsyncClient", FakeAsyncClient)

    response = client.patch(
        "/todos/existing-1/action-metadata",
        json={"list": "todo", "action": "Decision"},
    )

    assert response.status_code == 502
    assert response.json() == {
        "detail": 'Monday.com board is missing the "Action" column.'
    }


def test_bulk_update_todo_action_metadata_updates_multiple_boards(monkeypatch):
    requests = []

    class FakeAsyncClient:
        def __init__(self, timeout):
            self.timeout = timeout

        async def __aenter__(self):
            return self

        async def __aexit__(self, exc_type, exc, traceback):
            return False

        async def post(self, url, json, headers):
            requests.append(json)
            request = httpx.Request("POST", url)
            if "GetBoardColumns" in json["query"]:
                return httpx.Response(
                    200,
                    json={
                        "data": {
                            "boards": [
                                {
                                    "columns": [
                                        {
                                            "id": "text_mkp",
                                            "title": "Action Group",
                                            "type": "text",
                                        },
                                    ]
                                }
                            ]
                        }
                    },
                    request=request,
                )
            return httpx.Response(
                200,
                json={
                    "data": {
                        "change_multiple_column_values": {
                            "id": json["variables"]["item_id"]
                        }
                    }
                },
                request=request,
            )

    monkeypatch.setenv("MONDAY_API_TOKEN", "test-token")
    monkeypatch.delenv("TIMMENY_OS_API_KEY", raising=False)
    monkeypatch.setenv("TODO_BOARD_ID", "todo-board")
    monkeypatch.setenv("GS_TODO_BOARD_ID", "gs-board")
    monkeypatch.setattr(main.httpx, "AsyncClient", FakeAsyncClient)

    response = client.post(
        "/todos/bulk-action-metadata",
        json={
            "updates": [
                {
                    "item_id": "todo-1",
                    "list": "todo",
                    "action_group": "Launch",
                },
                {
                    "item_id": "gs-1",
                    "list": "gs",
                    "action_group": "Partnerships",
                },
            ]
        },
    )

    assert response.status_code == 200
    assert response.json() == {
        "success": True,
        "updated_count": 2,
        "failed_count": 0,
        "results": [
            {
                "success": True,
                "item_id": "todo-1",
                "list": "todo",
                "action_group": "Launch",
                "action_date": None,
                "action": None,
                "annual_objective": None,
                "initiative_project": None,
                "error": None,
            },
            {
                "success": True,
                "item_id": "gs-1",
                "list": "gs",
                "action_group": "Partnerships",
                "action_date": None,
                "action": None,
                "annual_objective": None,
                "initiative_project": None,
                "error": None,
            },
        ],
    }
    assert [request["variables"]["board_id"] for request in requests] == [
        "todo-board",
        "todo-board",
        "gs-board",
        "gs-board",
    ]
    assert json.loads(requests[1]["variables"]["column_values"]) == {
        "text_mkp": "Launch",
    }
    assert json.loads(requests[3]["variables"]["column_values"]) == {
        "text_mkp": "Partnerships",
    }


def test_bulk_update_todo_action_metadata_reports_partial_failures(monkeypatch):
    requests = []

    class FakeAsyncClient:
        def __init__(self, timeout):
            self.timeout = timeout

        async def __aenter__(self):
            return self

        async def __aexit__(self, exc_type, exc, traceback):
            return False

        async def post(self, url, json, headers):
            requests.append(json)
            request = httpx.Request("POST", url)
            if "GetBoardColumns" in json["query"]:
                board_id = json["variables"]["board_id"]
                columns_by_board = {
                    "todo-board": [
                        {
                            "id": "text_mkp",
                            "title": "Action Group",
                            "type": "text",
                        },
                    ],
                    "gs-board": [],
                }
                return httpx.Response(
                    200,
                    json={
                        "data": {
                            "boards": [
                                {
                                    "columns": columns_by_board[board_id],
                                }
                            ]
                        }
                    },
                    request=request,
                )
            return httpx.Response(
                200,
                json={
                    "data": {
                        "change_multiple_column_values": {
                            "id": json["variables"]["item_id"]
                        }
                    }
                },
                request=request,
            )

    monkeypatch.setenv("MONDAY_API_TOKEN", "test-token")
    monkeypatch.delenv("TIMMENY_OS_API_KEY", raising=False)
    monkeypatch.setenv("TODO_BOARD_ID", "todo-board")
    monkeypatch.setenv("GS_TODO_BOARD_ID", "gs-board")
    monkeypatch.setattr(main.httpx, "AsyncClient", FakeAsyncClient)

    response = client.post(
        "/todos/bulk-action-metadata",
        json={
            "updates": [
                {
                    "item_id": "todo-1",
                    "list": "todo",
                    "action_group": "Launch",
                },
                {
                    "item_id": "gs-1",
                    "list": "gs",
                    "action_group": "Partnerships",
                },
            ]
        },
    )

    assert response.status_code == 200
    assert response.json() == {
        "success": False,
        "updated_count": 1,
        "failed_count": 1,
        "results": [
            {
                "success": True,
                "item_id": "todo-1",
                "list": "todo",
                "action_group": "Launch",
                "action_date": None,
                "action": None,
                "annual_objective": None,
                "initiative_project": None,
                "error": None,
            },
            {
                "success": False,
                "item_id": "gs-1",
                "list": "gs",
                "action_group": "Partnerships",
                "action_date": None,
                "action": None,
                "annual_objective": None,
                "initiative_project": None,
                "error": 'Monday.com board is missing the "Action Group" column.',
            },
        ],
    }
    assert len(requests) == 3


def test_bulk_update_todo_action_metadata_updates_gs_planning_fields(monkeypatch):
    requests = []

    class FakeAsyncClient:
        def __init__(self, timeout):
            self.timeout = timeout

        async def __aenter__(self):
            return self

        async def __aexit__(self, exc_type, exc, traceback):
            return False

        async def post(self, url, json, headers):
            requests.append(json)
            request = httpx.Request("POST", url)
            if "GetBoardColumns" in json["query"]:
                return httpx.Response(
                    200,
                    json={
                        "data": {
                            "boards": [
                                {
                                    "columns": [
                                        {
                                            "id": "objective_mkp",
                                            "title": "Annual Objective",
                                            "type": "dropdown",
                                        },
                                        {
                                            "id": "initiative_mkp",
                                            "title": "Initiative",
                                            "type": "text",
                                        },
                                        {
                                            "id": "group_mkp",
                                            "title": "Action Group",
                                            "type": "text",
                                        },
                                    ]
                                }
                            ]
                        }
                    },
                    request=request,
                )
            return httpx.Response(
                200,
                json={
                    "data": {
                        "change_multiple_column_values": {
                            "id": json["variables"]["item_id"]
                        }
                    }
                },
                request=request,
            )

    monkeypatch.setenv("MONDAY_API_TOKEN", "test-token")
    monkeypatch.delenv("TIMMENY_OS_API_KEY", raising=False)
    monkeypatch.setenv("GS_TODO_BOARD_ID", "gs-board")
    monkeypatch.setattr(main.httpx, "AsyncClient", FakeAsyncClient)

    response = client.post(
        "/todos/bulk-action-metadata",
        json={
            "updates": [
                {
                    "item_id": "gs-1",
                    "list": "gs",
                    "annual_objective": "Grow Revenue",
                    "initiative_project": "Partner Pipeline",
                    "action_group": "Partnerships",
                }
            ]
        },
    )

    assert response.status_code == 200
    assert response.json()["results"][0] == {
        "success": True,
        "item_id": "gs-1",
        "list": "gs",
        "action_group": "Partnerships",
        "action_date": None,
        "action": None,
        "annual_objective": "Grow Revenue",
        "initiative_project": "Partner Pipeline",
        "error": None,
    }
    assert json.loads(requests[1]["variables"]["column_values"]) == {
        "objective_mkp": {"labels": ["Grow Revenue"]},
        "initiative_mkp": "Partner Pipeline",
        "group_mkp": "Partnerships",
    }


def test_list_todos_returns_items_from_both_boards(monkeypatch):
    requests = []

    class FakeAsyncClient:
        def __init__(self, timeout):
            self.timeout = timeout

        async def __aenter__(self):
            return self

        async def __aexit__(self, exc_type, exc, traceback):
            return False

        async def post(self, url, json, headers):
            requests.append(json)
            board_id = json["variables"]["board_id"]
            request = httpx.Request("POST", url)
            items_by_board = {
                "todo-board": [
                    {
                        "id": "1",
                        "name": "regular item",
                        "group": {"id": "todo-group", "title": "To Do"},
                        "column_values": [
                            {
                                "id": "text_mkp",
                                "text": "Launch",
                                "value": None,
                                "column": {"title": "Action Group"},
                            },
                            {
                                "id": "date_mkp",
                                "text": "2026-06-21",
                                "value": None,
                                "column": {"title": "Action Date"},
                            },
                            {
                                "id": "dropdown_mkp",
                                "text": "Decision",
                                "value": None,
                                "column": {"title": "Action"},
                            },
                        ],
                    }
                ],
                "gs-board": [
                    {
                        "id": "2",
                        "name": "gs item",
                        "group": {"id": "gs-group", "title": "Action Items"},
                        "column_values": [],
                    }
                ],
            }
            return httpx.Response(
                200,
                json={
                    "data": {
                        "boards": [
                            {
                                "items_page": {
                                    "items": items_by_board[board_id],
                                }
                            }
                        ]
                    }
                },
                request=request,
            )

    monkeypatch.setenv("MONDAY_API_TOKEN", "test-token")
    monkeypatch.delenv("TIMMENY_OS_API_KEY", raising=False)
    monkeypatch.setenv("TODO_BOARD_ID", "todo-board")
    monkeypatch.setenv("GS_TODO_BOARD_ID", "gs-board")
    monkeypatch.setattr(main.httpx, "AsyncClient", FakeAsyncClient)

    response = client.get("/todos")

    assert response.status_code == 200
    assert response.json() == {
        "success": True,
        "count": 2,
        "items": [
            {
                "item_id": "1",
                "title": "regular item",
                "list": "todo",
                "group_id": "todo-group",
                "group_title": "To Do",
                "action_group": "Launch",
                "action_date": "2026-06-21",
                "action": "Decision",
                "annual_objective": None,
                "initiative_project": None,
                "status": None,
                "owner": None,
                "due_date": None,
            },
            {
                "item_id": "2",
                "title": "gs item",
                "list": "gs",
                "group_id": "gs-group",
                "group_title": "Action Items",
                "action_group": None,
                "action_date": None,
                "action": None,
                "annual_objective": None,
                "initiative_project": None,
                "status": None,
                "owner": None,
                "due_date": None,
            },
        ],
    }
    assert [request["variables"]["board_id"] for request in requests] == [
        "todo-board",
        "gs-board",
    ]
    assert [request["variables"]["limit"] for request in requests] == [500, 500]


def test_list_todos_can_filter_to_gs(monkeypatch):
    requests = []

    class FakeAsyncClient:
        def __init__(self, timeout):
            self.timeout = timeout

        async def __aenter__(self):
            return self

        async def __aexit__(self, exc_type, exc, traceback):
            return False

        async def post(self, url, json, headers):
            requests.append(json)
            request = httpx.Request("POST", url)
            return httpx.Response(
                200,
                json={
                    "data": {
                        "boards": [
                            {
                                "items_page": {
                                    "items": [
                                        {
                                            "id": "2",
                                            "name": "gs item",
                                            "group": None,
                                            "column_values": [],
                                        }
                                    ],
                                }
                            }
                        ]
                    }
                },
                request=request,
            )

    monkeypatch.setenv("MONDAY_API_TOKEN", "test-token")
    monkeypatch.delenv("TIMMENY_OS_API_KEY", raising=False)
    monkeypatch.setenv("GS_TODO_BOARD_ID", "gs-board")
    monkeypatch.setattr(main.httpx, "AsyncClient", FakeAsyncClient)

    response = client.get("/todos?list=gs&limit=10")

    assert response.status_code == 200
    assert response.json() == {
        "success": True,
        "count": 1,
        "items": [
            {
                "item_id": "2",
                "title": "gs item",
                "list": "gs",
                "group_id": None,
                "group_title": None,
                "action_group": None,
                "action_date": None,
                "action": None,
                "annual_objective": None,
                "initiative_project": None,
                "status": None,
                "owner": None,
                "due_date": None,
            }
        ],
    }
    assert len(requests) == 1
    assert requests[0]["variables"] == {
        "board_id": "gs-board",
        "limit": 10,
    }


def test_list_todos_reads_next_monday_page(monkeypatch):
    requests = []

    class FakeAsyncClient:
        def __init__(self, timeout):
            self.timeout = timeout

        async def __aenter__(self):
            return self

        async def __aexit__(self, exc_type, exc, traceback):
            return False

        async def post(self, url, json, headers):
            requests.append(json)
            request = httpx.Request("POST", url)
            if "board_id" in json["variables"]:
                return httpx.Response(
                    200,
                    json={
                        "data": {
                            "boards": [
                                {
                                    "columns": [
                                        {"id": "status_mkp", "title": "Status"},
                                    ],
                                    "items_page": {
                                        "cursor": "next-page",
                                        "items": [
                                            {
                                                "id": "1",
                                                "name": "first item",
                                                "group": None,
                                                "column_values": [
                                                    {
                                                        "id": "status_mkp",
                                                        "text": "Not Yet Started",
                                                        "value": None,
                                                    }
                                                ],
                                            }
                                        ],
                                    },
                                }
                            ]
                        }
                    },
                    request=request,
                )
            return httpx.Response(
                200,
                json={
                    "data": {
                        "next_items_page": {
                            "cursor": None,
                            "items": [
                                {
                                    "id": "2",
                                    "name": "second item",
                                    "group": None,
                                    "column_values": [
                                        {
                                            "id": "status_mkp",
                                            "text": "Working on it",
                                            "value": None,
                                        }
                                    ],
                                }
                            ],
                        }
                    }
                },
                request=request,
            )

    monkeypatch.setenv("MONDAY_API_TOKEN", "test-token")
    monkeypatch.delenv("TIMMENY_OS_API_KEY", raising=False)
    monkeypatch.setenv("TODO_BOARD_ID", "todo-board")
    monkeypatch.setattr(main.httpx, "AsyncClient", FakeAsyncClient)

    response = client.get("/todos?list=todo&limit=2")

    assert response.status_code == 200
    assert response.json()["count"] == 2
    assert [item["item_id"] for item in response.json()["items"]] == ["1", "2"]
    assert requests[0]["variables"] == {"board_id": "todo-board", "limit": 2}
    assert requests[1]["variables"] == {"cursor": "next-page", "limit": 1}


def test_list_todos_returns_gs_planning_context(monkeypatch):
    class FakeAsyncClient:
        def __init__(self, timeout):
            self.timeout = timeout

        async def __aenter__(self):
            return self

        async def __aexit__(self, exc_type, exc, traceback):
            return False

        async def post(self, url, json, headers):
            request = httpx.Request("POST", url)
            return httpx.Response(
                200,
                json={
                    "data": {
                        "boards": [
                            {
                                "columns": [],
                                "items_page": {
                                    "items": [
                                        {
                                            "id": "gs-1",
                                            "name": "Follow up with partner",
                                            "group": None,
                                            "column_values": [
                                                {
                                                    "id": "status_mkp",
                                                    "text": "Not Yet Started",
                                                    "value": None,
                                                    "column": {"title": "Status"},
                                                },
                                                {
                                                    "id": "owner_mkp",
                                                    "text": "Ben",
                                                    "value": None,
                                                    "column": {"title": "Owner"},
                                                },
                                                {
                                                    "id": "due_mkp",
                                                    "text": "2026-07-10",
                                                    "value": None,
                                                    "column": {"title": "Due Date"},
                                                },
                                                {
                                                    "id": "objective_mkp",
                                                    "text": "Grow Revenue",
                                                    "value": None,
                                                    "column": {"title": "Annual Objective"},
                                                },
                                                {
                                                    "id": "initiative_mkp",
                                                    "text": "Partner Pipeline",
                                                    "value": None,
                                                    "column": {"title": "Initiative"},
                                                },
                                                {
                                                    "id": "group_mkp",
                                                    "text": "Partnerships",
                                                    "value": None,
                                                    "column": {"title": "Action Group"},
                                                },
                                            ],
                                        }
                                    ],
                                },
                            }
                        ]
                    }
                },
                request=request,
            )

    monkeypatch.setenv("MONDAY_API_TOKEN", "test-token")
    monkeypatch.delenv("TIMMENY_OS_API_KEY", raising=False)
    monkeypatch.setenv("GS_TODO_BOARD_ID", "gs-board")
    monkeypatch.setattr(main.httpx, "AsyncClient", FakeAsyncClient)

    response = client.get("/todos?list=gs")

    assert response.status_code == 200
    assert response.json()["items"][0] == {
        "item_id": "gs-1",
        "title": "Follow up with partner",
        "list": "gs",
        "group_id": None,
        "group_title": None,
        "action_group": "Partnerships",
        "action_date": None,
        "action": None,
        "annual_objective": "Grow Revenue",
        "initiative_project": "Partner Pipeline",
        "status": "Not Yet Started",
        "owner": "Ben",
        "due_date": "2026-07-10",
    }


def test_list_todos_excludes_done_items_by_default(monkeypatch):
    class FakeAsyncClient:
        def __init__(self, timeout):
            self.timeout = timeout

        async def __aenter__(self):
            return self

        async def __aexit__(self, exc_type, exc, traceback):
            return False

        async def post(self, url, json, headers):
            request = httpx.Request("POST", url)
            return httpx.Response(
                200,
                json={
                    "data": {
                        "boards": [
                            {
                                "columns": [
                                    {"id": "status_mkp", "title": "Status"},
                                ],
                                "items_page": {
                                    "items": [
                                        {
                                            "id": "1",
                                            "name": "open item",
                                            "group": None,
                                            "column_values": [
                                                {
                                                    "id": "status_mkp",
                                                    "text": "Not Yet Started",
                                                    "value": None,
                                                }
                                            ],
                                        },
                                        {
                                            "id": "2",
                                            "name": "done item",
                                            "group": None,
                                            "column_values": [
                                                {
                                                    "id": "status_mkp",
                                                    "text": "Done",
                                                    "value": None,
                                                }
                                            ],
                                        },
                                    ],
                                },
                            }
                        ]
                    }
                },
                request=request,
            )

    monkeypatch.setenv("MONDAY_API_TOKEN", "test-token")
    monkeypatch.delenv("TIMMENY_OS_API_KEY", raising=False)
    monkeypatch.setenv("TODO_BOARD_ID", "todo-board")
    monkeypatch.setattr(main.httpx, "AsyncClient", FakeAsyncClient)

    response = client.get("/todos?list=todo")

    assert response.status_code == 200
    assert response.json()["count"] == 1
    assert response.json()["items"][0]["item_id"] == "1"


def test_list_todos_can_include_done_items(monkeypatch):
    class FakeAsyncClient:
        def __init__(self, timeout):
            self.timeout = timeout

        async def __aenter__(self):
            return self

        async def __aexit__(self, exc_type, exc, traceback):
            return False

        async def post(self, url, json, headers):
            request = httpx.Request("POST", url)
            return httpx.Response(
                200,
                json={
                    "data": {
                        "boards": [
                            {
                                "columns": [
                                    {"id": "status_mkp", "title": "Status"},
                                ],
                                "items_page": {
                                    "items": [
                                        {
                                            "id": "1",
                                            "name": "open item",
                                            "group": None,
                                            "column_values": [
                                                {
                                                    "id": "status_mkp",
                                                    "text": "Not Yet Started",
                                                    "value": None,
                                                }
                                            ],
                                        },
                                        {
                                            "id": "2",
                                            "name": "done item",
                                            "group": None,
                                            "column_values": [
                                                {
                                                    "id": "status_mkp",
                                                    "text": "Done",
                                                    "value": None,
                                                }
                                            ],
                                        },
                                    ],
                                },
                            }
                        ]
                    }
                },
                request=request,
            )

    monkeypatch.setenv("MONDAY_API_TOKEN", "test-token")
    monkeypatch.delenv("TIMMENY_OS_API_KEY", raising=False)
    monkeypatch.setenv("TODO_BOARD_ID", "todo-board")
    monkeypatch.setattr(main.httpx, "AsyncClient", FakeAsyncClient)

    response = client.get("/todos?list=todo&include_done=true")

    assert response.status_code == 200
    assert response.json()["count"] == 2


def test_list_todos_requires_api_key_when_configured(monkeypatch):
    monkeypatch.setenv("TIMMENY_OS_API_KEY", "app-key")
    monkeypatch.setenv("MONDAY_API_TOKEN", "test-token")
    monkeypatch.setenv("TODO_BOARD_ID", "todo-board")

    response = client.get("/todos")

    assert response.status_code == 401
    assert response.json() == {"detail": "Invalid or missing API key."}


def test_get_todo_metadata_returns_configured_column_labels(monkeypatch):
    requests = []

    class FakeAsyncClient:
        def __init__(self, timeout):
            self.timeout = timeout

        async def __aenter__(self):
            return self

        async def __aexit__(self, exc_type, exc, traceback):
            return False

        async def post(self, url, json, headers):
            requests.append(json)
            request = httpx.Request("POST", url)
            if "GetTodoItems" in json["query"]:
                return httpx.Response(
                    200,
                    json={
                        "data": {
                            "boards": [
                                {
                                    "columns": [
                                        {"id": "objective_mkp", "title": "Annual Objective"},
                                        {"id": "initiative_mkp", "title": "Initiative"},
                                        {"id": "group_mkp", "title": "Action Group"},
                                    ],
                                    "items_page": {
                                        "cursor": None,
                                        "items": [
                                            {
                                                "id": "1",
                                                "name": "item",
                                                "group": None,
                                                "column_values": [
                                                    {
                                                        "id": "objective_mkp",
                                                        "text": "Developer Productivity",
                                                        "value": None,
                                                    },
                                                    {
                                                        "id": "initiative_mkp",
                                                        "text": "GSCode Platform",
                                                        "value": None,
                                                    },
                                                    {
                                                        "id": "group_mkp",
                                                        "text": "Platform",
                                                        "value": None,
                                                    },
                                                ],
                                            },
                                            {
                                                "id": "2",
                                                "name": "item",
                                                "group": None,
                                                "column_values": [
                                                    {
                                                        "id": "objective_mkp",
                                                        "text": "developer productivity",
                                                        "value": None,
                                                    },
                                                    {
                                                        "id": "initiative_mkp",
                                                        "text": "Evergreen Engineering",
                                                        "value": None,
                                                    },
                                                    {
                                                        "id": "group_mkp",
                                                        "text": "Risk",
                                                        "value": None,
                                                    },
                                                ],
                                            },
                                        ],
                                    },
                                }
                            ]
                        }
                    },
                    request=request,
                )
            return httpx.Response(
                200,
                json={
                    "data": {
                        "boards": [
                            {
                                "columns": [
                                    {
                                        "id": "objective_mkp",
                                        "title": "Annual Objective",
                                        "type": "dropdown",
                                        "settings_str": __import__("json").dumps(
                                            {
                                                "labels": [
                                                    {"id": 1, "name": "Developer Productivity"},
                                                    {"id": 2, "name": "Risk Reduction"},
                                                ]
                                            }
                                        ),
                                    },
                                    {
                                        "id": "initiative_mkp",
                                        "title": "Initiative",
                                        "type": "status",
                                        "settings_str": __import__("json").dumps(
                                            {
                                                "labels": {
                                                    "0": {"label": "Evergreen Engineering"},
                                                    "1": {"label": "GSCode Platform"},
                                                }
                                            }
                                        ),
                                    },
                                    {
                                        "id": "group_mkp",
                                        "title": "Action Group",
                                        "type": "text",
                                        "settings_str": "{}",
                                    },
                                    {
                                        "id": "ignored_mkp",
                                        "title": "Unrelated",
                                        "type": "text",
                                        "settings_str": "{}",
                                    },
                                ]
                            }
                        ]
                    }
                },
                request=request,
            )

    monkeypatch.setenv("MONDAY_API_TOKEN", "test-token")
    monkeypatch.delenv("TIMMENY_OS_API_KEY", raising=False)
    monkeypatch.setenv("GS_TODO_BOARD_ID", "gs-board")
    monkeypatch.setattr(main.httpx, "AsyncClient", FakeAsyncClient)

    response = client.get("/todos/metadata?list=gs")

    assert response.status_code == 200
    assert response.json() == {
        "success": True,
        "list": "gs",
        "columns": [
            {
                "id": "objective_mkp",
                "title": "Annual Objective",
                "type": "dropdown",
                "labels": ["Developer Productivity", "Risk Reduction"],
                "observed_values": ["Developer Productivity"],
            },
            {
                "id": "initiative_mkp",
                "title": "Initiative",
                "type": "status",
                "labels": ["Evergreen Engineering", "GSCode Platform"],
                "observed_values": ["GSCode Platform", "Evergreen Engineering"],
            },
            {
                "id": "group_mkp",
                "title": "Action Group",
                "type": "text",
                "labels": [],
                "observed_values": ["Platform", "Risk"],
            },
        ],
    }
    assert requests[0]["variables"] == {"board_id": "gs-board"}


def test_list_key_initiatives_returns_open_items(monkeypatch):
    requests = []

    class FakeAsyncClient:
        def __init__(self, timeout):
            self.timeout = timeout

        async def __aenter__(self):
            return self

        async def __aexit__(self, exc_type, exc, traceback):
            return False

        async def post(self, url, json, headers):
            requests.append(json)
            request = httpx.Request("POST", url)
            return httpx.Response(
                200,
                json={
                    "data": {
                        "boards": [
                            {
                                "columns": [
                                    {"id": "status_mkp", "title": "Status"},
                                    {"id": "owner_mkp", "title": "Owner"},
                                    {"id": "objective_mkp", "title": "Annual Objective"},
                                    {"id": "initiative_mkp", "title": "Initiative"},
                                ],
                                "items_page": {
                                    "cursor": None,
                                    "items": [
                                        {
                                            "id": "ki-1",
                                            "name": "Build partner pipeline",
                                            "group": {
                                                "id": "key-initiatives",
                                                "title": "Key Initiatives",
                                            },
                                            "column_values": [
                                                {
                                                    "id": "status_mkp",
                                                    "text": "Not Yet Started",
                                                    "value": None,
                                                },
                                                {
                                                    "id": "owner_mkp",
                                                    "text": "Ben",
                                                    "value": None,
                                                },
                                                {
                                                    "id": "objective_mkp",
                                                    "text": "Grow Revenue",
                                                    "value": None,
                                                },
                                                {
                                                    "id": "initiative_mkp",
                                                    "text": "Partner Pipeline",
                                                    "value": None,
                                                },
                                            ],
                                        },
                                        {
                                            "id": "ki-2",
                                            "name": "Finished initiative",
                                            "group": {
                                                "id": "key-initiatives",
                                                "title": "Key Initiatives",
                                            },
                                            "column_values": [
                                                {
                                                    "id": "status_mkp",
                                                    "text": "Done",
                                                    "value": None,
                                                }
                                            ],
                                        },
                                        {
                                            "id": "gs-1",
                                            "name": "Regular action item",
                                            "group": {
                                                "id": "action-items",
                                                "title": "Action Items",
                                            },
                                            "column_values": [],
                                        },
                                    ],
                                },
                            }
                        ]
                    }
                },
                request=request,
            )

    monkeypatch.setenv("MONDAY_API_TOKEN", "test-token")
    monkeypatch.delenv("TIMMENY_OS_API_KEY", raising=False)
    monkeypatch.delenv("GS_KEY_INITIATIVES_GROUP_ID", raising=False)
    monkeypatch.setenv("GS_TODO_BOARD_ID", "gs-board")
    monkeypatch.setattr(main.httpx, "AsyncClient", FakeAsyncClient)

    response = client.get("/key-initiatives")

    assert response.status_code == 200
    assert response.json() == {
        "success": True,
        "count": 1,
        "items": [
            {
                "item_id": "ki-1",
                "title": "Build partner pipeline",
                "group_id": "key-initiatives",
                "group_title": "Key Initiatives",
                "action_group": None,
                "action_date": None,
                "action": None,
                "annual_objective": "Grow Revenue",
                "initiative_project": "Partner Pipeline",
                "status": "Not Yet Started",
                "owner": "Ben",
                "due_date": None,
            }
        ],
    }
    assert requests[0]["variables"] == {
        "board_id": "gs-board",
        "limit": 500,
    }


def test_list_key_initiatives_can_filter_by_configured_group_id(monkeypatch):
    class FakeAsyncClient:
        def __init__(self, timeout):
            self.timeout = timeout

        async def __aenter__(self):
            return self

        async def __aexit__(self, exc_type, exc, traceback):
            return False

        async def post(self, url, json, headers):
            request = httpx.Request("POST", url)
            return httpx.Response(
                200,
                json={
                    "data": {
                        "boards": [
                            {
                                "columns": [],
                                "items_page": {
                                    "cursor": None,
                                    "items": [
                                        {
                                            "id": "ki-1",
                                            "name": "Configured group item",
                                            "group": {
                                                "id": "custom-group",
                                                "title": "Different Title",
                                            },
                                            "column_values": [],
                                        },
                                        {
                                            "id": "ki-2",
                                            "name": "Title match only",
                                            "group": {
                                                "id": "key-initiatives",
                                                "title": "Key Initiatives",
                                            },
                                            "column_values": [],
                                        },
                                    ],
                                },
                            }
                        ]
                    }
                },
                request=request,
            )

    monkeypatch.setenv("MONDAY_API_TOKEN", "test-token")
    monkeypatch.delenv("TIMMENY_OS_API_KEY", raising=False)
    monkeypatch.setenv("GS_TODO_BOARD_ID", "gs-board")
    monkeypatch.setenv("GS_KEY_INITIATIVES_GROUP_ID", "custom-group")
    monkeypatch.setattr(main.httpx, "AsyncClient", FakeAsyncClient)

    response = client.get("/key-initiatives")

    assert response.status_code == 200
    assert response.json()["count"] == 1
    assert response.json()["items"][0]["item_id"] == "ki-1"


def test_list_key_initiatives_requires_gs_board_id(monkeypatch):
    monkeypatch.setenv("MONDAY_API_TOKEN", "test-token")
    monkeypatch.delenv("TIMMENY_OS_API_KEY", raising=False)
    monkeypatch.delenv("GS_TODO_BOARD_ID", raising=False)

    response = client.get("/key-initiatives")

    assert response.status_code == 500
    assert response.json() == {
        "detail": "GS_TODO_BOARD_ID environment variable is not configured."
    }
