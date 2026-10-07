"""采样率与声道规整（规格书 4.2）。"""

from __future__ import annotations

from math import gcd

import numpy as np
from scipy.signal import resample_poly

__all__ = ["to_mono", "resample", "prepare_block"]


def to_mono(samples: np.ndarray) -> np.ndarray:
    """把 ``(n,)`` / ``(n, channels)`` 规整为单声道 ``(n,)`` float32。"""
    arr = np.asarray(samples, dtype=np.float32)
    if arr.ndim == 1:
        return arr
    if arr.ndim != 2:
        raise ValueError(f"samples 必须是一维或二维，收到 ndim={arr.ndim}")
    if arr.shape[1] == 1:
        return np.ascontiguousarray(arr[:, 0])
    return np.ascontiguousarray(arr.mean(axis=1, dtype=np.float32))


def prepare_block(block: np.ndarray, channels: int) -> np.ndarray:
    """把任意形状的音频块规整成 ``(n, channels)`` float32。"""
    arr = np.asarray(block, dtype=np.float32)
    if arr.ndim == 1:
        arr = arr.reshape(-1, 1)
    if arr.ndim != 2:
        raise ValueError(f"block 必须是一维或二维，收到 ndim={arr.ndim}")
    if arr.shape[1] == channels:
        return np.ascontiguousarray(arr)
    if arr.shape[1] == 1:
        return np.ascontiguousarray(np.repeat(arr, channels, axis=1))
    if channels == 1:
        return np.ascontiguousarray(arr.mean(axis=1, dtype=np.float32).reshape(-1, 1))
    raise ValueError(f"无法把 {arr.shape[1]} 声道规整为 {channels} 声道")


def resample(samples: np.ndarray, src_rate: int, dst_rate: int) -> np.ndarray:
    """多相重采样；采样率相同时返回副本。

    使用 ``scipy.signal.resample_poly``，上下采样比按最大公约数约分（规格书 4.2）。
    """
    if src_rate <= 0 or dst_rate <= 0:
        raise ValueError(f"采样率必须为正，收到 {src_rate} -> {dst_rate}")
    arr = np.asarray(samples, dtype=np.float32)
    if src_rate == dst_rate or arr.shape[0] == 0:
        return np.array(arr, dtype=np.float32, copy=True)
    divisor = gcd(int(src_rate), int(dst_rate))
    up = int(dst_rate) // divisor
    down = int(src_rate) // divisor
    out = resample_poly(arr, up, down, axis=0)
    return np.ascontiguousarray(out, dtype=np.float32)
