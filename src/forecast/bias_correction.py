from __future__ import annotations


def calculate_bias_offsets(
    historical_weights: dict[str, dict[str, float | None]],
    shrinkage: float = 0.75,
    max_abs_c: float = 2.0,
) -> dict[str, float]:
    offsets: dict[str, float] = {}
    for model, metrics in historical_weights.items():
        bias_candidates = [metrics.get("bias_30"), metrics.get("bias_14"), metrics.get("bias_7")]
        bias_values = [float(value) for value in bias_candidates if value is not None]
        if not bias_values:
            offsets[model] = 0.0
            continue
        bias = bias_values[0]
        corrected = max(-max_abs_c, min(max_abs_c, bias * shrinkage))
        offsets[model] = corrected
    return offsets

