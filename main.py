import json
import os
from datetime import date
from enum import StrEnum
from typing import Any

import httpx
from fastapi import Body, FastAPI, Header, HTTPException, Query
from pydantic import BaseModel, Field, field_validator

from adminos.api.admin import router as admin_router
from adminos.api.review import router as review_router
from adminos.api.security import extract_bearer_token


MONDAY_API_URL = "https://api.monday.com/v2"
ACTION_GROUP_COLUMN_TITLE = "Action Group"
ACTION_DATE_COLUMN_TITLE = "Action Date"
ACTION_COLUMN_TITLE = "Action"
STATUS_COLUMN_TITLE = "Status"
OWNER_COLUMN_TITLE = "Owner"
DUE_DATE_COLUMN_TITLE = "Due Date"
ANNUAL_OBJECTIVE_COLUMN_TITLE = "Annual Objective"
INITIATIVE_PROJECT_COLUMN_TITLE = "Initiative"
DECISION_ACTION = "Decision"
DEFAULT_DECISION_STATUS = "Not Yet Started"
DONE_STATUS_LABELS = {"done", "complete", "completed"}
MONDAY_ITEMS_PAGE_SIZE = 500
KEY_INITIATIVES_GROUP_TITLE = "Key Initiatives"
GS_KEY_INITIATIVES_GROUP_ID_VARIABLE = "GS_KEY_INITIATIVES_GROUP_ID"

app = FastAPI(title="timmeny-admin-os", version="0.5.0")
app.include_router(admin_router)
app.include_router(review_router)


class TodoList(StrEnum):
    TODO = "todo"
    GS = "gs"


class TodoListFilter(StrEnum):
    ALL = "all"
    TODO = "todo"
    GS = "gs"


class TodoActionMetadata(BaseModel):
    action_group: str | None = Field(default=None, max_length=255)
    action_date: date | None = None
    action: str | None = Field(default=None, max_length=255)
    annual_objective: str | None = Field(default=None, max_length=255)
    initiative_project: str | None = Field(default=None, max_length=255)

    @field_validator(
        "action_group",
        "action",
        "annual_objective",
        "initiative_project",
    )
    @classmethod
    def optional_strings_must_not_be_blank(cls, value: str | None) -> str | None:
        if value is None:
            return None
        stripped_value = value.strip()
        return stripped_value or None


class TodoCreateRequest(TodoActionMetadata):
    title: str = Field(..., min_length=1, max_length=255)
    list: TodoList = TodoList.TODO

    @field_validator("title")
    @classmethod
    def title_must_not_be_blank(cls, value: str) -> str:
        stripped_value = value.strip()
        if not stripped_value:
            raise ValueError("title must not be blank")
        return stripped_value


class TodoCreateResponse(TodoActionMetadata):
    success: bool
    item_id: str
    title: str
    list: TodoList


class TodoUpdateActionMetadataRequest(TodoActionMetadata):
    list: TodoList


class TodoUpdateActionMetadataResponse(TodoActionMetadata):
    success: bool
    item_id: str
    list: TodoList


class TodoBulkActionMetadataUpdate(TodoUpdateActionMetadataRequest):
    item_id: str = Field(..., min_length=1)

    @field_validator("item_id")
    @classmethod
    def item_id_must_not_be_blank(cls, value: str) -> str:
        stripped_value = value.strip()
        if not stripped_value:
            raise ValueError("item_id must not be blank")
        return stripped_value


class TodoBulkActionMetadataRequest(BaseModel):
    updates: list[TodoBulkActionMetadataUpdate] = Field(..., min_length=1, max_length=100)


class TodoBulkActionMetadataResult(TodoActionMetadata):
    success: bool
    item_id: str
    list: TodoList
    error: str | dict[str, Any] | None = None


class TodoBulkActionMetadataResponse(BaseModel):
    success: bool
    updated_count: int
    failed_count: int
    results: list[TodoBulkActionMetadataResult]


class TodoBulkActionMetadataSimpleRequest(BaseModel):
    updates_json: str = Field(..., min_length=1)

    @field_validator("updates_json")
    @classmethod
    def updates_json_must_not_be_blank(cls, value: str) -> str:
        stripped_value = value.strip()
        if not stripped_value:
            raise ValueError("updates_json must not be blank")
        return stripped_value


class TodoBulkActionMetadataSimpleResponse(BaseModel):
    success: bool
    updated_count: int
    failed_count: int


class TodoItem(TodoActionMetadata):
    item_id: str
    title: str
    list: TodoList
    group_id: str | None = None
    group_title: str | None = None
    status: str | None = None
    owner: str | None = None
    due_date: str | None = None


class TodoListResponse(BaseModel):
    success: bool
    count: int
    items: list[TodoItem]


