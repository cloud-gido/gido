# Copyright 2026 玑渡 GIDO Contributors
# SPDX-License-Identifier: Apache-2.0
"""Versioned, typed Kafka -> Paimon pipeline definition."""
from __future__ import annotations

from enum import Enum
from typing import Any, Dict, List, Literal, Optional

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator


class PipelineMode(str, Enum):
    APPEND = "append"
    UPSERT = "upsert"
    CDC = "cdc"


class SchemaField(BaseModel):
    name: str = Field(min_length=1, max_length=256)
    data_type: str = Field(min_length=1, max_length=128)
    nullable: bool = True
    comment: Optional[str] = Field(default=None, max_length=1024)

    @field_validator("name")
    @classmethod
    def validate_name(cls, value: str) -> str:
        normalized = value.strip()
        if any(ord(char) < 32 for char in normalized):
            raise ValueError("schema field name contains control characters")
        return normalized

    @field_validator("data_type")
    @classmethod
    def validate_data_type(cls, value: str) -> str:
        import re

        normalized = value.strip().upper()
        if not re.fullmatch(r"[A-Z][A-Z0-9_ <>,()]*", normalized):
            raise ValueError("data_type contains unsupported characters")
        return normalized


class KafkaSource(BaseModel):
    connector: Literal["kafka"] = "kafka"
    connection_profile_id: int = Field(gt=0)
    topic: str = Field(min_length=1, max_length=512)
    consumer_group: str = Field(min_length=1, max_length=256)
    format: Literal["json", "debezium-json", "canal-json", "maxwell-json"] = "json"
    startup_mode: Literal["earliest-offset", "latest-offset", "group-offsets"] = "group-offsets"
    options: Dict[str, str] = Field(default_factory=dict)

    @field_validator("topic", "consumer_group")
    @classmethod
    def validate_kafka_name(cls, value: str) -> str:
        import re

        normalized = value.strip()
        if not re.fullmatch(r"[A-Za-z0-9._-]+", normalized):
            raise ValueError("Kafka topic/group may contain letters, digits, dot, underscore and hyphen")
        return normalized


class PaimonSink(BaseModel):
    connector: Literal["paimon"] = "paimon"
    connection_profile_id: int = Field(gt=0)
    database: str = Field(min_length=1, max_length=256)
    table: str = Field(min_length=1, max_length=256)
    primary_keys: List[str] = Field(default_factory=list)
    partition_keys: List[str] = Field(default_factory=list)
    options: Dict[str, str] = Field(default_factory=dict)

    @field_validator("database", "table")
    @classmethod
    def validate_identifier_text(cls, value: str) -> str:
        normalized = value.strip()
        if any(ord(char) < 32 for char in normalized):
            raise ValueError("Paimon identifier contains control characters")
        return normalized


class PipelineTransform(BaseModel):
    projections: Dict[str, str] = Field(default_factory=dict)
    filter: Optional[str] = Field(default=None, max_length=8000)

    @staticmethod
    def _safe_expression(value: str) -> str:
        import re

        expression = str(value or "").strip()
        if not expression:
            raise ValueError("transform expression cannot be empty")
        if any(token in expression for token in (";", "--", "/*", "*/")):
            raise ValueError("transform expression cannot contain statement delimiters or comments")
        if re.search(
            r"\b(CREATE|DROP|ALTER|INSERT|UPDATE|DELETE|MERGE|SET|USE|CALL|EXECUTE)\b",
            expression,
            re.I,
        ):
            raise ValueError("transform expression contains a forbidden statement keyword")
        return expression

    @field_validator("projections")
    @classmethod
    def validate_projections(cls, value: Dict[str, str]) -> Dict[str, str]:
        return {
            str(alias).strip(): cls._safe_expression(expression)
            for alias, expression in value.items()
            if str(alias).strip()
        }

    @field_validator("filter")
    @classmethod
    def validate_filter(cls, value: Optional[str]) -> Optional[str]:
        return cls._safe_expression(value) if value is not None else None


class PipelineSpec(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    spec_version: Literal["1.0"] = "1.0"
    kind: Literal["kafka_to_paimon"] = "kafka_to_paimon"
    mode: PipelineMode = PipelineMode.APPEND
    schema_evolution: Literal["strict", "additive"] = "strict"
    error_policy: Literal["fail-fast"] = "fail-fast"
    schema_contract_id: Optional[int] = Field(default=None, gt=0)
    schema_version: Optional[int] = Field(default=None, gt=0)
    source: KafkaSource
    sink: PaimonSink
    source_schema_fields: Optional[List[SchemaField]] = Field(
        default=None, alias="source_schema", min_length=1
    )
    schema_fields: List[SchemaField] = Field(alias="schema", min_length=1)
    transform: Optional[PipelineTransform] = None
    description: Optional[str] = Field(default=None, max_length=4000)

    @model_validator(mode="after")
    def validate_semantics(self) -> "PipelineSpec":
        names = [field.name for field in self.schema_fields]
        if len(names) != len(set(names)):
            raise ValueError("schema field names must be unique")
        source_names = [
            field.name for field in (self.source_schema_fields or self.schema_fields)
        ]
        if len(source_names) != len(set(source_names)):
            raise ValueError("source schema field names must be unique")
        unknown_keys = (set(self.sink.primary_keys) | set(self.sink.partition_keys)) - set(names)
        if unknown_keys:
            raise ValueError(f"sink keys are absent from schema: {sorted(unknown_keys)}")
        nullable_by_name = {field.name: field.nullable for field in self.schema_fields}
        nullable_primary_keys = [
            name for name in self.sink.primary_keys if nullable_by_name.get(name)
        ]
        if nullable_primary_keys:
            raise ValueError(
                f"Paimon primary key fields must be NOT NULL: {nullable_primary_keys}"
            )
        if self.mode in (PipelineMode.UPSERT, PipelineMode.CDC) and not self.sink.primary_keys:
            raise ValueError(f"{self.mode.value} mode requires sink.primary_keys")
        if self.sink.primary_keys and not set(self.sink.partition_keys).issubset(
            set(self.sink.primary_keys)
        ):
            raise ValueError(
                "Paimon primary-key tables require every partition key in sink.primary_keys"
            )
        if self.mode == PipelineMode.CDC and self.source.format == "json":
            raise ValueError("cdc mode requires a CDC envelope format")
        if self.schema_version is not None and self.schema_contract_id is None:
            raise ValueError("schema_version requires schema_contract_id")
        if self.transform and self.transform.projections:
            aliases = set(self.transform.projections)
            if aliases != set(names):
                raise ValueError(
                    "transform.projections must define every target schema field exactly once"
                )
        return self


def parse_pipeline_spec(value: Any) -> PipelineSpec:
    if isinstance(value, PipelineSpec):
        return value
    return PipelineSpec.model_validate(value)
