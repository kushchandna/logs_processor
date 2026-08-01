from __future__ import annotations

import re
from typing import Callable, Iterable, Iterator

BlockTransform = Callable[[list[str]], list[str]]
"""transform(block) -> new list of lines replacing the block's lines."""


def transform_blocks(
    blocks: Iterable[list[str]],
    transform: BlockTransform,
) -> Iterator[list[str]]:
    for block in blocks:
        yield transform(block)


def replace_pattern(
    patterns: list[str | re.Pattern],
    replacement: str = "[REDACTED]",
) -> BlockTransform:
    """Replace every regex match in every line of the block with replacement."""
    compiled = [re.compile(p) if isinstance(p, str) else p for p in patterns]

    def transform(block: list[str]) -> list[str]:
        new_block = []
        for line in block:
            for pattern in compiled:
                line = pattern.sub(replacement, line)
            new_block.append(line)
        return new_block
    return transform


DEFAULT_EMAIL_PATTERN = r"[\w.+-]+@[\w-]+\.[\w.-]+"
DEFAULT_PHONE_PATTERN = r"(?:\+?1[-.\s]?)?\(?\d{3}\)?[-.\s]?\d{3}[-.\s]?\d{4}\b"
DEFAULT_IP_PATTERN = r"\b(?:\d{1,3}\.){3}\d{1,3}\b"


def redact(
    *,
    exclude_email: bool = False,
    email_pattern: str | re.Pattern = DEFAULT_EMAIL_PATTERN,
    email_replacement: str = "[EMAIL]",
    exclude_phone: bool = False,
    phone_pattern: str | re.Pattern = DEFAULT_PHONE_PATTERN,
    phone_replacement: str = "[PHONE]",
    exclude_ip: bool = False,
    ip_pattern: str | re.Pattern = DEFAULT_IP_PATTERN,
    ip_replacement: str = "[IP]",
) -> BlockTransform:
    """Redact common categories of sensitive data (email, phone, IPv4 by
    default), each replaced with a category-tagged placeholder so it's
    clear what kind of value was removed.

    Every category is redacted unless excluded via its exclude_X flag.
    Each category's pattern and placeholder can be overridden via its
    X_pattern/X_replacement arguments. Categories are applied in a fixed
    order (email, then phone, then IP) so an email match can't later be
    re-matched by the phone or IP pattern.
    """
    transforms: list[BlockTransform] = []
    if not exclude_email:
        transforms.append(replace_pattern([email_pattern], replacement=email_replacement))
    if not exclude_phone:
        transforms.append(replace_pattern([phone_pattern], replacement=phone_replacement))
    if not exclude_ip:
        transforms.append(replace_pattern([ip_pattern], replacement=ip_replacement))

    def transform(block: list[str]) -> list[str]:
        for t in transforms:
            block = t(block)
        return block
    return transform


def truncate(max_length: int, marker: str = "...") -> BlockTransform:
    """Cap each line's content to max_length characters, appending marker
    to any line that was cut. A trailing newline, if present, is excluded
    from the length count and preserved after the marker.
    """
    def transform(block: list[str]) -> list[str]:
        new_block = []
        for line in block:
            has_newline = line.endswith("\n")
            content = line[:-1] if has_newline else line
            if len(content) > max_length:
                content = content[:max_length] + marker
            new_block.append(content + "\n" if has_newline else content)
        return new_block
    return transform