class TodoMetadataColumn(BaseModel):
    id: str
    title: str
    type: str
    labels: list[str] = Field(default_factory=list)
    observed_values: list[str] = Field(default_factory=list)


class TodoMetadataResponse(BaseModel):
    success: bool
    list: TodoList
    columns: list[TodoMetadataColumn]


class KeyInitiativeItem(TodoActionMetadata):
    item_id: str
    title: str
    group_id: str | None = None
    group_title: str | None = None
    status: str | None = None
    owner: str | None = None
    due_date: str | None = None


class KeyInitiativeListResponse(BaseModel):
    success: bool
    count: int
    items: list[KeyInitiativeItem]


class GptReadResponse(BaseModel):
    success: bool
    count: int
    result_text: str


class HealthResponse(BaseModel):
    status: str


@app.get("/health", response_model=HealthResponse)
async def health() -> HealthResponse:
    return HealthResponse(status="ok")


@app.get("/todos", response_model=TodoListResponse)
async def list_todos(
    list_filter: TodoListFilter = Query(default=TodoListFilter.ALL, alias="list"),
    limit: int = Query(default=MONDAY_ITEMS_PAGE_SIZE, ge=1, le=MONDAY_ITEMS_PAGE_SIZE),
    include_done: bool = Query(default=False),
    x_api_key: str | None = Header(default=None),
    authorization: str | None = Header(default=None),
) -> TodoListResponse:
    verify_api_key(x_api_key=x_api_key, authorization=authorization)

    items = await get_todo_items(
        list_filter=list_filter,
        limit=limit,
        include_done=include_done,
    )
    return TodoListResponse(success=True, count=len(items), items=items)


@app.get("/todos/read-simple", response_model=GptReadResponse)
async def list_todos_simple(
    list_filter: TodoListFilter = Query(default=TodoListFilter.ALL, alias="list"),
    limit: int = Query(default=MONDAY_ITEMS_PAGE_SIZE, ge=1, le=MONDAY_ITEMS_PAGE_SIZE),
    include_done: bool = Query(default=False),
    x_api_key: str | None = Header(default=None),
    authorization: str | None = Header(default=None),
) -> GptReadResponse:
    verify_api_key(x_api_key=x_api_key, authorization=authorization)

    items = await get_todo_items(
        list_filter=list_filter,
        limit=limit,
        include_done=include_done,
    )
    return GptReadResponse(
        success=True,
        count=len(items),
        result_text=format_todo_items_for_gpt(items),
    )


async def get_todo_items(
    list_filter: TodoListFilter,
    limit: int,
    include_done: bool,
) -> list[TodoItem]:
    monday_token = get_monday_token()
    todo_lists = get_todo_lists_for_filter(list_filter)
    items: list[TodoItem] = []

    for todo_list in todo_lists:
        target = get_todo_target(todo_list)
        monday_items = await get_monday_items(
            token=monday_token,
            board_id=target["board_id"],
            limit=limit,
        )
        if not include_done:
            monday_items = [
                item for item in monday_items if not is_done_monday_item(item)
            ]
        items.extend(
            TodoItem(
                item_id=item["id"],
                title=item["name"],
                list=todo_list,
                group_id=get_monday_item_group(item).get("id"),
                group_title=get_monday_item_group(item).get("title"),
                **get_monday_action_metadata(item),
                **get_monday_planning_metadata(item),
            )
            for item in monday_items
        )

    return items


@app.get("/todos/metadata", response_model=TodoMetadataResponse)
async def get_todo_metadata(
    todo_list: TodoList = Query(default=TodoList.GS, alias="list"),
    x_api_key: str | None = Header(default=None),
    authorization: str | None = Header(default=None),
) -> TodoMetadataResponse:
    verify_api_key(x_api_key=x_api_key, authorization=authorization)

    columns = await get_todo_metadata_columns(todo_list=todo_list)
    return TodoMetadataResponse(success=True, list=todo_list, columns=columns)


@app.get("/todos/metadata/read-simple", response_model=GptReadResponse)
async def get_todo_metadata_simple(
    todo_list: TodoList = Query(default=TodoList.GS, alias="list"),
    x_api_key: str | None = Header(default=None),
    authorization: str | None = Header(default=None),
) -> GptReadResponse:
    verify_api_key(x_api_key=x_api_key, authorization=authorization)

    columns = await get_todo_metadata_columns(todo_list=todo_list)
    return GptReadResponse(
        success=True,
        count=len(columns),
        result_text=format_todo_metadata_for_gpt(columns),
    )


