# Logs Processor

Streams a log file through a group → filter → transform pipeline without
ever loading the whole file into memory. Lines are grouped into blocks
(e.g. a multi-line stack trace grouped under the line that started it),
blocks can be filtered in or out, and surviving blocks can be transformed
(e.g. redacting secrets, truncating long lines).

`LogProcessor` is the only way to run a pipeline — a fluent builder you
configure with `add_grouping`/`add_filter`/`add_transformer`, then run with
`process()` (over any iterable of lines) or `process_file()` (streams a file
to another file):

```python
from logs_processor import (
    LogProcessor,
    timestamp_block_grouper,
    contains,
    any_of,
    redact,
)

processor = (
    LogProcessor(filter_mode="all")
    .add_grouping(timestamp_block_grouper())
    .add_filter(any_of(contains("ERROR"), contains("WARN")))
    .add_filter(contains("payments"))
    .add_transformer(redact())  # redacts emails/phones/IPs by default
)
processor.process_file("app.log", "app.processed.log")
```

- `add_grouping(...)` can be called multiple times; a line continues the
  current block if **any** added grouping predicate says to continue. If
  none are added, every line is its own block.
- `add_filter(...)` can be called multiple times; how they combine is
  controlled by `processor.filter_mode`, a plain string: `"any"` (the
  default) ORs the added filters, `"all"` ANDs them. Use `any_of(...)`/
  `all_of(...)` inside a single `add_filter(...)` call to nest AND/OR logic
  independent of that mode.
- `add_transformer(...)` applies transforms in the order added.

