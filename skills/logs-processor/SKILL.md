---
name: logs-processor
description: Process, filter, group, or redact log files in this repo (context_utils) using the logs_processor library instead of writing ad-hoc regex/awk/grep scripts. Use this whenever the task involves reading a log file and doing any of: grouping multi-line entries (stack traces, indented continuations), filtering log lines/blocks by content (errors, warnings, a specific service, a keyword), keeping only entries logged inside one or more time windows ("logs between 10:00 and 10:05", "around the outage", "just yesterday's entries"), redacting sensitive data (emails, phone numbers, IPs, or custom patterns) before sharing logs, truncating long lines, or writing a cleaned/processed copy of a log file. Also use it if the user asks to "parse a log file", "clean up logs", "extract errors from a log", "prepare a log for an agent/LLM", mentions log files being too large to load into memory, or wants a reusable/named log-processing profile defined in a YAML config file (e.g. "make a config for android logs", "create a claude_logs_on_android.yaml"). Do not use for structured logs already in JSON/CSV (use normal parsing) or for one-off single-line greps that don't need grouping/streaming.
---

# logs_processor skill

Use this skill instead of writing custom regex/awk/grep scripts whenever you
need to process a log file in this repo. `logs_processor` is a small, tested
Python library that streams a log file through a **group → filter → transform**
pipeline without ever loading the whole file into memory — important for large
log files. Reference docs live at `src/logs_processor/README.md`; this skill
is the fast path to using it correctly without re-reading the source.

## Why use this instead of ad-hoc scripting

Log files often have multi-line entries (a stack trace under one timestamped
line), need filtering by content rather than by line number, and may contain
data you shouldn't paste into a prompt or share verbatim (emails, phone
numbers, IPs). `logs_processor` handles all three concerns — grouping,
filtering, redaction — with a few lines of code, streaming so it's safe on
files too large to read in one go.

## Setup

The library lives at `src/logs_processor/` in this repo and is not published
to PyPI. From the repo root, with the project's venv active:

```bash
source .venv/bin/activate   # created via: python3 -m venv .venv && pip install -e ".[dev]"
python -c "from logs_processor import LogProcessor"  # sanity check
```

If there's no `.venv` yet, create one and install the project editable
(`pip install -e ".[dev]"`) before using the library — see the root
`README.md`. If you're just running a one-off script without activating the
venv, you can also run it with `PYTHONPATH=src python your_script.py` from
the repo root.

## Core concept: `LogProcessor` is the only entry point

Everything goes through the `LogProcessor` builder. Construct one, chain
`add_grouping` / `add_filter` / `add_transformer` calls (each returns `self`),
then call `process()` (over any iterable of lines) or `process_file()`
(streams one file to another):

```python
from logs_processor import LogProcessor, timestamp_block_grouper, contains, redact

processor = (
    LogProcessor()
    .add_grouping(timestamp_block_grouper())
    .add_filter(contains("ERROR"))
    .add_transformer(redact())
)
processor.process_file("app.log", "app.processed.log")
```

A **block** is one or more consecutive lines treated as a unit (e.g. an
error line plus its stack trace). Grouping decides how lines become blocks;
filtering keeps or drops whole blocks; transforming rewrites the lines of a
surviving block. If you never call `add_grouping`, every line is its own
block — grouping is optional, filtering and transforming still work fine on
single-line blocks.

## Step 1: Grouping — decide what counts as one log entry

Two built-in groupers, or write your own:

- **`timestamp_block_grouper()`** — a line carrying a timestamp starts a new
  block; any line without one is a continuation of the previous block. This
  is the right choice for logs where every real entry is timestamped and
  continuation lines (stack traces, multi-line messages) aren't. Which
  timestamps count comes from the processor's `timestamp_format`, which is
  auto-detected if you don't set it (see "Filtering by time window" below) —
  so you normally call this with no arguments and never write a regex.
  `timestamp_block_grouper(pattern=...)` overrides that with your own regex
  for logs whose entries start with something that isn't a timestamp
  (`^\[LOG\]`).

- **`indent_continuation_grouper()`** — a line starting with a space or tab
  continues the previous block; anything else (including blank lines) starts
  a new block. Use this for logs where continuation lines are indented
  rather than timestamp-less.

- **Custom grouping** — pass any callable with signature
  `(current_block: list[str], previous_line: str, current_line: str) -> bool`,
  returning `True` if `current_line` continues `current_block`.