async def get_todo_metadata_columns(todo_list: TodoList) -> list[TodoMetadataColumn]:
    monday_token = get_monday_token()
    target = get_todo_target(todo_list)
    columns_by_title = await get_board_columns_by_title(
        token=monday_token,
        board_id=target["board_id"],
    )
    monday_items = await get_monday_items(
        token=monday_token,
        board_id=target["board_id"],
        limit=MONDAY_ITEMS_PAGE_SIZE,
    )
    planning_columns = [
        ANNUAL_OBJECTIVE_COLUMN_TITLE,
        INITIATIVE_PROJECT_COLUMN_TITLE,
        ACTION_GROUP_COLUMN_TITLE,
        ACTION_DATE_COLUMN_TITLE,
        ACTION_COLUMN_TITLE,
        STATUS_COLUMN_TITLE,
        OWNER_COLUMN_TITLE,
        DUE_DATE_COLUMN_TITLE,
    ]
    columns = [
        TodoMetadataColumn(
            id=column["id"],
            title=column["title"],
            type=column.get("type") or "",
            labels=get_column_settings_labels(column),
            observed_values=get_observed_column_values(monday_items, title),
        )
        for title in planning_columns
        if (column := columns_by_title.get(title))
    ]
    return columns


@app.get("/key-initiatives", response_model=KeyInitiativeListResponse)
async def list_key_initiatives(
    limit: int = Query(default=MONDAY_ITEMS_PAGE_SIZE, ge=1, le=MONDAY_ITEMS_PAGE_SIZE),
    include_done: bool = Query(default=False),
    x_api_key: str | None = Header(default=None),
    authorization: str | None = Header(default=None),
) -> KeyInitiativeListResponse:
    verify_api_key(x_api_key=x_api_key, authorization=authorization)

    items = await get_key_initiative_items(limit=limit, include_done=include_done)
    return KeyInitiativeListResponse(success=True, count=len(items), items=items)


@app.get("/key-initiatives/read-simple", response_model=GptReadResponse)
async def list_key_initiatives_simple(
    limit: int = Query(default=MONDAY_ITEMS_PAGE_SIZE, ge=1, le=MONDAY_ITEMS_PAGE_SIZE),
    include_done: bool = Query(default=False),
    x_api_key: str | None = Header(default=None),
    authorization: str | None = Header(default=None),
) -> GptReadResponse:
    verify_api_key(x_api_key=x_api_key, authorization=authorization)

    items = await get_key_initiative_items(limit=limit, include_done=include_done)
    return GptReadResponse(
        success=True,
        count=len(items),
        result_text=format_todo_items_for_gpt(items),
    )


async def get_key_initiative_items(
    limit: int,
    include_done: bool,
) -> list[KeyInitiativeItem]:
    monday_token = get_monday_token()
    target = get_todo_target(TodoList.GS)
    monday_items = await get_monday_items(
        token=monday_token,
        board_id=target["board_id"],
        limit=MONDAY_ITEMS_PAGE_SIZE,
    )
    monday_items = [item for item in monday_items if is_key_initiatives_item(item)]
    if not include_done:
        monday_items = [item for item in monday_items if not is_done_monday_item(item)]
    monday_items = monday_items[:limit]

    items = [
        KeyInitiativeItem(
            item_id=item["id"],
            title=item["name"],
            group_id=get_monday_item_group(item).get("id"),
            group_title=get_monday_item_group(item).get("title"),
            **get_monday_action_metadata(item),
            **get_monday_planning_metadata(item),
        )
        for item in monday_items
    ]
    return items


@app.post("/todos", response_model=TodoCreateResponse)
async def create_todo(
    payload: TodoCreateRequest,
    x_api_key: str | None = Header(default=None),
    authorization: str | None = Header(default=None),
) -> TodoCreateResponse:
    verify_api_key(x_api_key=x_api_key, authorization=authorization)

    monday_token = get_monday_token()
    target = get_todo_target(payload.list)
    column_values = await build_action_column_values(
        token=monday_token,
        board_id=target["board_id"],
        action_group=payload.action_group,
        action_date=payload.action_date,
        action=payload.action,
        annual_objective=payload.annual_objective,
        initiative_project=payload.initiative_project,
    )

    item_id = await create_monday_item(
        token=monday_token,
        board_id=target["board_id"],
        group_id=target["group_id"],
        title=payload.title,
        column_values=column_values,
    )

    return TodoCreateResponse(
        success=True,
        item_id=item_id,
        title=payload.title,
        list=payload.list,
        action_group=payload.action_group,
        action_date=payload.action_date,
        action=payload.action,
        annual_objective=payload.annual_objective,
        initiative_project=payload.initiative_project,
    )


