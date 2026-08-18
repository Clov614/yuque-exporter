# cli_anything.yuque

Harness package for `cli-anything-yuque`.

## Install

```bash
python -m pip install -e ./agent-harness
```

## Quick check

```bash
cli-anything-yuque project info
cli-anything-yuque project paths --json
```

## JSON mode

Use `--json` to return machine-consumable envelopes.

```bash
cli-anything-yuque --json session init --profile default
```

## Offline Markdown images

Add `--download-images` to `export run` or `export batch` when using Markdown:

```bash
cli-anything-yuque export run \
  --repo-id 123 \
  --format markdown \
  --all \
  --download-images \
  --json
```

The option is disabled by default. Successful HTTP(S) images are stored beside each document in `<document>.assets/`; failed images keep their original URL and do not mark the document export as failed. The JSON response includes an `image_localization` summary. Other export formats reject this option.
