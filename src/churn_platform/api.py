"""Prediction and retention operations API with model and batch lineage."""

from __future__ import annotations

from contextlib import asynccontextmanager
import json
import math
import os
from pathlib import Path
from typing import Annotated, Any, Literal

from fastapi import FastAPI, HTTPException, Query, Request
from fastapi.responses import FileResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, ConfigDict, Field

from .inference import canonical_sha256, load_model
from .feature_registry import FeatureRegistry
from .operations import DEFAULT_PLAYBOOKS, RiskBand, RiskPolicy, portfolio_summary, priority_customers
from .retention_store import RetentionTaskStore


STATIC_DIRECTORY = Path(__file__).resolve().parent / "static"


class Prediction(BaseModel):
    model_config = ConfigDict(extra="forbid")

    schema_version: int
    prediction_id: str
    customer_key: str
    as_of_time: str
    model_sha256: str
    churn_probability: str
    churn_prediction: bool
    decision_threshold: str


class PredictionPage(BaseModel):
    total: int
    offset: int
    limit: int
    items: list[Prediction]


class RetentionTaskCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    prediction_id: str = Field(min_length=1, max_length=128)
    owner: str = Field(min_length=1, max_length=80)
    playbook: str | None = Field(default=None, min_length=1, max_length=120)


class RetentionTaskStatusUpdate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    status: Literal["OPEN", "IN_PROGRESS", "COMPLETED", "DISMISSED"]


class RetentionTaskOwnerUpdate(BaseModel):
    model_config = ConfigDict(extra="forbid")
    owner: str = Field(min_length=1, max_length=80)


class RetentionTaskBulkOwnerUpdate(BaseModel):
    model_config = ConfigDict(extra="forbid")
    task_ids: list[str] = Field(min_length=1, max_length=100)
    owner: str = Field(min_length=1, max_length=80)


class RetentionTaskBulkStatusUpdate(BaseModel):
    model_config = ConfigDict(extra="forbid")
    task_ids: list[str] = Field(min_length=1, max_length=100)
    status: Literal["OPEN", "IN_PROGRESS", "COMPLETED", "DISMISSED"]


class PredictionRepository:
    def __init__(self, rows: list[dict[str, Any]], model: dict[str, Any]) -> None:
        self.model = model
        self.model_sha256 = canonical_sha256(model)
        self.rows: list[dict[str, Any]] = []
        self.by_id: dict[str, dict[str, Any]] = {}
        self.by_customer: dict[str, list[dict[str, Any]]] = {}
        identities: set[tuple[str, str]] = set()
        for index, row in enumerate(rows, start=1):
            try:
                prediction = Prediction.model_validate(row).model_dump()
            except Exception as error:
                raise ValueError(f"invalid prediction row {index}") from error
            prediction_id = prediction["prediction_id"]
            identity = (prediction["customer_key"], prediction["as_of_time"])
            if not prediction_id or not identity[0] or not identity[1]:
                raise ValueError("prediction identity fields must be non-empty")
            if prediction_id in self.by_id:
                raise ValueError(f"duplicate prediction_id: {prediction_id}")
            if identity in identities:
                raise ValueError(f"duplicate customer snapshot: {identity[0]} at {identity[1]}")
            if prediction["model_sha256"] != self.model_sha256:
                raise ValueError("prediction model lineage does not match the loaded model")
            probability = float(prediction["churn_probability"])
            if not math.isfinite(probability) or not 0 <= probability <= 1:
                raise ValueError("churn_probability must be between 0 and 1")
            threshold = float(prediction["decision_threshold"])
            if not math.isfinite(threshold) or abs(threshold - float(model["decision_threshold"])) > 1e-6:
                raise ValueError("prediction decision threshold does not match the loaded model")
            identities.add(identity)
            self.rows.append(prediction)
            self.by_id[prediction_id] = prediction
            self.by_customer.setdefault(identity[0], []).append(prediction)
        self.rows.sort(key=lambda row: (row["as_of_time"], row["customer_key"]))
        for values in self.by_customer.values():
            values.sort(key=lambda row: row["as_of_time"], reverse=True)

    @classmethod
    def from_files(cls, model_path: Path, predictions_path: Path) -> "PredictionRepository":
        model = load_model(model_path)
        rows: list[dict[str, Any]] = []
        with predictions_path.open(encoding="utf-8") as handle:
            for line_number, line in enumerate(handle, start=1):
                if not line.strip():
                    continue
                row = json.loads(line)
                if not isinstance(row, dict):
                    raise ValueError(f"prediction row {line_number} must be an object")
                rows.append(row)
        if not rows:
            raise ValueError("prediction batch must not be empty")
        return cls(rows, model)


def _repository(request: Request) -> PredictionRepository:
    return request.app.state.repository