@app.patch("/todos/{item_id}/action-metadata", response_model=TodoUpdateActionMetadataResponse)
async def update_todo_action_metadata(
    item_id: str,
    payload: TodoUpdateActionMetadataRequest,
    x_api_key: str | None = Header(default=None),
    authorization: str | None = Header(default=None),
) -> TodoUpdateActionMetadataResponse:
    verify_api_key(x_api_key=x_api_key, authorization=authorization)

    monday_token = get_monday_token()
    target = get_todo_target(payload.list)
    column_values = await build_action_column_values(
        token=monday_token,
        board_id=target["board_id"],
        action_group=payload.action_group,
        action_date=payload.action_date,
        action=payload.action,
        annual_objective=payload.annual_objective,
        initiative_project=payload.initiative_project,
    )

    if not column_values:
        raise HTTPException(
            status_code=422,
            detail="At least one action metadata field is required.",
        )

    updated_item_id = await update_monday_item_columns(
        token=monday_token,
        board_id=target["board_id"],
        item_id=item_id,
        column_values=column_values,
    )

    return TodoUpdateActionMetadataResponse(
        success=True,
        item_id=updated_item_id,
        list=payload.list,
        action_group=payload.action_group,
        action_date=payload.action_date,
        action=payload.action,
        annual_objective=payload.annual_objective,
        initiative_project=payload.initiative_project,
    )


@app.post("/todos/bulk-action-metadata", response_model=TodoBulkActionMetadataResponse)
async def bulk_update_todo_action_metadata(
    payload: TodoBulkActionMetadataRequest,
    x_api_key: str | None = Header(default=None),
    authorization: str | None = Header(default=None),
) -> TodoBulkActionMetadataResponse:
    verify_api_key(x_api_key=x_api_key, authorization=authorization)

    return await perform_bulk_update_todo_action_metadata(payload)


@app.post("/todos/bulk-action-metadata-json", response_model=TodoBulkActionMetadataResponse)
async def bulk_update_todo_action_metadata_json(
    payload: Any = Body(...),
    x_api_key: str | None = Header(default=None),
    authorization: str | None = Header(default=None),
) -> TodoBulkActionMetadataResponse:
    verify_api_key(x_api_key=x_api_key, authorization=authorization)

    updates = parse_bulk_update_payload(payload)
    bulk_payload = validate_bulk_updates(updates)
    return await perform_bulk_update_todo_action_metadata(bulk_payload)


@app.post(
    "/todos/bulk-action-metadata-simple",
    response_model=TodoBulkActionMetadataSimpleResponse,
)
async def bulk_update_todo_action_metadata_simple(
    payload: TodoBulkActionMetadataSimpleRequest,
    x_api_key: str | None = Header(default=None),
    authorization: str | None = Header(default=None),
) -> TodoBulkActionMetadataSimpleResponse:
    verify_api_key(x_api_key=x_api_key, authorization=authorization)

    updates = parse_bulk_update_payload({"updates_json": payload.updates_json})
    bulk_payload = validate_bulk_updates(updates)
    result = await perform_bulk_update_todo_action_metadata(bulk_payload)
    return TodoBulkActionMetadataSimpleResponse(
        success=result.success,
        updated_count=result.updated_count,
        failed_count=result.failed_count,
    )


def parse_bulk_update_payload(payload: Any) -> list[Any]:
    if isinstance(payload, list):
        return payload

    if not isinstance(payload, dict):
        raise HTTPException(
            status_code=422,
            detail="Bulk update payload must be an object or array.",
        )

    if "updates" in payload:
        updates = payload["updates"]
        if not isinstance(updates, list):
            raise HTTPException(status_code=422, detail="updates must be an array.")
        return updates

    if "updates_json" not in payload:
        raise HTTPException(
            status_code=422,
            detail="Bulk update payload must include updates or updates_json.",
        )

    updates_json = payload["updates_json"]
    if isinstance(updates_json, list):
        return updates_json

    if not isinstance(updates_json, str):
        raise HTTPException(
            status_code=422,
            detail="updates_json must be a JSON string or array.",
        )

    try:
        updates = json.loads(updates_json)
    except json.JSONDecodeError as exc:
        raise HTTPException(status_code=422, detail="updates_json must be valid JSON.") from exc

    if not isinstance(updates, list):
        raise HTTPException(status_code=422, detail="updates_json must be a JSON array.")
    return updates


def validate_bulk_updates(updates: list[Any]) -> TodoBulkActionMetadataRequest:
    try:
        return TodoBulkActionMetadataRequest.model_validate({"updates": updates})
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc


def format_todo_items_for_gpt(items: list[TodoItem] | list[KeyInitiativeItem]) -> str:
    if not items:
        return "No items found."

    lines = []
    for item in items:
        parts = [
            f"item_id={item.item_id}",
            f"title={item.title}",
            f"list={getattr(item, 'list', 'gs')}",
            f"group={format_optional_value(item.group_title)}",
            f"status={format_optional_value(item.status)}",
            f"owner={format_optional_value(item.owner)}",
            f"due_date={format_optional_value(item.due_date)}",
            f"action={format_optional_value(item.action)}",
            f"action_date={format_optional_value(item.action_date)}",
            f"action_group={format_optional_value(item.action_group)}",
            f"annual_objective={format_optional_value(item.annual_objective)}",
            f"initiative={format_optional_value(item.initiative_project)}",
        ]
        lines.append(" | ".join(parts))
    return "\n".join(lines)


