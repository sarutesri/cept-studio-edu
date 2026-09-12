"""OpenDSS adapter facade.

The adapter implementation lives in :mod:`cept.adapters.opendss.adapter`;
engine-specific network, dynamics, and study concerns remain in their
dedicated sibling modules.
"""

from .adapter import OpenDSSAdapter

__all__ = ["OpenDSSAdapter"]
