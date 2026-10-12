from ..slicing import register_format_provider, register_slicing_provider
from .adapter import LaminusSlicingProvider
from .format import LaminusFormatProvider

register_slicing_provider("laminus", LaminusSlicingProvider)
register_format_provider("laminus", LaminusFormatProvider)

__all__ = ["LaminusFormatProvider", "LaminusSlicingProvider"]