def format_todo_metadata_for_gpt(columns: list[TodoMetadataColumn]) -> str:
    if not columns:
        return "No metadata columns found."

    lines = []
    for column in columns:
        labels = ", ".join(column.labels) if column.labels else "none"
        observed_values = (
            ", ".join(column.observed_values) if column.observed_values else "none"
        )
        lines.append(
            f"id={column.id} | title={column.title} | type={column.type} | "
            f"labels={labels} | observed_values={observed_values}"
        )
    return "\n".join(lines)


def format_optional_value(value: Any) -> str:
    if value is None:
        return "none"
    value_text = str(value).strip()
    return value_text or "none"


async def perform_bulk_update_todo_action_metadata(
    payload: TodoBulkActionMetadataRequest,
) -> TodoBulkActionMetadataResponse:
    monday_token = get_monday_token()
    columns_by_board_id: dict[str, dict[str, dict[str, Any]]] = {}
    results: list[TodoBulkActionMetadataResult] = []

    for update in payload.updates:
        try:
            target = get_todo_target(update.list)
            board_id = target["board_id"]
            if board_id not in columns_by_board_id:
                columns_by_board_id[board_id] = await get_board_columns_by_title(
                    token=monday_token,
                    board_id=board_id,
                )

            column_values = build_action_column_values_from_columns(
                columns_by_title=columns_by_board_id[board_id],
                action_group=update.action_group,
                action_date=update.action_date,
                action=update.action,
                annual_objective=update.annual_objective,
                initiative_project=update.initiative_project,
            )
            if not column_values:
                raise HTTPException(
                    status_code=422,
                    detail="At least one action metadata field is required.",
                )

            updated_item_id = await update_monday_item_columns(
                token=monday_token,
                board_id=board_id,
                item_id=update.item_id,
                column_values=column_values,
            )
            results.append(
                TodoBulkActionMetadataResult(
                    success=True,
                    item_id=updated_item_id,
                    list=update.list,
                    action_group=update.action_group,
                    action_date=update.action_date,
                    action=update.action,
                    annual_objective=update.annual_objective,
                    initiative_project=update.initiative_project,
                )
            )
        except HTTPException as exc:
            results.append(
                TodoBulkActionMetadataResult(
                    success=False,
                    item_id=update.item_id,
                    list=update.list,
                    action_group=update.action_group,
                    action_date=update.action_date,
                    action=update.action,
                    annual_objective=update.annual_objective,
                    initiative_project=update.initiative_project,
                    error=exc.detail,
                )
            )

    updated_count = sum(1 for result in results if result.success)
    failed_count = len(results) - updated_count
    return TodoBulkActionMetadataResponse(
        success=failed_count == 0,
        updated_count=updated_count,
        failed_count=failed_count,
        results=results,
    )


def get_monday_token() -> str:
    return get_required_env_var("MONDAY_API_TOKEN")


def get_required_env_var(variable_name: str) -> str:
    value = os.getenv(variable_name)
    if not value:
        raise HTTPException(
            status_code=500,
            detail=f"{variable_name} environment variable is not configured.",
        )
    return value


def verify_api_key(
    x_api_key: str | None,
    authorization: str | None,
) -> None:
    expected_api_key = os.getenv("TIMMENY_OS_API_KEY")
    if not expected_api_key:
        return

    provided_api_key = x_api_key or extract_bearer_token(authorization)

    if provided_api_key != expected_api_key:
        raise HTTPException(
            status_code=401,
            detail="Invalid or missing API key.",
        )


def get_todo_lists_for_filter(list_filter: TodoListFilter) -> list[TodoList]:
    if list_filter == TodoListFilter.TODO:
        return [TodoList.TODO]
    if list_filter == TodoListFilter.GS:
        return [TodoList.GS]
    return [TodoList.TODO, TodoList.GS]


def get_monday_item_group(item: dict[str, Any]) -> dict[str, Any]:
    group = item.get("group")
    if not isinstance(group, dict):
        return {}
    return group


def get_monday_action_metadata(item: dict[str, Any]) -> dict[str, str | None]:
    metadata = {
        "action_group": None,
        "action_date": None,
        "action": None,
        "annual_objective": None,
        "initiative_project": None,
    }
    column_values = item.get("column_values")
    if not isinstance(column_values, list):
        return metadata

    for column_value in column_values:
        if not isinstance(column_value, dict):
            continue
        column = column_value.get("column")
        if not isinstance(column, dict):
            continue

        title = column.get("title")
        text = column_value.get("text")
        if not text:
            continue

        if title == ACTION_GROUP_COLUMN_TITLE:
            metadata["action_group"] = text
        elif title == ACTION_DATE_COLUMN_TITLE:
            metadata["action_date"] = text
        elif title == ACTION_COLUMN_TITLE:
            metadata["action"] = text
        elif title == ANNUAL_OBJECTIVE_COLUMN_TITLE:
            metadata["annual_objective"] = text
        elif title == INITIATIVE_PROJECT_COLUMN_TITLE:
            metadata["initiative_project"] = text

    return metadata