Call `add_grouping(...)` more than once to combine strategies — a line
continues the block if **any** added grouping predicate says to continue
(OR combination). There's no AND option for groupings; if you need more
complex logic, write one custom predicate that encodes it.

```python
LogProcessor().add_grouping(timestamp_block_grouper())          # single strategy
LogProcessor().add_grouping(timestamp_block_grouper()).add_grouping(indent_continuation_grouper())  # either can trigger continuation
```

## Step 2: Filtering — keep or drop whole blocks

Filters are **block-level**: a filter predicate receives the whole block
(`list[str]`) and returns `True` to keep it, `False` to drop it. There's no
line-level filtering — if you need to drop individual lines within a
surviving block, do that in a transformer instead.

Built-in filters (all match if **any line in the block** satisfies the
condition):

| Filter | Matches when... |
|---|---|
| `equal_to(value)` | a line equals `value` exactly (note: lines from a file include the trailing `\n`) |
| `contains(substring)` | a line contains `substring` |
| `matches(pattern)` | a line matches a regex via `re.search` (string or compiled pattern) |
| `starts_with(prefix)` | a line starts with `prefix` |
| `ends_with(suffix)` | a line ends with `suffix` (remember the trailing `\n` if matching end-of-line text) |
| `within_time_ranges(ranges, timestamp_format)` | the block's timestamp falls in one of `ranges` (see "Filtering by time window" below — normally used via `add_time_range`) |

Combine multiple filters with `add_filter(...)` calls. How they combine is
controlled by `filter_mode`, a plain string property:

- `"any"` (**default**) — block is kept if **any** added filter matches (OR).
- `"all"` — block is kept only if **every** added filter matches (AND).

```python
LogProcessor(filter_mode="any").add_filter(contains("ERROR")).add_filter(contains("WARN"))
# keeps a block if it has ERROR *or* WARN

LogProcessor(filter_mode="all").add_filter(contains("ERROR")).add_filter(contains("payments"))
# keeps a block only if it has ERROR *and* payments
```

For logic that mixes AND/OR (e.g. "(ERROR or WARN) and payments"), use the
`any_of(...)` / `all_of(...)` combinators to build one compound predicate
inside a single `add_filter(...)` call — this nesting works independent of
`filter_mode`:

```python
from logs_processor import any_of, contains

LogProcessor(filter_mode="all").add_filter(
    any_of(contains("ERROR"), contains("WARN"))
).add_filter(contains("payments"))
# (ERROR or WARN) and payments
```

`filter_mode` can also be changed after construction (`processor.filter_mode = "all"`)
— it's read each time `process()`/`process_file()` runs, so changing it and
re-running reprocesses with the new combination logic.

### Filtering by time window

To keep only entries logged during one or more windows (e.g. "the two
minutes around the outage, plus the restart at 18:00"), call
`add_time_range(start, end)` once per window:

```python
processor = (
    LogProcessor()
    .add_grouping(timestamp_block_grouper())
    .add_time_range("2026-08-01 10:00:00", "2026-08-01 10:02:00")
    .add_time_range("2026-08-01 18:00:00")      # open-ended: 18:00 onwards
    .add_filter(contains("ERROR"))
)
```

- Ranges OR together (a block is kept if it falls in **any** of them), and
  the whole set is **AND**ed with the `add_filter` filters regardless of
  `filter_mode` — a range narrows what filters can let through, it's never
  one more way in. The example keeps ERROR entries from either window.
- Both ends are **inclusive**; omit `start` or `end` for an open-ended range
  (omitting both is an error). Bounds are `datetime`s or strings **written in
  the log's own timestamp format**, whatever that is (`"08-01 10:00:00.000"`
  for logcat, `"01/Aug/2026:10:00:00 +0000"` for nginx).
- A block's timestamp is the first one found in its lines — usually the line
  that opened the block, so a stack trace is kept or dropped along with its
  error line. The timestamp needn't be at the start of the line
  (`INFO [2026-08-01 10:00:00] ...` works).
- Blocks with no readable timestamp are **dropped**. To keep them (banner
  lines, preambles), skip `add_time_range` and add the underlying filter
  directly: `add_filter(within_time_ranges([(start, end)], fmt, keep_untimestamped=True))`.

#### `timestamp_format`: one description of the log's timestamps

