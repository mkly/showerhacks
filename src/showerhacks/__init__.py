"""Showermaster application entry point."""

from importlib import import_module
import os

# The app does not play sound; keep OpenAL from probing PipeWire on import.
os.environ.setdefault("ALSOFT_DRIVERS", "null")
main = import_module("showerhacks.app").main

__all__ = ["main"]