def get_monday_planning_metadata(item: dict[str, Any]) -> dict[str, str | None]:
    return {
        "status": get_monday_column_text(item, STATUS_COLUMN_TITLE),
        "owner": get_monday_column_text(item, OWNER_COLUMN_TITLE),
        "due_date": get_monday_column_text(item, DUE_DATE_COLUMN_TITLE),
    }


def is_done_monday_item(item: dict[str, Any]) -> bool:
    status = get_monday_column_text(item, STATUS_COLUMN_TITLE)
    if status is None:
        return False
    return status.strip().casefold() in DONE_STATUS_LABELS


def is_key_initiatives_item(item: dict[str, Any]) -> bool:
    group = get_monday_item_group(item)
    configured_group_id = os.getenv(GS_KEY_INITIATIVES_GROUP_ID_VARIABLE)
    if configured_group_id:
        return group.get("id") == configured_group_id

    group_title = group.get("title")
    if not isinstance(group_title, str):
        return False
    return group_title.strip().casefold() == KEY_INITIATIVES_GROUP_TITLE.casefold()


def get_monday_column_text(item: dict[str, Any], column_title: str) -> str | None:
    column_values = item.get("column_values")
    if not isinstance(column_values, list):
        return None

    for column_value in column_values:
        if not isinstance(column_value, dict):
            continue
        column = column_value.get("column")
        if not isinstance(column, dict):
            continue
        if column.get("title") != column_title:
            continue
        text = column_value.get("text")
        if isinstance(text, str):
            return text

    return None


def get_todo_target(todo_list: TodoList) -> dict[str, str | None]:
    env_prefix = "TODO" if todo_list == TodoList.TODO else "GS_TODO"
    board_id_variable = f"{env_prefix}_BOARD_ID"
    group_id_variable = f"{env_prefix}_GROUP_ID"

    board_id = os.getenv(board_id_variable)
    if not board_id:
        raise HTTPException(
            status_code=500,
            detail=f"{board_id_variable} environment variable is not configured.",
        )

    return {
        "board_id": board_id,
        "group_id": os.getenv(group_id_variable) or None,
    }


async def get_monday_items(token: str, board_id: str, limit: int) -> list[dict[str, Any]]:
    query = """
    query GetTodoItems($board_id: ID!, $limit: Int!) {
      boards(ids: [$board_id]) {
        columns {
          id
          title
        }
        items_page(limit: $limit) {
          cursor
          items {
            id
            name
            group {
              id
              title
            }
            column_values {
              id
              text
              value
            }
          }
        }
      }
    }
    """

    response_body = await execute_monday_graphql(
        token=token,
        body={
            "query": query,
            "variables": {
                "board_id": board_id,
                "limit": min(limit, MONDAY_ITEMS_PAGE_SIZE),
            },
        },
    )

    boards = response_body.get("data", {}).get("boards", [])
    if not boards:
        raise HTTPException(
            status_code=502,
            detail="Monday.com response did not include board data.",
        )

    board = boards[0]
    columns_by_id = {
        column["id"]: column
        for column in board.get("columns", [])
        if isinstance(column, dict) and column.get("id")
    }
    items_page = board.get("items_page", {})
    items = list(items_page.get("items", []))
    attach_monday_column_metadata(items=items, columns_by_id=columns_by_id)

    cursor = items_page.get("cursor")
    while cursor and len(items) < limit:
        next_items_page = await get_next_monday_items_page(
            token=token,
            cursor=cursor,
            limit=min(MONDAY_ITEMS_PAGE_SIZE, limit - len(items)),
        )
        next_items = list(next_items_page.get("items", []))
        attach_monday_column_metadata(items=next_items, columns_by_id=columns_by_id)
        items.extend(next_items)
        cursor = next_items_page.get("cursor")

    return items[:limit]


async def get_next_monday_items_page(
    token: str,
    cursor: str,
    limit: int,
) -> dict[str, Any]:
    query = """
    query GetNextTodoItems($cursor: String!, $limit: Int!) {
      next_items_page(cursor: $cursor, limit: $limit) {
        cursor
        items {
          id
          name
          group {
            id
            title
          }
          column_values {
            id
            text
            value
          }
        }
      }
    }
    """
    response_body = await execute_monday_graphql(
        token=token,
        body={
            "query": query,
            "variables": {
                "cursor": cursor,
                "limit": limit,
            },
        },
    )
    items_page = response_body.get("data", {}).get("next_items_page")
    if not isinstance(items_page, dict):
        raise HTTPException(
            status_code=502,
            detail="Monday.com response did not include next item page data.",
        )
    return items_page


