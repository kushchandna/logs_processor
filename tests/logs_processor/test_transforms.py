import re

from logs_processor.transforms import redact, replace_pattern, transform_blocks, truncate


def test_transform_blocks_applies_to_each_block():
    blocks = [["a"], ["b"]]
    assert list(transform_blocks(blocks, lambda b: [x.upper() for x in b])) == [["A"], ["B"]]


def test_replace_pattern_single_pattern():
    t = replace_pattern([r"\d{3}-\d{4}"])
    assert t(["call 555-1234 now\n"]) == ["call [REDACTED] now\n"]


def test_replace_pattern_multiple_patterns():
    t = replace_pattern([r"\d{3}-\d{4}", r"[\w.]+@[\w.]+"])
    assert t(["phone 555-1234 email a@b.com\n"]) == ["phone [REDACTED] email [REDACTED]\n"]


def test_replace_pattern_custom_replacement():
    t = replace_pattern([r"secret"], replacement="***")
    assert t(["the secret value\n"]) == ["the *** value\n"]


def test_replace_pattern_precompiled_pattern():
    t = replace_pattern([re.compile(r"ERROR")])
    assert t(["ERROR: boom\n"]) == ["[REDACTED]: boom\n"]


def test_truncate_short_line_unchanged():
    t = truncate(20)
    assert t(["short line\n"]) == ["short line\n"]


def test_truncate_long_line_gets_marker():
    t = truncate(5)
    assert t(["1234567890\n"]) == ["12345...\n"]


def test_truncate_preserves_trailing_newline():
    t = truncate(10)
    assert t(["1234567890extra\n"]) == ["1234567890...\n"]


def test_truncate_custom_marker():
    t = truncate(5, marker="[cut]")
    assert t(["1234567890\n"]) == ["12345[cut]\n"]


def test_redact_defaults_all_categories():
    t = redact()
    line = "contact a@b.com or 555-123-4567 from 10.0.0.1\n"
    assert t([line]) == ["contact [EMAIL] or [PHONE] from [IP]\n"]


def test_redact_no_matches_unchanged():
    t = redact()
    assert t(["nothing sensitive here\n"]) == ["nothing sensitive here\n"]


def test_redact_exclude_email():
    t = redact(exclude_email=True)
    assert t(["a@b.com and 10.0.0.1\n"]) == ["a@b.com and [IP]\n"]


def test_redact_exclude_phone():
    t = redact(exclude_phone=True)
    assert t(["555-123-4567 and 10.0.0.1\n"]) == ["555-123-4567 and [IP]\n"]


def test_redact_exclude_ip():
    t = redact(exclude_ip=True)
    assert t(["a@b.com and 10.0.0.1\n"]) == ["[EMAIL] and 10.0.0.1\n"]


def test_redact_exclude_all_is_no_op():
    t = redact(exclude_email=True, exclude_phone=True, exclude_ip=True)
    line = "a@b.com 555-123-4567 10.0.0.1\n"
    assert t([line]) == [line]


def test_redact_custom_email_pattern():
    t = redact(email_pattern=r"internal-\w+", exclude_phone=True, exclude_ip=True)
    assert t(["user internal-bob logged in\n"]) == ["user [EMAIL] logged in\n"]


def test_redact_custom_replacement():
    t = redact(email_replacement="[REDACTED_EMAIL]", exclude_phone=True, exclude_ip=True)
    assert t(["a@b.com\n"]) == ["[REDACTED_EMAIL]\n"]
