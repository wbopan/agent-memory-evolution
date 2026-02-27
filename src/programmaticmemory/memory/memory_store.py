"""XML memory store operations.

Pure utility module for parsing, serializing, validating, and editing
memory entries stored in XML format.
"""

import re
from dataclasses import dataclass

# Match <entry key="...">content</entry> where content cannot contain another <entry tag,
# but CAN contain the literal text "</entry>" as part of content. We match up to the LAST
# </entry> by using a greedy match that stops at the next <entry or end of input.
_ENTRY_OPEN_PATTERN = re.compile(r'<entry\s+key="([^"]+)">', re.DOTALL)
_MEMORY_WRAPPER_PATTERN = re.compile(r"^\s*<memory>\s*(.*?)\s*</memory>\s*$", re.DOTALL)


@dataclass
class MemoryEntry:
    key: str
    content: str


@dataclass
class EditOperation:
    old_string: str
    new_string: str


def parse_memory_xml(xml_str: str) -> list[MemoryEntry]:
    """Parse memory XML string into a list of MemoryEntry objects.

    Args:
        xml_str: XML string with <memory><entry key="...">...</entry></memory> format.

    Returns:
        List of MemoryEntry objects.

    Raises:
        ValueError: If the XML is malformed (missing <memory> wrapper).
    """
    wrapper_match = _MEMORY_WRAPPER_PATTERN.match(xml_str)
    if wrapper_match is None:
        raise ValueError("Memory XML must be wrapped in <memory>...</memory> tags.")

    inner = wrapper_match.group(1)
    entries = []

    # Find all <entry key="..."> opening tags
    open_matches = list(_ENTRY_OPEN_PATTERN.finditer(inner))
    for i, open_match in enumerate(open_matches):
        key = open_match.group(1)
        content_start = open_match.end()

        # The content region extends up to the next <entry or end of inner
        if i + 1 < len(open_matches):
            search_region = inner[content_start : open_matches[i + 1].start()]
        else:
            search_region = inner[content_start:]

        # Find the last </entry> in the search region
        close_idx = search_region.rfind("</entry>")
        if close_idx == -1:
            raise ValueError(f"Missing </entry> for key {key!r}")

        content = search_region[:close_idx]
        entries.append(MemoryEntry(key=key, content=content))

    return entries


def serialize_memory(entries: list[MemoryEntry]) -> str:
    """Serialize a list of MemoryEntry objects into XML string.

    Args:
        entries: List of MemoryEntry objects.

    Returns:
        XML string with <memory><entry key="...">...</entry></memory> format.
    """
    if not entries:
        return "<memory>\n</memory>"

    parts = ["<memory>"]
    for entry in entries:
        parts.append(f'<entry key="{entry.key}">{entry.content}</entry>')
    parts.append("</memory>")
    return "\n".join(parts)


def validate_memory_xml(xml_str: str) -> bool:
    """Validate that a memory XML string is well-formed.

    Checks:
    - Has <memory>...</memory> wrapper
    - All <entry> tags have valid key attributes
    - No duplicate keys

    Args:
        xml_str: XML string to validate.

    Returns:
        True if valid.

    Raises:
        ValueError: If the XML is invalid, with a description of the problem.
    """
    wrapper_match = _MEMORY_WRAPPER_PATTERN.match(xml_str)
    if wrapper_match is None:
        raise ValueError("Memory XML must be wrapped in <memory>...</memory> tags.")

    entries = parse_memory_xml(xml_str)
    keys = [e.key for e in entries]
    seen: set[str] = set()
    for key in keys:
        if key in seen:
            raise ValueError(f"Duplicate memory key: {key!r}")
        seen.add(key)

    return True


def apply_edit(xml_str: str, edit: EditOperation) -> str:
    """Apply a find-and-replace edit to a memory XML string.

    Args:
        xml_str: The current memory XML string.
        edit: EditOperation with old_string and new_string.

    Returns:
        The updated memory XML string.

    Raises:
        ValueError: If old_string is not found, is ambiguous (>1 match),
            or the result is invalid XML.
    """
    count = xml_str.count(edit.old_string)
    if count == 0:
        raise ValueError(f"old_string not found in memory XML: {edit.old_string!r}")
    if count > 1:
        raise ValueError(f"old_string is ambiguous ({count} occurrences): {edit.old_string!r}")

    result = xml_str.replace(edit.old_string, edit.new_string, 1)
    validate_memory_xml(result)
    return result


def format_memory_as_markdown(entries: list[MemoryEntry]) -> str:
    """Convert memory entries to markdown sections for task LLM context.

    Args:
        entries: List of MemoryEntry objects.

    Returns:
        Markdown string with each entry as a section.
    """
    if not entries:
        return ""

    parts = []
    for entry in entries:
        parts.append(f"## {entry.key}\n{entry.content.strip()}")
    return "\n\n".join(parts)
