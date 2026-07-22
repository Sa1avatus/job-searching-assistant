from __future__ import annotations

import hashlib
import struct
import zlib
from dataclasses import dataclass
from pathlib import Path

from sqlalchemy.orm import Session

from app.storage.tables import EvidenceArtifactRow, HumanActionCheckpointRow


class InvalidEvidenceArtifact(ValueError):
    pass


@dataclass(frozen=True, slots=True)
class ResolvedEvidenceArtifact:
    row: EvidenceArtifactRow
    path: Path


ALLOWED_SCREENSHOT_TYPES: dict[str, str] = {
    ".png": "image/png",
}


class EvidenceArtifactStorage:
    def __init__(self, root_directory: Path, *, max_artifact_bytes: int) -> None:
        self._root_directory = root_directory.resolve()
        self._max_artifact_bytes = max_artifact_bytes

    def register_screenshot(
        self,
        session: Session,
        *,
        checkpoint_id: str,
        storage_path: Path,
        content_type: str,
        commit: bool = True,
    ) -> EvidenceArtifactRow:
        checkpoint = session.get(HumanActionCheckpointRow, checkpoint_id)
        if checkpoint is None:
            raise InvalidEvidenceArtifact("Human-action checkpoint not found")
        resolved_path = storage_path.resolve()
        if not resolved_path.is_relative_to(self._root_directory):
            raise InvalidEvidenceArtifact("Evidence path is outside the artifact root")
        if not resolved_path.is_file():
            raise InvalidEvidenceArtifact("Evidence file does not exist")
        expected_type = ALLOWED_SCREENSHOT_TYPES.get(resolved_path.suffix.casefold())
        if expected_type is None or content_type != expected_type:
            raise InvalidEvidenceArtifact("Evidence extension and content type do not match")
        if not self._is_valid_png(resolved_path):
            raise InvalidEvidenceArtifact("Evidence PNG structure is invalid")
        size_bytes = resolved_path.stat().st_size
        if size_bytes < 1 or size_bytes > self._max_artifact_bytes:
            raise InvalidEvidenceArtifact("Evidence file is empty or exceeds the size limit")
        relative_path = resolved_path.relative_to(self._root_directory).as_posix()
        artifact = EvidenceArtifactRow(
            checkpoint_id=checkpoint_id,
            kind="screenshot",
            relative_path=relative_path,
            content_type=content_type,
            sha256=self._sha256(resolved_path),
            size_bytes=size_bytes,
        )
        session.add(artifact)
        session.flush()
        checkpoint.evidence = [*checkpoint.evidence, f"artifact:{artifact.id}"]
        if commit:
            session.commit()
        return artifact

    def resolve(self, session: Session, artifact_id: str) -> ResolvedEvidenceArtifact:
        row = session.get(EvidenceArtifactRow, artifact_id)
        if row is None:
            raise InvalidEvidenceArtifact("Evidence artifact not found")
        candidate = (self._root_directory / row.relative_path).resolve()
        if not candidate.is_relative_to(self._root_directory):
            raise InvalidEvidenceArtifact("Evidence path is outside the artifact root")
        if not candidate.is_file():
            raise InvalidEvidenceArtifact("Evidence file is unavailable")
        return ResolvedEvidenceArtifact(row=row, path=candidate)

    def delete(self, relative_path: str) -> None:
        candidate = (self._root_directory / relative_path).resolve()
        if not candidate.is_relative_to(self._root_directory):
            raise InvalidEvidenceArtifact("Evidence path is outside the artifact root")
        candidate.unlink(missing_ok=True)

    @staticmethod
    def _sha256(path: Path) -> str:
        digest = hashlib.sha256()
        with path.open("rb") as stream:
            for chunk in iter(lambda: stream.read(65_536), b""):
                digest.update(chunk)
        return digest.hexdigest()

    @staticmethod
    def _is_valid_png(path: Path) -> bool:
        content = path.read_bytes()
        if not content.startswith(b"\x89PNG\r\n\x1a\n"):
            return False
        offset = 8
        saw_header = False
        saw_end = False
        compressed_data = bytearray()
        try:
            while offset < len(content):
                length = struct.unpack(">I", content[offset : offset + 4])[0]
                chunk_type = content[offset + 4 : offset + 8]
                data_start = offset + 8
                data_end = data_start + length
                checksum_end = data_end + 4
                if checksum_end > len(content):
                    return False
                data = content[data_start:data_end]
                expected_crc = struct.unpack(">I", content[data_end:checksum_end])[0]
                if zlib.crc32(chunk_type + data) & 0xFFFFFFFF != expected_crc:
                    return False
                if chunk_type == b"IHDR":
                    if saw_header or length != 13:
                        return False
                    width, height = struct.unpack(">II", data[:8])
                    if not 1 <= width <= 20_000 or not 1 <= height <= 20_000:
                        return False
                    saw_header = True
                elif chunk_type == b"IDAT":
                    compressed_data.extend(data)
                elif chunk_type == b"IEND":
                    saw_end = length == 0
                    break
                offset = checksum_end
            return bool(
                saw_header
                and saw_end
                and compressed_data
                and zlib.decompress(bytes(compressed_data))
            )
        except (struct.error, zlib.error):
            return False
