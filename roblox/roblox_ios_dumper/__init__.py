"""Roblox iOS Mach-O dumper.

The package intentionally has no dependency on the Android ELF dumper. Shared
algorithms can be extracted later only after Android regression fixtures exist.
"""

from .macho import MachOError, MachOImage

__all__ = ["MachOError", "MachOImage"]
__version__ = "0.1.0"
