from __future__ import annotations

import json
import os
import re
import uuid
from dataclasses import asdict, dataclass
from enum import StrEnum
from pathlib import Path
from typing import TypedDict, cast


class InvalidSelectorLibrary(ValueError):
    pass


class SelectorKind(StrEnum):
    LABEL = "label"
    PLACEHOLDER = "placeholder"
    ID = "id"
    NAME = "name"


@dataclass(frozen=True, slots=True)
class SelectorCandidate:
    kind: SelectorKind
    value: str

    @property
    def evidence(self) -> str:
        return f"{self.kind.value}:{self.value}"


@dataclass(frozen=True, slots=True)
class SelectorVersion:
    version: int
    kind: SelectorKind
    value: str
    is_active: bool


class SelectorLibraryPayload(TypedDict):
    schema_version: int
    adapter_name: str
    mappings: dict[str, list[dict[str, object]]]


class SelectorLibrary:
    def __init__(self, artifact_directory: Path, *, max_versions_per_field: int = 20) -> None:
        if max_versions_per_field < 2:
            raise ValueError("max_versions_per_field must be at least 2")
        self._root_directory = (artifact_directory / "selectors").resolve()
        self._max_versions_per_field = max_versions_per_field

    def candidates(self, adapter_name: str, field_id: str) -> tuple[SelectorCandidate, ...]:
        versions = self.versions(adapter_name, field_id)
        return tuple(
            SelectorCandidate(version.kind, version.value)
            for version in sorted(versions, key=lambda version: version.version, reverse=True)
            if version.is_active
        )

    def versions(self, adapter_name: str, field_id: str) -> tuple[SelectorVersion, ...]:
        payload = self._load(adapter_name)
        raw_versions = payload["mappings"].get(field_id, [])
        versions: list[SelectorVersion] = []
        for raw_version in raw_versions:
            try:
                version_number = raw_version["version"]
                kind = raw_version["kind"]
                value = raw_version["value"]
                is_active = raw_version["is_active"]
                if (
                    not isinstance(version_number, int)
                    or not isinstance(kind, str)
                    or not isinstance(value, str)
                    or not isinstance(is_active, bool)
                ):
                    raise TypeError
                versions.append(
                    SelectorVersion(
                        version=version_number,
                        kind=SelectorKind(kind),
                        value=value,
                        is_active=is_active,
                    )
                )
            except (KeyError, TypeError, ValueError) as error:
                raise InvalidSelectorLibrary("Selector version is invalid") from error
        return tuple(versions)

    def promote(
        self, adapter_name: str, field_id: str, candidate: SelectorCandidate
    ) -> SelectorVersion:
        if not field_id or not candidate.value:
            raise InvalidSelectorLibrary("Selector field and value are required")
        payload = self._load(adapter_name)
        mappings = payload["mappings"]
        raw_versions = list(mappings.get(field_id, []))
        for raw_version in raw_versions:
            raw_version["is_active"] = False
        next_version = (
            max((version.version for version in self.versions(adapter_name, field_id)), default=0)
            + 1
        )
        promoted = SelectorVersion(next_version, candidate.kind, candidate.value, True)
        raw_versions.append(asdict(promoted) | {"kind": promoted.kind.value})
        mappings[field_id] = raw_versions[-self._max_versions_per_field :]
        self._save(adapter_name, payload)
        return promoted

    def _load(self, adapter_name: str) -> SelectorLibraryPayload:
        path = self._path(adapter_name)
        if not path.exists():
            return {"schema_version": 1, "adapter_name": adapter_name, "mappings": {}}
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, UnicodeDecodeError, json.JSONDecodeError) as error:
            raise InvalidSelectorLibrary("Selector library cannot be read") from error
        if (
            not isinstance(payload, dict)
            or payload.get("schema_version") != 1
            or payload.get("adapter_name") != adapter_name
            or not isinstance(payload.get("mappings"), dict)
        ):
            raise InvalidSelectorLibrary("Selector library structure is invalid")
        return cast(SelectorLibraryPayload, payload)

    def _save(self, adapter_name: str, payload: SelectorLibraryPayload) -> None:
        destination = self._path(adapter_name)
        self._root_directory.mkdir(parents=True, exist_ok=True)
        temporary = destination.with_name(f".{destination.name}.{uuid.uuid4()}.tmp")
        try:
            with temporary.open("x", encoding="utf-8") as stream:
                json.dump(payload, stream, indent=2, sort_keys=True)
                stream.flush()
                os.fsync(stream.fileno())
            temporary.chmod(0o600)
            temporary.replace(destination)
        finally:
            temporary.unlink(missing_ok=True)

    def _path(self, adapter_name: str) -> Path:
        if re.fullmatch(r"[a-z0-9][a-z0-9_-]{0,99}", adapter_name) is None:
            raise InvalidSelectorLibrary("Adapter name is invalid")
        return self._root_directory / f"{adapter_name}.json"