def attach_monday_column_metadata(
    items: list[dict[str, Any]],
    columns_by_id: dict[str, dict[str, Any]],
) -> None:
    for item in items:
        if not isinstance(item, dict):
            continue
        column_values = item.get("column_values")
        if not isinstance(column_values, list):
            continue
        for column_value in column_values:
            if not isinstance(column_value, dict) or column_value.get("column"):
                continue
            column = columns_by_id.get(column_value.get("id"))
            if column:
                column_value["column"] = column


async def create_monday_item(
    token: str,
    board_id: str,
    group_id: str | None,
    title: str,
    column_values: dict[str, Any] | None = None,
) -> str:
    query = """
    mutation CreateTodo($board_id: ID!, $group_id: String, $item_name: String!, $column_values: JSON) {
      create_item(board_id: $board_id, group_id: $group_id, item_name: $item_name, column_values: $column_values) {
        id
      }
    }
    """

    response_body = await execute_monday_graphql(
        token=token,
        body={
            "query": query,
            "variables": {
                "board_id": board_id,
                "group_id": group_id,
                "item_name": title,
                "column_values": serialize_column_values(column_values),
            },
        },
    )

    item_id = (
        response_body.get("data", {})
        .get("create_item", {})
        .get("id")
    )

    if not item_id:
        raise HTTPException(
            status_code=502,
            detail="Monday.com response did not include a created item id.",
        )

    return str(item_id)


async def update_monday_item_columns(
    token: str,
    board_id: str,
    item_id: str,
    column_values: dict[str, Any],
) -> str:
    query = """
    mutation UpdateTodoColumns($board_id: ID!, $item_id: ID!, $column_values: JSON!) {
      change_multiple_column_values(board_id: $board_id, item_id: $item_id, column_values: $column_values) {
        id
      }
    }
    """

    response_body = await execute_monday_graphql(
        token=token,
        body={
            "query": query,
            "variables": {
                "board_id": board_id,
                "item_id": item_id,
                "column_values": serialize_column_values(column_values),
            },
        },
    )

    updated_item_id = (
        response_body.get("data", {})
        .get("change_multiple_column_values", {})
        .get("id")
    )

    if not updated_item_id:
        raise HTTPException(
            status_code=502,
            detail="Monday.com response did not include an updated item id.",
        )

    return str(updated_item_id)


async def build_action_column_values(
    token: str,
    board_id: str,
    action_group: str | None,
    action_date: date | None,
    action: str | None,
    annual_objective: str | None,
    initiative_project: str | None,
) -> dict[str, Any]:
    requested_columns = {
        ACTION_GROUP_COLUMN_TITLE: action_group,
        ACTION_DATE_COLUMN_TITLE: action_date,
        ACTION_COLUMN_TITLE: action,
        ANNUAL_OBJECTIVE_COLUMN_TITLE: annual_objective,
        INITIATIVE_PROJECT_COLUMN_TITLE: initiative_project,
        STATUS_COLUMN_TITLE: DEFAULT_DECISION_STATUS if is_decision_action(action) else None,
    }
    if all(value is None for value in requested_columns.values()):
        return {}

    columns_by_title = await get_board_columns_by_title(token=token, board_id=board_id)
    return build_action_column_values_from_columns(
        columns_by_title=columns_by_title,
        action_group=action_group,
        action_date=action_date,
        action=action,
        annual_objective=annual_objective,
        initiative_project=initiative_project,
    )


def build_action_column_values_from_columns(
    columns_by_title: dict[str, dict[str, Any]],
    action_group: str | None,
    action_date: date | None,
    action: str | None,
    annual_objective: str | None,
    initiative_project: str | None,
) -> dict[str, Any]:
    column_values: dict[str, Any] = {}

    if action_group is not None:
        column = require_board_column(columns_by_title, ACTION_GROUP_COLUMN_TITLE)
        column_values[column["id"]] = action_group

    if action_date is not None:
        column = require_board_column(columns_by_title, ACTION_DATE_COLUMN_TITLE)
        column_values[column["id"]] = {"date": action_date.isoformat()}

    if action is not None:
        column = require_board_column(columns_by_title, ACTION_COLUMN_TITLE)
        column_values[column["id"]] = build_label_column_value(column, action)

    if annual_objective is not None:
        column = require_board_column(columns_by_title, ANNUAL_OBJECTIVE_COLUMN_TITLE)
        column_values[column["id"]] = build_planning_column_value(
            column,
            annual_objective,
        )

    if initiative_project is not None:
        column = require_board_column(columns_by_title, INITIATIVE_PROJECT_COLUMN_TITLE)
        column_values[column["id"]] = build_planning_column_value(
            column,
            initiative_project,
        )

    if is_decision_action(action):
        column = require_board_column(columns_by_title, STATUS_COLUMN_TITLE)
        column_values[column["id"]] = build_label_column_value(
            column,
            DEFAULT_DECISION_STATUS,
        )

    return column_values


