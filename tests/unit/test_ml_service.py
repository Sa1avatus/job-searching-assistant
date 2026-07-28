from ml_service.main import _EMBEDDING_MODEL_FILES, _RERANKER_MODEL_FILES


def test_matching_model_download_excludes_unused_formats() -> None:
    assert "pytorch_model.bin" in _EMBEDDING_MODEL_FILES
    assert "model.safetensors" in _RERANKER_MODEL_FILES
    assert not any(path.startswith("onnx/") for path in _EMBEDDING_MODEL_FILES)
    assert not any(path.startswith("assets/") for path in _RERANKER_MODEL_FILES)
