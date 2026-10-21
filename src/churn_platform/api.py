"""Prediction and retention operations API with model and batch lineage."""

from __future__ import annotations

from contextlib import asynccontextmanager
from datetime import datetime
import hashlib
import json
import math
import os
from pathlib import Path
import tomllib
from typing import Annotated, Any, Literal

from fastapi import FastAPI, HTTPException, Query, Request
from fastapi.responses import FileResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, ConfigDict, Field, model_validator

from .inference import canonical_sha256, load_model
from .feature_registry import FeatureRegistry
from .operations import DEFAULT_PLAYBOOKS, RiskBand, RiskPolicy, portfolio_summary, priority_customers
from .retention_store import RetentionTaskStore


STATIC_DIRECTORY = Path(__file__).resolve().parent / "static"
PROJECT_ROOT = Path(__file__).resolve().parents[2]
PROJECT_VERSION = str(tomllib.loads((PROJECT_ROOT / "pyproject.toml").read_text(encoding="utf-8"))["project"]["version"])


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


class RetentionOutcomeUpsert(BaseModel):
    model_config = ConfigDict(extra="forbid")

    contact_result: Literal["REACHED", "NO_RESPONSE", "INVALID_CONTACT", "DECLINED"]
    contact_channel: Literal["PHONE", "SMS", "EMAIL", "IN_APP", "OTHER"]
    customer_response: Literal["POSITIVE", "NEUTRAL", "NEGATIVE", "UNKNOWN"]
    contacted_at: datetime
    observed_at: datetime | None = None
    retained_7d: bool | None = None
    retained_30d: bool | None = None
    note: str | None = Field(default=None, max_length=500)

    @model_validator(mode="after")
    def validate_observation_window(self) -> "RetentionOutcomeUpsert":
        if self.observed_at is not None and self.observed_at < self.contacted_at:
            raise ValueError("observed_at must not be earlier than contacted_at")
        return self


class RetentionTaskBulkOwnerUpdate(BaseModel):
    model_config = ConfigDict(extra="forbid")
    task_ids: list[str] = Field(min_length=1, max_length=100)
    owner: str = Field(min_length=1, max_length=80)


class RetentionTaskBulkStatusUpdate(BaseModel):
    model_config = ConfigDict(extra="forbid")
    task_ids: list[str] = Field(min_length=1, max_length=100)
    status: Literal["OPEN", "IN_PROGRESS", "COMPLETED", "DISMISSED"]


class RetentionTaskBulkPreview(BaseModel):
    model_config = ConfigDict(extra="forbid")
    task_ids: list[str] = Field(min_length=1, max_length=100)
    owner: str | None = Field(default=None, min_length=1, max_length=80)
    status: Literal["OPEN", "IN_PROGRESS", "COMPLETED", "DISMISSED"] | None = None

    @model_validator(mode="after")
    def require_operation(self) -> "RetentionTaskBulkPreview":
        if self.owner is None and self.status is None:
            raise ValueError("owner or status is required")
        if self.owner is not None and not self.owner.strip():
            raise ValueError("owner must not be blank")
        return self