`timestamp_format` is a **`strptime` format string** (not a regex) —
`"%Y-%m-%d %H:%M:%S"`, `"%m-%d %H:%M:%S.%f"`, ... It is the pipeline's single
description of the log's timestamps: `add_time_range` parses bounds with it,
and `timestamp_block_grouper()` groups by it. Set it once, in the constructor
(`LogProcessor(timestamp_format=...)`), via `processor.timestamp_format = ...`,
or as `timestamp_format:` in a YAML config — never twice, and never as a
regex.

**Usually you don't set it at all.** If a pipeline needs timestamps and the
format isn't set, it's detected from the log's first 20 lines by trying
`KNOWN_TIMESTAMP_FORMATS` — ISO 8601 (with or without `T`, fractional
seconds, offset), log4j's `2026-08-01 10:00:00,123`, `YYYY/MM/DD`,
nginx/Apache `01/Aug/2026:10:00:00 +0000`, syslog `Aug  1 10:00:00`, logcat
`08-01 10:00:00.123` — and keeping whichever reads the most of those lines.

Set it explicitly when:
- the log's timestamps aren't in that list — notably **time-only** stamps and
  **`%d/%m/%Y` vs `%m/%d/%Y`**, both left out on purpose because guessing
  would silently mis-parse;
- `process()` raised `ValueError: Could not detect the log's timestamp
  format ...` — that's the deliberate failure mode when a pipeline needs
  timestamps and none of the known formats fit. Detection never falls back to
  "match nothing".

Detection runs per `process()` call, reads at most 20 lines and hands them
back to the pipeline (streaming is preserved), and doesn't run at all if
nothing in the pipeline reads timestamps. Setting `timestamp_format` on its
own filters nothing — ranges and groupings do the work.

## Step 3: Transforming — rewrite the lines of surviving blocks

Transformers run in the order added, each one's output feeding the next.

- **`redact(...)`** — the go-to for scrubbing sensitive data before sharing
  a log with an agent, in a bug report, etc. Redacts **email, phone, and
  IPv4 addresses by default**, each replaced with a category-tagged
  placeholder (`[EMAIL]`, `[PHONE]`, `[IP]`) so you can still see *that*
  something was removed. Turn off a category with `exclude_email=True` /
  `exclude_phone=True` / `exclude_ip=True`; override a category's pattern or
  placeholder with `email_pattern=`/`email_replacement=` (same shape for
  `phone_` and `ip_`).

  ```python
  redact()                                    # email + phone + IP, default placeholders
  redact(exclude_ip=True)                     # skip IP redaction
  redact(email_replacement="[REDACTED_EMAIL]")
  ```

- **`replace_pattern(patterns, replacement="[REDACTED]")`** — generic regex
  substitution for anything `redact()`'s built-in categories don't cover
  (API keys, internal hostnames, custom IDs, ...). `patterns` is a list of
  regex strings or compiled patterns; every match of every pattern is
  replaced with `replacement`.

  ```python
  replace_pattern([r"api_key=\w+", r"user_\d+"], replacement="[REDACTED]")
  ```

- **`truncate(max_length, marker="...")`** — caps each line's visible
  content to `max_length` characters, appending `marker` to lines that were
  cut (trailing newline is preserved, not counted toward the limit). Useful
  for capping absurdly long single-line log entries (huge JSON blobs, etc.)
  before feeding them to an LLM.

  ```python
  truncate(500)   # no line longer than ~500 chars + "..."
  ```

- **Custom transform** — any callable `(block: list[str]) -> list[str]`.

## Step 4: Running it

- **`processor.process(lines)`** — `lines` is any iterable of strings (e.g.
  an open file object, a list, a generator). Returns a lazy iterator of
  blocks (`list[str]`) — nothing is computed until you iterate it.
- **`processor.process_file(input_path, output_path)`** — opens
  `input_path`, streams it line-by-line through `process()`, and writes each
  surviving/transformed block straight to `output_path` as it's produced.
  Never holds the whole file in memory — safe for large logs.

```python
# Over a file, streaming end to end:
processor.process_file("huge_app.log", "huge_app.cleaned.log")

# Over an in-memory list or any iterable, when you need the blocks in Python:
blocks = list(processor.process(["line1\n", "line2\n"]))
```

## Step 5: Reusable pipelines via YAML config

