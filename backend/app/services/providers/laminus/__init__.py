from ..slicing import register_format_provider
from .adapter import LaminusSlicingProvider
from .format import LaminusFormatProvider

register_format_provider("laminus", LaminusFormatProvider)

__all__ = ["LaminusFormatProvider", "LaminusSlicingProvider"]