class RetentionTaskBulkCommand(RetentionTaskBulkPreview):
    """Validated combined bulk command used by the v2 operations workflow."""

    operator_note: str | None = Field(default=None, max_length=200)

    @model_validator(mode="after")
    def normalize_note(self) -> "RetentionTaskBulkCommand":
        if self.operator_note is not None and not self.operator_note.strip():
            raise ValueError("operator_note must not be blank")
        return self


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
        version=PROJECT_VERSION,
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
            "retention_outcomes": request.app.state.task_store.outcome_summary(),
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

    @app.get("/v3/retention/strategies")
    def retention_strategies(request: Request) -> dict[str, Any]:
        items = request.app.state.task_store.strategy_summary()
        return {"strategy_count": len(items), "items": items}

    @app.get("/v3/retention/sla")
    def retention_sla(
        request: Request,
        target_hours: Annotated[int, Query(ge=1, le=168)] = 24,
    ) -> dict[str, Any]:
        return request.app.state.task_store.task_sla_summary(target_hours=target_hours)

    @app.get("/v3/retention/outcomes")
    def retention_outcomes(
        request: Request,
        limit: Annotated[int, Query(ge=1, le=500)] = 500,
    ) -> dict[str, Any]:
        items = request.app.state.task_store.list_outcomes(limit=limit)
        return {"count": len(items), "limit": limit, "items": items}

    @app.get("/v3/retention/outcomes/summary")
    def retention_outcome_summary(request: Request) -> dict[str, Any]:
        return request.app.state.task_store.outcome_summary()

    @app.get("/v3/retention/outcomes/by-strategy")
    def retention_outcomes_by_strategy(request: Request) -> dict[str, Any]:
        items = request.app.state.task_store.strategy_effectiveness()
        return {"strategy_count": len(items), "items": items}

    @app.get("/v3/retention/outcomes/by-model-risk")
    def retention_outcomes_by_model_risk(request: Request) -> dict[str, Any]:
        items = request.app.state.task_store.model_effectiveness()
        return {"segment_count": len(items), "items": items}

    @app.get("/v3/retention/priority-queue")
    def retention_priority_queue(
        request: Request, limit: Annotated[int, Query(ge=1, le=500)] = 50
    ) -> dict[str, Any]:
        items = request.app.state.task_store.priority_queue(limit=limit)
        return {"count": len(items), "items": items}

    @app.get("/v3/retention/tasks/{task_id}/next-action")
    def retention_next_action(task_id: str, request: Request) -> dict[str, Any]:
        try:
            return request.app.state.task_store.next_action(task_id)
        except KeyError as error:
            raise HTTPException(status_code=404, detail="retention task not found") from error

    @app.get("/v3/retention/outcomes/confidence")
    def retention_outcome_confidence(request: Request) -> dict[str, Any]:
        return request.app.state.task_store.outcome_confidence()

    @app.get("/v3/retention/capacity")
    def retention_capacity(
        request: Request,
        target_per_owner: Annotated[int, Query(ge=1, le=500)] = 20,
    ) -> dict[str, Any]:
        items = request.app.state.task_store.owner_capacity(target_per_owner=target_per_owner)
        return {"owner_count": len(items), "target_per_owner": target_per_owner, "items": items}

    @app.get("/v3/retention/tasks/{task_id}/contact-window")
    def retention_contact_window(
        task_id: str, request: Request,
        local_hour: Annotated[int, Query(ge=0, le=23)],
    ) -> dict[str, Any]:
        try:
            return request.app.state.task_store.contact_window(task_id, local_hour=local_hour)
        except KeyError as error:
            raise HTTPException(status_code=404, detail="retention task not found") from error

    @app.get("/v3/retention/tasks/{task_id}/contact-policy")
    def retention_contact_policy(
        task_id: str, request: Request,
        consent_status: Literal["GRANTED", "UNKNOWN", "WITHDRAWN"],
        contacts_7d: Annotated[int, Query(ge=0, le=100)],
        requested_channel: Literal["PHONE", "SMS", "EMAIL", "IN_APP"],
        maximum_contacts_7d: Annotated[int, Query(ge=1, le=100)] = 3,
        preferred_channels: Annotated[list[Literal["PHONE", "SMS", "EMAIL", "IN_APP"]] | None, Query()] = None,
    ) -> dict[str, Any]:
        try:
            return request.app.state.task_store.contact_policy(
                task_id, consent_status=consent_status, contacts_7d=contacts_7d,
                maximum_contacts_7d=maximum_contacts_7d,
                requested_channel=requested_channel,
                preferred_channels=list(preferred_channels or []),
            )
        except KeyError as error:
            raise HTTPException(status_code=404, detail="retention task not found") from error

    @app.get("/v3/retention/tasks/{task_id}/offer-budget")
    def retention_offer_budget(
        task_id: str, request: Request,
        customer_value: Annotated[float, Query(ge=0)],
        expected_retention_uplift: Annotated[float, Query(ge=0, le=1)],
        offer_cost: Annotated[float, Query(gt=0)],
        remaining_campaign_budget: Annotated[float, Query(ge=0)],
        minimum_roi: Annotated[float, Query(gt=0, le=100)] = 1.2,
    ) -> dict[str, Any]:
        try:
            return request.app.state.task_store.offer_budget(
                task_id, customer_value=customer_value,
                expected_retention_uplift=expected_retention_uplift,
                offer_cost=offer_cost,
                remaining_campaign_budget=remaining_campaign_budget,
                minimum_roi=minimum_roi,
            )
        except KeyError as error:
            raise HTTPException(status_code=404, detail="retention task not found") from error

    @app.get("/v3/retention/queue-forecast")
    def retention_queue_forecast(
        request: Request,
        handling_rate_per_hour: Annotated[int, Query(ge=1, le=500)],
        horizon_hours: Annotated[int, Query(ge=1, le=168)] = 8,
    ) -> dict[str, Any]:
        return request.app.state.task_store.queue_forecast(handling_rate_per_hour=handling_rate_per_hour, horizon_hours=horizon_hours)

    @app.get("/v3/retention/strategy-guardrails")
    def retention_strategy_guardrails(
        request: Request,
        minimum_observations: Annotated[int, Query(ge=1, le=10000)] = 30,
    ) -> dict[str, Any]:
        items = request.app.state.task_store.strategy_guardrails(minimum_observations=minimum_observations)
        return {"strategy_count": len(items), "items": items}

    @app.get("/v3/retention/daily-briefing")
    def retention_daily_briefing(
        request: Request,
        handling_rate_per_hour: Annotated[int, Query(ge=1, le=500)] = 5,
        horizon_hours: Annotated[int, Query(ge=1, le=168)] = 8,
    ) -> dict[str, Any]:
        return request.app.state.task_store.daily_briefing(handling_rate_per_hour=handling_rate_per_hour, horizon_hours=horizon_hours)

    @app.get("/v3/retention/tasks/{task_id}/outcome")
    def retention_task_outcome(task_id: str, request: Request) -> dict[str, Any]:
        try:
            return request.app.state.task_store.get_outcome(task_id)
        except KeyError as error:
            raise HTTPException(status_code=404, detail="retention outcome not found") from error

    @app.put("/v3/retention/tasks/{task_id}/outcome")
    def record_retention_task_outcome(
        task_id: str,
        command: RetentionOutcomeUpsert,
        request: Request,
    ) -> dict[str, Any]:
        try:
            return request.app.state.task_store.upsert_outcome(
                task_id,
                contact_result=command.contact_result,
                contact_channel=command.contact_channel,
                customer_response=command.customer_response,
                contacted_at=command.contacted_at.isoformat(),
                observed_at=command.observed_at.isoformat() if command.observed_at else None,
                retained_7d=command.retained_7d,
                retained_30d=command.retained_30d,
                note=command.note,
            )
        except KeyError as error:
            raise HTTPException(status_code=404, detail="retention task not found") from error
        except ValueError as error:
            raise HTTPException(status_code=422, detail=str(error)) from error

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

    @app.post("/v1/retention/tasks/bulk-preview")
    def preview_retention_task_bulk_update(
        command: RetentionTaskBulkPreview,
        request: Request,
    ) -> dict[str, Any]:
        unique_ids = list(dict.fromkeys(command.task_ids))
        try:
            items = [request.app.state.task_store.get(task_id) for task_id in unique_ids]
        except KeyError as error:
            raise HTTPException(status_code=404, detail="retention task not found") from error
        normalized_owner = command.owner.strip() if command.owner is not None else None
        changed_items = [
            item for item in items
            if (normalized_owner is not None and item["owner"] != normalized_owner)
            or (command.status is not None and item["status"] != command.status)
        ]
        changed_by_risk_band = {
            band.value: sum(item["risk_band"] == band.value for item in changed_items)
            for band in RiskBand
        }
        changed_by_status = {
            status: sum(item["status"] == status for item in changed_items)
            for status in ("OPEN", "IN_PROGRESS", "COMPLETED", "DISMISSED")
        }
        changes = []
        for item in changed_items:
            changed_fields = []
            if normalized_owner is not None and item["owner"] != normalized_owner:
                changed_fields.append("owner")
            if command.status is not None and item["status"] != command.status:
                changed_fields.append("status")
            changes.append({
                "task_id": item["task_id"],
                "risk_band": item["risk_band"],
                "changed_fields": changed_fields,
                "before": {"owner": item["owner"], "status": item["status"]},
                "after": {
                    "owner": normalized_owner if normalized_owner is not None else item["owner"],
                    "status": command.status if command.status is not None else item["status"],
                },
            })
        confirmation_reasons = []
        if any(item["risk_band"] in {"CRITICAL", "HIGH"} for item in changed_items):
            confirmation_reasons.append("HIGH_RISK_TASKS")
        if command.status in {"COMPLETED", "DISMISSED"}:
            confirmation_reasons.append("TERMINAL_STATUS")
        if normalized_owner is not None and command.status is not None:
            confirmation_reasons.append("MULTI_FIELD_CHANGE")
        return {
            "count": len(items),
            "changed_count": len(changed_items),
            "unchanged_count": len(items) - len(changed_items),
            "owner_changed_count": sum(
                normalized_owner is not None and item["owner"] != normalized_owner for item in items
            ),
            "status_changed_count": sum(
                command.status is not None and item["status"] != command.status for item in items
            ),
            "changed_by_risk_band": changed_by_risk_band,
            "changed_by_status": changed_by_status,
            "changes": changes,
            "impact": {
                "field_change_count": sum(len(change["changed_fields"]) for change in changes),
                "source_owner_count": len({item["owner"] for item in changed_items}),
                "target_owner": normalized_owner,
                "target_status": command.status,
            },
            "requires_explicit_confirmation": bool(confirmation_reasons),
            "confirmation_reasons": confirmation_reasons,
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

    @app.patch("/v2/retention/tasks/bulk")
    def update_retention_tasks(
        command: RetentionTaskBulkCommand,
        request: Request,
    ) -> dict[str, Any]:
        try:
            unique_ids = list(dict.fromkeys(command.task_ids))
            before = {task_id: request.app.state.task_store.get(task_id) for task_id in unique_ids}
            previous_event_id = request.app.state.task_store.latest_event_id()
            items = request.app.state.task_store.update_many(
                command.task_ids, owner=command.owner, status=command.status
            )
        except KeyError as error:
            raise HTTPException(status_code=404, detail="retention task not found") from error
        except ValueError as error:
            raise HTTPException(status_code=422, detail=str(error)) from error
        owner_changed = sum(before[item["task_id"]]["owner"] != item["owner"] for item in items)
        status_changed = sum(before[item["task_id"]]["status"] != item["status"] for item in items)
        changed_count = sum(
            before[item["task_id"]]["owner"] != item["owner"]
            or before[item["task_id"]]["status"] != item["status"]
            for item in items
        )
        changes = []
        for item in items:
            previous = before[item["task_id"]]
            changed_fields = [
                field for field in ("owner", "status")
                if previous[field] != item[field]
            ]
            if changed_fields:
                changes.append({
                    "task_id": item["task_id"],
                    "changed_fields": changed_fields,
                    "before": {field: previous[field] for field in changed_fields},
                    "after": {field: item[field] for field in changed_fields},
                })
        latest_event_id = request.app.state.task_store.latest_event_id()
        receipt_payload = json.dumps({
            "task_ids": sorted(unique_ids),
            "owner": command.owner.strip() if command.owner is not None else None,
            "status": command.status,
            "first_new_event_id": previous_event_id + 1 if latest_event_id > previous_event_id else None,
            "latest_event_id": latest_event_id,
            "operator_note": command.operator_note.strip() if command.operator_note else None,
        }, ensure_ascii=False, separators=(",", ":"), sort_keys=True)
        receipt_id = "RET-" + hashlib.sha256(receipt_payload.encode("utf-8")).hexdigest()[:12].upper()
        risk_band_counts = {
            band: sum(item.get("risk_band") == band for item in items)
            for band in ("CRITICAL", "HIGH", "WATCH", "STABLE")
        }
        high_priority_count = risk_band_counts["CRITICAL"] + risk_band_counts["HIGH"]
        follow_up = {
            "required": changed_count > 0,
            "review_priority": "NONE" if changed_count == 0 else "P1" if high_priority_count > 0 else "P2",
            "next_action": (
                "REVIEW_NO_CHANGE_SELECTION" if changed_count == 0
                else "CONTACT_HIGH_PRIORITY_CUSTOMERS" if high_priority_count > 0
                else "MONITOR_TASK_PROGRESS"
            ),
            "review_window_hours": None if changed_count == 0 else 4 if high_priority_count > 0 else 24,
        }
        target_owner = command.owner.strip() if command.owner else "UNCHANGED"
        handoff_summary = (
            f"{receipt_id} · {changed_count}/{len(items)} tasks changed · "
            f"{high_priority_count} high priority · {target_owner} · {follow_up['next_action']}"
        )
        handoff_checklist = ["REVIEW_SELECTION"]
        if follow_up["required"]:
            handoff_checklist = ["VERIFY_AUDIT_RANGE", "CONTACT_TARGET_OWNER"]
            if high_priority_count > 0:
                handoff_checklist.append("CONTACT_HIGH_PRIORITY_CUSTOMERS")
            handoff_checklist.append("SCHEDULE_REVIEW")
        if not follow_up["required"]:
            handoff_readiness = "SELECTION_REVIEW"
            handoff_blockers = ["NO_EFFECTIVE_CHANGE"]
        elif high_priority_count > 0:
            handoff_readiness = "ESCALATION_REQUIRED"
            handoff_blockers = ["HIGH_PRIORITY_CONTACT_REQUIRED"]
        else:
            handoff_readiness = "READY_FOR_FOLLOW_UP"
            handoff_blockers = []
        return {
            "count": len(items),
            "selection": {
                "input_count": len(command.task_ids),
                "unique_count": len(unique_ids),
                "duplicate_count": len(command.task_ids) - len(unique_ids),
            },
            "changed_count": changed_count,
            "unchanged_count": len(items) - changed_count,
            "owner_changed_count": owner_changed,
            "status_changed_count": status_changed,
            "audit": {
                "receipt_id": receipt_id,
                "events_created_count": latest_event_id - previous_event_id,
                "first_new_event_id": previous_event_id + 1 if latest_event_id > previous_event_id else None,
                "latest_event_id": latest_event_id,
                "operator_note": command.operator_note.strip() if command.operator_note else None,
            },
            "impact": {
                "risk_band_counts": risk_band_counts,
                "high_priority_count": high_priority_count,
            },
            "follow_up": follow_up,
            "handoff": {
                "summary": handoff_summary,
                "target_owner": target_owner,
                "requires_review": follow_up["required"],
                "checklist": handoff_checklist,
                "step_count": len(handoff_checklist),
                "estimated_minutes": len(handoff_checklist) * 5,
                "readiness": handoff_readiness,
                "blockers": handoff_blockers,
            },
            "changes": changes,
            "items": items,
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
