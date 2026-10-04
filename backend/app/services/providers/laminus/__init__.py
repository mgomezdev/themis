from ..slicing import register_slicing_provider
from .adapter import LaminusSlicingProvider

register_slicing_provider("laminus", LaminusSlicingProvider)

__all__ = ["LaminusSlicingProvider"]
