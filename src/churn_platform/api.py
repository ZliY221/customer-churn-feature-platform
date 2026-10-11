"""Read-only prediction query API with model and batch lineage."""

from __future__ import annotations

from contextlib import asynccontextmanager
import json
import math
import os
from pathlib import Path
from typing import Annotated, Any

from fastapi import FastAPI, HTTPException, Query, Request
from pydantic import BaseModel, ConfigDict

from .inference import canonical_sha256, load_model


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
    model_path: Path | None = None, predictions_path: Path | None = None
) -> FastAPI:
    configured_model = model_path or Path(os.getenv("CHURN_MODEL_PATH", "build/demo/model/baseline.json"))
    configured_predictions = predictions_path or Path(
        os.getenv("CHURN_PREDICTIONS_PATH", "build/demo/predictions/candidate.ndjson")
    )

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        app.state.repository = PredictionRepository.from_files(
            configured_model, configured_predictions
        )
        yield
        app.state.repository = None

    app = FastAPI(
        title="Customer Churn Prediction Query API",
        version="1.0.0",
        lifespan=lifespan,
    )

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

    return app


app = create_app()