def is_decision_action(action: str | None) -> bool:
    return action is not None and action.strip().casefold() == DECISION_ACTION.casefold()


def build_label_column_value(column: dict[str, Any], label: str) -> dict[str, Any]:
    if column.get("type") == "status":
        return {"label": label}
    return {"labels": [label]}


def build_planning_column_value(column: dict[str, Any], value: str) -> str | dict[str, Any]:
    if column.get("type") in {"dropdown", "status"}:
        return build_label_column_value(column, value)
    return value


async def get_board_columns_by_title(
    token: str,
    board_id: str,
) -> dict[str, dict[str, Any]]:
    query = """
    query GetBoardColumns($board_id: ID!) {
      boards(ids: [$board_id]) {
        columns {
          id
          title
          type
          settings_str
        }
      }
    }
    """

    response_body = await execute_monday_graphql(
        token=token,
        body={
            "query": query,
            "variables": {
                "board_id": board_id,
            },
        },
    )

    boards = response_body.get("data", {}).get("boards", [])
    if not boards:
        raise HTTPException(
            status_code=502,
            detail="Monday.com response did not include board column data.",
        )

    columns = boards[0].get("columns", [])
    return {
        column["title"]: column
        for column in columns
        if isinstance(column, dict) and column.get("title") and column.get("id")
    }


def get_column_settings_labels(column: dict[str, Any]) -> list[str]:
    settings_str = column.get("settings_str")
    if not isinstance(settings_str, str) or not settings_str.strip():
        return []

    try:
        settings = json.loads(settings_str)
    except json.JSONDecodeError:
        return []

    labels = settings.get("labels")
    if isinstance(labels, dict):
        return dedupe_preserving_order(
            parse_column_label(label) for label in labels.values()
        )
    if isinstance(labels, list):
        return dedupe_preserving_order(parse_column_label(label) for label in labels)
    return []


def parse_column_label(label: Any) -> str | None:
    if isinstance(label, str):
        stripped_label = label.strip()
        return stripped_label or None
    if not isinstance(label, dict):
        return None

    for key in ("name", "label", "title", "text"):
        value = label.get(key)
        if isinstance(value, str) and value.strip():
            return value.strip()
    return None


def get_observed_column_values(
    items: list[dict[str, Any]],
    column_title: str,
) -> list[str]:
    return dedupe_preserving_order(
        get_monday_column_text(item, column_title) for item in items
    )


def dedupe_preserving_order(values: Any) -> list[str]:
    deduped_values: list[str] = []
    seen_values: set[str] = set()
    for value in values:
        if not isinstance(value, str):
            continue
        stripped_value = value.strip()
        if not stripped_value:
            continue
        normalized_value = stripped_value.casefold()
        if normalized_value in seen_values:
            continue
        seen_values.add(normalized_value)
        deduped_values.append(stripped_value)
    return deduped_values


def require_board_column(
    columns_by_title: dict[str, dict[str, Any]],
    title: str,
) -> dict[str, Any]:
    column = columns_by_title.get(title)
    if not column:
        raise HTTPException(
            status_code=502,
            detail=f'Monday.com board is missing the "{title}" column.',
        )
    return column


def serialize_column_values(column_values: dict[str, Any] | None) -> str | None:
    if not column_values:
        return None
    return json.dumps(column_values)


async def execute_monday_graphql(token: str, body: dict[str, Any]) -> dict[str, Any]:
    headers = {
        "Authorization": token,
        "Content-Type": "application/json",
    }
    try:
        async with httpx.AsyncClient(timeout=15.0) as client:
            response = await client.post(MONDAY_API_URL, json=body, headers=headers)
            response.raise_for_status()
    except httpx.TimeoutException as exc:
        raise HTTPException(
            status_code=504,
            detail="Timed out while contacting Monday.com.",
        ) from exc
    except httpx.HTTPStatusError as exc:
        raise HTTPException(
            status_code=502,
            detail=f"Monday.com returned HTTP {exc.response.status_code}.",
        ) from exc
    except httpx.HTTPError as exc:
        raise HTTPException(
            status_code=502,
            detail="Could not contact Monday.com.",
        ) from exc

    try:
        response_body: dict[str, Any] = response.json()
    except ValueError as exc:
        raise HTTPException(
            status_code=502,
            detail="Monday.com returned an invalid JSON response.",
        ) from exc

    monday_errors = response_body.get("errors")
    if monday_errors:
        raise HTTPException(
            status_code=502,
            detail={
                "message": "Monday.com GraphQL request failed.",
                "errors": monday_errors,
            },
        )

    return response_body
