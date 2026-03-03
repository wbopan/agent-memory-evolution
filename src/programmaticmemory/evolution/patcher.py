"""Thin wrapper around codex-apply-patch for applying patches to Knowledge Base Programs."""

from __future__ import annotations

from codex_apply_patch import apply_patch_in_memory

# Virtual filename used in the patch envelope — not a real file.
_VIRTUAL_FILENAME = "program.py"


def apply_patch(source_code: str, patch_body: str) -> str:
    """Apply an apply-patch-format patch to source code and return the updated source.

    Args:
        source_code: The current source code to patch.
        patch_body: The patch content between Begin/End markers, including
            the ``*** Update File: program.py`` header and hunk(s).

    Returns:
        The patched source code.

    Raises:
        RuntimeError: If the patch cannot be applied (malformed patch,
            context mismatch, etc.).
    """
    full_patch = f"*** Begin Patch\n{patch_body}*** End Patch\n"
    result = apply_patch_in_memory(full_patch, {_VIRTUAL_FILENAME: source_code})
    return result.files[_VIRTUAL_FILENAME]
