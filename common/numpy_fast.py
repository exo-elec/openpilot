"""Thin wrappers around numpy for common scalar/array operations."""
import numpy as np


def clip(x, lo, hi):
  return np.clip(x, lo, hi)


def interp(x, xp, fp):
  return np.interp(x, xp, fp)


def mean(x):
  return np.mean(x)
