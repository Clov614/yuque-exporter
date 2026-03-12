# cli-anything-yuque

`cli-anything-yuque` is a stateful, scriptable harness for this repository's Yuque exporter.

## Goals

- Reuse existing core logic from `src/`.
- Provide stable machine-readable `--json` output.
- Maintain profile-isolated session state under `~/.yuque_harness/<profile>/`.

## Command groups

- `auth login|status|logout`
- `repo list|tree`
- `export run|batch`
- `session init|show|doctor`
- `project info|paths`

## Output contract

Success envelope:

```json
{"ok": true, "data": {}, "error": null, "meta": {}}
```

Failure envelope:

```json
{"ok": false, "data": null, "error": {"code": "...", "message": "..."}, "meta": {}}
```

## Exit codes

- `0`: success
- `2`: parameter error
- `3`: auth/session error
- `4`: remote API error
- `5`: download/filesystem error
- `6`: unknown error
