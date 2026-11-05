"""The reviewed fixed manifest is packaged data; never trust an importer's manifest."""

from importlib.resources import files

from thoth.domain.model_asset import ModelAssetManifest
from thoth.domain.retrieval_embedding import GRANITE_ASSET_PREPROCESSING, DenseModelPin


def granite_manifest() -> ModelAssetManifest:
    raw = files("thoth.adapters.embeddings").joinpath("granite_manifest.json").read_text("utf-8")
    value = ModelAssetManifest.model_validate_json(raw)
    pin = DenseModelPin()
    model = next(f for f in value.files if f.path == "onnx/model_quint8_avx2.onnx")
    if (value.model_id, value.revision, value.preprocessing) != (
        pin.model_id,
        pin.revision,
        GRANITE_ASSET_PREPROCESSING,
    ) or model.sha256 != pin.onnx_sha256:
        raise ValueError("MODEL_MANIFEST_PIN_MISMATCH")
    return value