def create_app(
    model_path: Path | None = None,
    predictions_path: Path | None = None,
    *,
    feature_registry_path: Path | None = None,
    task_database_path: Path | None = None,
) -> FastAPI:
    configured_model = model_path or Path(os.getenv("CHURN_MODEL_PATH", "build/demo/model/baseline.json"))
    configured_predictions = predictions_path or Path(
        os.getenv("CHURN_PREDICTIONS_PATH", "build/demo/predictions/candidate.ndjson")
    )
    configured_registry = feature_registry_path or Path(
        os.getenv("CHURN_FEATURE_REGISTRY_PATH", "config/feature-registry.json")
    )
    configured_task_database = task_database_path or Path(
        os.getenv("CHURN_TASK_DATABASE_PATH", "build/operations/retention-tasks.sqlite3")
    )

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        app.state.repository = PredictionRepository.from_files(
            configured_model, configured_predictions
        )
        app.state.feature_registry = FeatureRegistry.from_file(configured_registry)
        app.state.task_store = RetentionTaskStore(configured_task_database)
        yield
        app.state.task_store.close()
        app.state.repository = None
        app.state.feature_registry = None
        app.state.task_store = None

    app = FastAPI(
        title="Customer Retention Operations Platform",
        version="1.10.0",
        lifespan=lifespan,
    )

    @app.get("/", include_in_schema=False)
    def root() -> RedirectResponse:
        return RedirectResponse("/console")

    @app.get("/console", include_in_schema=False)
    def console() -> FileResponse:
        return FileResponse(STATIC_DIRECTORY / "index.html")

    @app.get("/health")
    def health(request: Request) -> dict[str, Any]:
        repository = _repository(request)
        return {
            "status": "ready",
            "model_sha256": repository.model_sha256,
            "prediction_count": len(repository.rows),
        }

    @app.get("/v1/model")
    def model_metadata(request: Request) -> dict[str, Any]:
        repository = _repository(request)
        model = repository.model
        return {
            "model_sha256": repository.model_sha256,
            "model_version": model["model_version"],
            "algorithm": model["algorithm"],
            "feature_columns": model["feature_columns"],
            "decision_threshold": model["decision_threshold"],
            "training_data_sha256": model["training_data_sha256"],
        }

    @app.get("/v1/features")
    def feature_registry(request: Request) -> dict[str, Any]:
        return request.app.state.feature_registry.to_public_dict()

    @app.get("/v1/portfolio/summary")
    def portfolio(request: Request) -> dict[str, Any]:
        repository = _repository(request)
        return {
            **portfolio_summary(repository.rows),
            "model_sha256": repository.model_sha256,
            "retention_tasks": request.app.state.task_store.status_counts(),
            "retention_event_count": request.app.state.task_store.event_count(),
            "retention_latest_event_id": request.app.state.task_store.latest_event_id(),
        }

    @app.get("/v1/portfolio/priority")
    def priority_queue(
        request: Request,
        risk_band: RiskBand | None = None,
        limit: Annotated[int, Query(ge=1, le=100)] = 20,
    ) -> dict[str, Any]:
        items = priority_customers(
            _repository(request).rows,
            risk_band=risk_band,
            limit=limit,
        )
        return {"count": len(items), "limit": limit, "risk_band": risk_band, "items": items}

    @app.get("/v1/predictions", response_model=PredictionPage)
    def list_predictions(
        request: Request,
        offset: Annotated[int, Query(ge=0, le=1_000_000)] = 0,
        limit: Annotated[int, Query(ge=1, le=100)] = 50,
        churn_prediction: bool | None = None,
    ) -> dict[str, Any]:
        rows = _repository(request).rows
        filtered = (
            rows
            if churn_prediction is None
            else [row for row in rows if row["churn_prediction"] is churn_prediction]
        )
        return {
            "total": len(filtered),
            "offset": offset,
            "limit": limit,
            "items": filtered[offset : offset + limit],
        }

    @app.get("/v1/predictions/{prediction_id}", response_model=Prediction)
    def get_prediction(prediction_id: str, request: Request) -> dict[str, Any]:
        row = _repository(request).by_id.get(prediction_id)
        if row is None:
            raise HTTPException(status_code=404, detail="prediction not found")
        return row

    @app.get("/v1/customers/{customer_key}/predictions", response_model=list[Prediction])
    def get_customer_predictions(
        customer_key: str,
        request: Request,
        limit: Annotated[int, Query(ge=1, le=100)] = 20,
    ) -> list[dict[str, Any]]:
        rows = _repository(request).by_customer.get(customer_key)
        if rows is None:
            raise HTTPException(status_code=404, detail="customer predictions not found")
        return rows[:limit]

    @app.get("/v1/retention/tasks")
    def list_retention_tasks(
        request: Request,
        status: Literal["OPEN", "IN_PROGRESS", "COMPLETED", "DISMISSED"] | None = None,
        risk_band: Literal["CRITICAL", "HIGH", "WATCH", "STABLE"] | None = None,
        owner: Annotated[str | None, Query(min_length=1, max_length=80)] = None,
        limit: Annotated[int, Query(ge=1, le=500)] = 100,
    ) -> dict[str, Any]:
        items = request.app.state.task_store.list(
            status=status,
            risk_band=risk_band,
            owner=owner,
            limit=limit,
        )
        return {
            "count": len(items),
            "limit": limit,
            "status": status,
            "filters": {"status": status, "risk_band": risk_band, "owner": owner},
            "items": items,
        }

    @app.get("/v1/retention/workloads")
    def retention_workloads(request: Request) -> dict[str, Any]:
        items = request.app.state.task_store.owner_workloads()
        return {
            "owner_count": len(items),
            "active_task_count": sum(item["active_count"] for item in items),
            "items": items,
        }

    @app.get("/v1/retention/events")
    def retention_events(
        request: Request,
        after_id: Annotated[int, Query(ge=0)] = 0,
        limit: Annotated[int, Query(ge=1, le=500)] = 100,
    ) -> dict[str, Any]:
        items = request.app.state.task_store.events_after(after_id, limit=limit)
        return {
            "count": len(items),
            "after_id": after_id,
            "next_after_id": items[-1]["event_id"] if items else after_id,
            "items": items,
        }

    @app.post("/v1/retention/tasks", status_code=201)
    def create_retention_task(
        command: RetentionTaskCreate,
        request: Request,
    ) -> dict[str, Any]:
        repository = _repository(request)
        prediction = repository.by_id.get(command.prediction_id)
        if prediction is None:
            raise HTTPException(status_code=404, detail="prediction not found")
        band = RiskPolicy().classify(float(prediction["churn_probability"]))
        try:
            return request.app.state.task_store.create(
                prediction_id=command.prediction_id,
                customer_key=prediction["customer_key"],
                model_sha256=repository.model_sha256,
                risk_band=band.value,
                owner=command.owner.strip(),
                playbook=(command.playbook or DEFAULT_PLAYBOOKS[band]).strip(),
            )
        except ValueError as error:
            raise HTTPException(status_code=409, detail=str(error)) from error

    @app.patch("/v1/retention/tasks/{task_id}/status")
    def update_retention_task(
        task_id: str,
        command: RetentionTaskStatusUpdate,
        request: Request,
    ) -> dict[str, Any]:
        try:
            return request.app.state.task_store.update_status(task_id, command.status)
        except KeyError as error:
            raise HTTPException(status_code=404, detail="retention task not found") from error

    @app.patch("/v1/retention/tasks/{task_id}/owner")
    def update_retention_task_owner(
        task_id: str,
        command: RetentionTaskOwnerUpdate,
        request: Request,
    ) -> dict[str, Any]:
        try:
            return request.app.state.task_store.update_owner(task_id, command.owner)
        except KeyError as error:
            raise HTTPException(status_code=404, detail="retention task not found") from error
        except ValueError as error:
            raise HTTPException(status_code=422, detail=str(error)) from error

    @app.patch("/v1/retention/tasks/bulk-owner")
    def update_retention_task_owners(
        command: RetentionTaskBulkOwnerUpdate,
        request: Request,
    ) -> dict[str, Any]:
        try:
            unique_ids = list(dict.fromkeys(command.task_ids))
            before = {task_id: request.app.state.task_store.get(task_id) for task_id in unique_ids}
            items = request.app.state.task_store.update_owners(command.task_ids, command.owner)
        except KeyError as error:
            raise HTTPException(status_code=404, detail="retention task not found") from error
        except ValueError as error:
            raise HTTPException(status_code=422, detail=str(error)) from error
        normalized_owner = command.owner.strip()
        changed_count = sum(before[item["task_id"]]["owner"] != normalized_owner for item in items)
        return {
            "count": len(items), "changed_count": changed_count,
            "unchanged_count": len(items) - changed_count,
            "owner": normalized_owner, "items": items,
        }

    @app.patch("/v1/retention/tasks/bulk-status")
    def update_retention_task_statuses(
        command: RetentionTaskBulkStatusUpdate,
        request: Request,
    ) -> dict[str, Any]:
        try:
            unique_ids = list(dict.fromkeys(command.task_ids))
            before = {task_id: request.app.state.task_store.get(task_id) for task_id in unique_ids}
            items = request.app.state.task_store.update_statuses(command.task_ids, command.status)
        except KeyError as error:
            raise HTTPException(status_code=404, detail="retention task not found") from error
        except ValueError as error:
            raise HTTPException(status_code=422, detail=str(error)) from error
        changed_count = sum(before[item["task_id"]]["status"] != command.status for item in items)
        return {
            "count": len(items), "changed_count": changed_count,
            "unchanged_count": len(items) - changed_count,
            "status": command.status, "items": items,
        }

    @app.get("/v1/retention/tasks/{task_id}/events")
    def retention_task_events(task_id: str, request: Request) -> dict[str, Any]:
        try:
            items = request.app.state.task_store.events(task_id)
        except KeyError as error:
            raise HTTPException(status_code=404, detail="retention task not found") from error
        return {"task_id": task_id, "count": len(items), "items": items}

    app.mount("/console/assets", StaticFiles(directory=STATIC_DIRECTORY), name="console-assets")

    return app


app = create_app()
