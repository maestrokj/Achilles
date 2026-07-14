"""In-tree custom tools: drop a module here, it self-registers on rebuild.

The platform never touches this package; the canonical channel for shipping
custom tools is a pip package with an `achilles.tools` entry point
(tool-catalog.html#custom).
"""