If you'll reuse the same pipeline across multiple log files (or the user
wants a named, reusable profile — e.g. "a config for processing Android logs
from the Claude app"), define it as YAML instead of Python and load it with
`LogProcessor.from_config(path)`. It returns a normal `LogProcessor` — still
fully mutable, `add_grouping`/`add_filter`/`add_transformer` all work on it
afterward.

**Naming convention**: `<subject>_<context>.yaml`, snake_case — e.g.
`claude_logs_on_android.yaml`, `nginx_access_prod.yaml`. No fixed directory;
put the config wherever makes sense (next to the logs, in the caller's repo,
etc).

**Schema** — every key is optional; every field besides `type` in an entry
is passed straight through as a keyword argument to the matching built-in
(so the accepted fields are exactly that function's Python parameters):

```yaml
filter_mode: all             # "any" (default) or "all"
timestamp_format: '%Y-%m-%d %H:%M:%S'   # strptime format; omit to auto-detect

groupings:                    # OR-combined, same as calling add_grouping repeatedly
  - type: timestamp           # or "indent"; groups by timestamp_format
    pattern: '^\d{4}-\d{2}-\d{2}'   # timestamp only; optional regex override

filters:                      # combined per filter_mode
  - type: contains            # equal_to | contains | matches | starts_with | ends_with
    substring: ERROR
  - type: contains
    substring: payments

time_ranges:                  # OR-combined with each other, ANDed with filters
  - start: '2026-08-01 10:00:00'   # both ends inclusive
    end: '2026-08-01 10:05:00'
  - start: '2026-08-01 18:00:00'   # omit end for an open-ended range

transforms:                   # applied in the order listed
  - type: redact               # redact | replace_pattern | truncate
    exclude_ip: true
  - type: truncate
    max_length: 2000
```

```python
from logs_processor import LogProcessor

processor = LogProcessor.from_config("claude_logs_on_android.yaml")
processor.process_file("app.log", "app.processed.log")
```

A `time_ranges` entry takes only `start`/`end` (no `type`); any other key
raises a `ValueError`. Unquoted YAML timestamps are fine — they come through
as `datetime`s — as are strings in `timestamp_format`.

An unknown `type` raises a `ValueError` naming the valid options for that
section. YAML doesn't support nested `any_of`/`all_of` — for logic that
mixes AND/OR, either rely on `filter_mode` for the common any/all case, or
build that part of the pipeline in Python (you can still call `add_filter`
on the object `from_config` returns).

## Full example

Drop DEBUG-level entries, keep everything else, redact PII, cap line length,
group by timestamp so stack traces stay attached to their error line:

```python
from logs_processor import LogProcessor, timestamp_block_grouper, contains, redact, truncate

processor = (
    LogProcessor()
    .add_grouping(timestamp_block_grouper())
    .add_filter(lambda block: not contains("DEBUG")(block))
    .add_transformer(redact())
    .add_transformer(truncate(2000))
)
processor.process_file("service.log", "service.context.log")
```

## Gotchas

- Lines read from a file keep their trailing `\n`. `equal_to`/`ends_with`
  and any custom exact-match logic need to account for that (e.g.
  `equal_to("done\n")`, not `equal_to("done")`).
- Filtering is block-level only — a filter sees the whole block at once, not
  individual lines. To drop specific lines *within* a kept block, write a
  custom transformer that filters `block` internally.
- `add_grouping` combines with OR; `add_filter` combines per `filter_mode`
  (default `"any"` = OR, not AND) — this is the opposite default of what
  some might expect, so double-check `filter_mode` if a filtered result
  looks larger than expected.
- If no groupings are added, every line is treated as its own block — fine
  for logs without multi-line entries, but stack traces will get split apart
  unless you add a grouping.
- With time ranges active, blocks whose lines carry no parseable timestamp
  are dropped — if the output is emptier than expected, check that
  `timestamp_format` actually matches the log's timestamps (it's a
  `strptime` format like `%Y-%m-%d %H:%M:%S`, not a regex) and that the range
  bounds are written in that same format.
- `timestamp_format` and `timestamp_block_grouper`'s `pattern` are not two
  places to say the same thing: set the format and let the grouper follow it.
  Reach for `pattern` only when entries don't start with a timestamp at all.
- A year-less format (syslog, logcat) parses into year 1900, so ranges over
  those logs compare month/day/time only — write bounds year-less too.