Built-in filters: `equal_to`, `contains`, `matches`, `starts_with`, `ends_with`,
`within_time_ranges`.
Built-in groupings: `timestamp_block_grouper` (see
[Timestamps](#timestamps-timestamp_format-and-time-ranges)),
`indent_continuation_grouper`.
Built-in transforms:
- `redact(...)` — redacts email/phone/IPv4 by default, each replaced with a
  category-tagged placeholder (`[EMAIL]`, `[PHONE]`, `[IP]`). Disable a
  category with `exclude_email`/`exclude_phone`/`exclude_ip`, or override its
  pattern/placeholder with `email_pattern`/`email_replacement` (same for
  `phone_`/`ip_`).
- `replace_pattern(patterns, replacement="[REDACTED]")` — generic regex
  substitution for anything not covered by `redact`'s built-in categories.
- `truncate(max_length, marker="...")` — caps line length.

All filters/groupings/transforms are plain callables, so you can pass your
own alongside or instead of the built-ins.

## Timestamps: `timestamp_format` and time ranges

A log's timestamps are described **once**, by the processor's
`timestamp_format` — a `strptime` format string. Both the timestamp grouping
and the time ranges work off it:

```python
processor = (
    LogProcessor(timestamp_format="%Y-%m-%d %H:%M:%S")
    .add_grouping(timestamp_block_grouper())   # entries start where a timestamp is
    .add_time_range("2026-08-01 10:00:00", "2026-08-01 10:05:00")
    .add_time_range("2026-08-01 18:00:00", "2026-08-01 18:30:00")
    .add_filter(contains("ERROR"))
)
```

**Leave `timestamp_format` unset and it is detected** from the log's first 20
lines, by trying the formats in `KNOWN_TIMESTAMP_FORMATS` (ISO 8601 with or
without offset/fractional seconds, `log4j`'s comma variant, `YYYY/MM/DD`,
nginx/Apache `01/Aug/2026:10:00:00 +0000`, syslog `Aug  1 10:00:00`, logcat
`08-01 10:00:00.123`) and keeping the one that reads the most of those lines:

```python
LogProcessor().add_grouping(timestamp_block_grouper()).add_time_range(
    start="08-01 10:00:00.000"   # bounds are written in the log's own format
)
```

Detection is per-`process()` call and reads at most 20 lines before handing
them back to the pipeline, so streaming is preserved. If nothing in the
pipeline reads timestamps, no detection happens. If something does and no
known format fits, `process()` raises a `ValueError` rather than silently
matching nothing — set `timestamp_format` explicitly for logs it can't
place (deliberately undetectable: time-only timestamps, and `%d/%m/%Y` vs
`%m/%d/%Y`, which can't be told apart).

Details worth knowing:

- A block is kept if its timestamp falls in **any** added range (OR), and
  ranges are always **AND**ed with the filters added via `add_filter`,
  whatever `filter_mode` says — a range narrows what the filters can let
  through rather than being one more way in. The pipeline above keeps ERROR
  entries logged in either window.
- Both ends are **inclusive**; pass only `start` or only `end` for an
  open-ended range. Bounds may be `datetime`s, `date`s, or strings in
  `timestamp_format` — string bounds are parsed when `process()` runs (i.e.
  against the detected format), and validated straight away if
  `timestamp_format` is already set.
- A block's timestamp is the first one found in its lines — normally the line
  that opened the block, so a stack trace stays attached to its error line
  and is kept or dropped with it. The timestamp doesn't have to be at the
  start of the line: `INFO [2026-08-01 10:00:00] ...` works, for grouping as
  well as for ranges.
- Blocks with no readable timestamp anywhere are dropped by a time range. To
  keep them (log preambles, banner lines), skip `add_time_range` and add the
  underlying filter yourself: `add_filter(within_time_ranges(ranges, fmt,
  keep_untimestamped=True))`.
- `timestamp_block_grouper(pattern=...)` overrides the format with a regex of
  your own for grouping only — useful when entries start with something that
  isn't a timestamp at all (`^\[LOG\]`). Used outside a `LogProcessor`, where
  there's no format to bind to, an unbound grouping falls back to
  `DEFAULT_TIMESTAMP_PATTERN`.
- A year-less format (syslog, logcat) parses into year 1900, so ranges over
  such a log compare month/day/time only — write the bounds in the same
  year-less format.

## Config files

Instead of building a `LogProcessor` in Python, you can describe a pipeline
as YAML and load it with `LogProcessor.from_config(path)`. This is handy for
reusable, named profiles — e.g. a `claude_logs_on_android.yaml` you keep
around and reuse on any log dump from that source, without writing Python
each time.

```yaml
filter_mode: all             # "any" (default) or "all"
timestamp_format: '%Y-%m-%d %H:%M:%S'   # optional; detected from the log if absent

groupings:                    # optional, OR-combined (same as add_grouping)
  - type: timestamp           # groups by timestamp_format's timestamps
    pattern: '^\d{4}-\d{2}-\d{2}'   # optional regex, overrides that

filters:                      # optional, combined per filter_mode
  - type: contains
    substring: ERROR
  - type: contains
    substring: payments

time_ranges:                  # optional, OR-combined, ANDed with filters
  - start: '2026-08-01 10:00:00'   # both ends inclusive
    end: '2026-08-01 10:05:00'
  - start: '2026-08-01 18:00:00'   # end omitted = open-ended

transforms:                   # optional, applied in order
  - type: redact
    exclude_ip: true
  - type: truncate
    max_length: 2000
```

```python
from logs_processor import LogProcessor

processor = LogProcessor.from_config("claude_logs_on_android.yaml")
processor.process_file("app.log", "app.processed.log")
```

A `time_ranges` entry takes only `start`/`end` (either may be omitted, but not
both), each an unquoted YAML timestamp or a string in `timestamp_format`.
Every key besides `type` in a groupings/filters/transforms entry is passed
straight through as a keyword argument to the matching built-in function, so
the accepted fields are exactly that function's parameters:

| Section | `type` values | Maps to |
|---|---|---|
| `groupings` | `timestamp`, `indent` | `timestamp_block_grouper`, `indent_continuation_grouper` |
| `filters` | `equal_to`, `contains`, `matches`, `starts_with`, `ends_with` | the matching filter function |
| `time_ranges` | *(no `type`)* | `add_time_range(start, end)` |
| `transforms` | `redact`, `replace_pattern`, `truncate` | the matching transform function |

An unknown `type` raises a `ValueError` listing the valid options for that
section. Nested `any_of`/`all_of` logic isn't supported in YAML (use
`filter_mode` for the common any/all case, or build the pipeline in Python
directly for anything more complex). The `LogProcessor` returned by
`from_config` is a normal, fully mutable instance — you can still call
`add_grouping`/`add_filter`/`add_transformer`/`add_time_range` on it
afterward.

### Naming convention

Name config files `<subject>_<context>.yaml`, snake_case, describing what
the logs are and where they come from — e.g. `claude_logs_on_android.yaml`,
`nginx_access_prod.yaml`, `payment_service_staging.yaml`. Config files can
live anywhere; there's no fixed directory.


## Development

```bash
python3 -m venv .venv
source .venv/bin/activate
python -m pip install --upgrade pip
python -m pip install -e ".[dev]"
pytest
```