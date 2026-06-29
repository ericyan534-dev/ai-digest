"""Shared vector math for the processing stage (numpy, no heavy ML libs)."""

from __future__ import annotations

import numpy as np


def cosine(a: list[float], b: list[float]) -> float:
    """Cosine similarity of two equal-length vectors. Returns 0.0 if either is empty."""
    if not a or not b or len(a) != len(b):
        return 0.0
    # Sanitize non-finite components so a single bad embedding can't poison the
    # score with NaN/inf (which would silently corrupt dedup/cluster/rank).
    va = np.nan_to_num(np.asarray(a, dtype=np.float64), nan=0.0, posinf=0.0, neginf=0.0)
    vb = np.nan_to_num(np.asarray(b, dtype=np.float64), nan=0.0, posinf=0.0, neginf=0.0)
    na = float(np.linalg.norm(va))
    nb = float(np.linalg.norm(vb))
    if na == 0.0 or nb == 0.0:
        return 0.0
    return float(np.clip(np.dot(va, vb) / (na * nb), -1.0, 1.0))


def cosine_matrix(vectors: list[list[float]]) -> np.ndarray:
    """Pairwise cosine similarity matrix for a list of equal-length vectors."""
    if not vectors:
        return np.zeros((0, 0), dtype=np.float64)
    # Replace NaN/inf BEFORE normalizing — otherwise a non-finite component makes
    # the row norm NaN/inf and propagates invalid values through the matmul.
    mat = np.nan_to_num(
        np.asarray(vectors, dtype=np.float64), nan=0.0, posinf=0.0, neginf=0.0
    )
    norms = np.linalg.norm(mat, axis=1, keepdims=True)
    norms[norms == 0.0] = 1.0
    unit = mat / norms
    # `@` can emit spurious "divide by zero / overflow / invalid value encountered
    # in matmul" RuntimeWarnings from NumPy's SIMD matmul loop on some layouts even
    # when the result is exact. We already normalized + sanitize below, so silence
    # those and guarantee a finite, bounded similarity matrix.
    with np.errstate(divide="ignore", over="ignore", invalid="ignore"):
        sim = unit @ unit.T
    sim = np.nan_to_num(sim, nan=0.0, posinf=1.0, neginf=-1.0)
    return np.clip(sim, -1.0, 1.0)


def centroid(vectors: list[list[float]]) -> list[float] | None:
    """L2-normalized mean of vectors, or None if there are none."""
    if not vectors:
        return None
    mat = np.asarray(vectors, dtype=np.float64)
    mean = mat.mean(axis=0)
    norm = float(np.linalg.norm(mean))
    if norm == 0.0:
        return mean.tolist()  # type: ignore[no-any-return]
    return (mean / norm).tolist()  # type: ignore[no-any-return]


__all__ = ["cosine", "cosine_matrix", "centroid"]
