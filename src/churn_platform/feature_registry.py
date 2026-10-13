"""Versioned feature registry and privacy-safe offline/online parity checks."""

from __future__ import annotations

from dataclasses import asdict, dataclass
from decimal import Decimal, InvalidOperation
import hashlib
import json
from pathlib import Path
from typing import Any, Iterable, Literal

from .inference import canonical_sha256
from .training import FEATURE_COLUMNS


ValueType = Literal["integer", "decimal"]


@dataclass(frozen=True)
class FeatureDefinition:
    name: str
    value_type: ValueType
    online_enabled: bool
    description: str


@dataclass(frozen=True)
class FeatureRegistry:
    registry_version: str
    entity_key: str
    timestamp_key: str
    owner: str
    features: tuple[FeatureDefinition, ...]
    sha256: str

    @classmethod
    def from_file(cls, path: Path) -> "FeatureRegistry":
        try:
            document = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as error:
            raise ValueError(f"cannot load feature registry: {path}") from error
        if not isinstance(document, dict) or not isinstance(document.get("features"), list):
            raise ValueError("feature registry must contain a features list")
        definitions: list[FeatureDefinition] = []
        for row in document["features"]:
            try:
                definition = FeatureDefinition(
                    name=str(row["name"]),
                    value_type=row["value_type"],
                    online_enabled=bool(row["online_enabled"]),
                    description=str(row["description"]),
                )
            except (KeyError, TypeError) as error:
                raise ValueError("feature definition is incomplete") from error
            if definition.value_type not in ("integer", "decimal"):
                raise ValueError(f"unsupported feature type: {definition.value_type}")
            definitions.append(definition)
        names = [item.name for item in definitions]
        if len(names) != len(set(names)):
            raise ValueError("feature names must be unique")
        if tuple(names) != FEATURE_COLUMNS:
            raise ValueError("registry feature order must match the model contract")
        for key in ("registry_version", "entity_key", "timestamp_key", "owner"):
            if not isinstance(document.get(key), str) or not document[key].strip():
                raise ValueError(f"registry {key} must be non-empty")
        return cls(
            registry_version=document["registry_version"],
            entity_key=document["entity_key"],
            timestamp_key=document["timestamp_key"],
            owner=document["owner"],
            features=tuple(definitions),
            sha256=canonical_sha256(document),
        )

    def to_public_dict(self) -> dict[str, Any]:
        return {
            "registry_version": self.registry_version,
            "registry_sha256": self.sha256,
            "entity_key": self.entity_key,
            "timestamp_key": self.timestamp_key,
            "owner": self.owner,
            "feature_count": len(self.features),
            "features": [asdict(item) for item in self.features],
        }


def load_ndjson(path: Path) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    with path.open(encoding="utf-8") as handle:
        for line_number, line in enumerate(handle, start=1):
            if not line.strip():
                continue
            try:
                row = json.loads(line)
            except json.JSONDecodeError as error:
                raise ValueError(f"invalid JSON at line {line_number}: {path.name}") from error
            if not isinstance(row, dict):
                raise ValueError(f"row {line_number} must be an object: {path.name}")
            rows.append(row)
    return rows


def compare_feature_sets(
    offline_rows: Iterable[dict[str, Any]],
    online_rows: Iterable[dict[str, Any]],
    registry: FeatureRegistry,
    *,
    decimal_tolerance: Decimal = Decimal("0.000001"),
    max_examples: int = 20,
) -> dict[str, Any]:
    if decimal_tolerance < 0:
        raise ValueError("decimal_tolerance must not be negative")
    if not 0 <= max_examples <= 100:
        raise ValueError("max_examples must be between zero and 100")

    def index(rows: Iterable[dict[str, Any]], source: str) -> dict[tuple[str, str], dict[str, Any]]:
        indexed: dict[tuple[str, str], dict[str, Any]] = {}
        required = {
            registry.entity_key,
            registry.timestamp_key,
            *(feature.name for feature in registry.features if feature.online_enabled),
        }
        for position, row in enumerate(rows, start=1):
            missing = required - row.keys()
            if missing:
                raise ValueError(f"{source} row {position} is missing {sorted(missing)}")
            identity = (
                str(row[registry.entity_key]),
                str(row[registry.timestamp_key]),
            )
            if identity in indexed:
                raise ValueError(f"duplicate {source} feature identity")
            indexed[identity] = row
        return indexed

    offline = index(offline_rows, "offline")
    online = index(online_rows, "online")
    offline_keys = set(offline)
    online_keys = set(online)
    missing_online = sorted(offline_keys - online_keys)
    unexpected_online = sorted(online_keys - offline_keys)
    feature_mismatches = {feature.name: 0 for feature in registry.features}
    examples: list[dict[str, str]] = []
    mismatch_count = 0
    for identity in sorted(offline_keys & online_keys):
        for feature in registry.features:
            if not feature.online_enabled:
                continue
            matches = _values_match(
                offline[identity][feature.name],
                online[identity][feature.name],
                feature.value_type,
                decimal_tolerance,
            )
            if matches:
                continue
            mismatch_count += 1
            feature_mismatches[feature.name] += 1
            if len(examples) < max_examples:
                identity_material = f"{identity[0]}:{identity[1]}".encode("utf-8")
                examples.append(
                    {
                        "identity_hash": hashlib.sha256(identity_material).hexdigest()[:16],
                        "feature": feature.name,
                        "offline_value": str(offline[identity][feature.name]),
                        "online_value": str(online[identity][feature.name]),
                    }
                )
    missing_hashes = [_identity_hash(item) for item in missing_online[:max_examples]]
    unexpected_hashes = [_identity_hash(item) for item in unexpected_online[:max_examples]]
    status = "PASS" if not missing_online and not unexpected_online and not mismatch_count else "FAIL"
    return {
        "report_version": "1.0",
        "status": status,
        "registry_version": registry.registry_version,
        "registry_sha256": registry.sha256,
        "offline_row_count": len(offline),
        "online_row_count": len(online),
        "matched_identity_count": len(offline_keys & online_keys),
        "missing_online_count": len(missing_online),
        "unexpected_online_count": len(unexpected_online),
        "mismatch_count": mismatch_count,
        "feature_mismatch_counts": feature_mismatches,
        "missing_online_identity_hashes": missing_hashes,
        "unexpected_online_identity_hashes": unexpected_hashes,
        "mismatch_examples": examples,
    }


def _values_match(
    offline: Any,
    online: Any,
    value_type: ValueType,
    tolerance: Decimal,
) -> bool:
    try:
        if value_type == "integer":
            return Decimal(str(offline)) == Decimal(str(online)) and Decimal(str(offline)) % 1 == 0
        return abs(Decimal(str(offline)) - Decimal(str(online))) <= tolerance
    except InvalidOperation:
        return False


def _identity_hash(identity: tuple[str, str]) -> str:
    return hashlib.sha256(f"{identity[0]}:{identity[1]}".encode("utf-8")).hexdigest()[:16]
