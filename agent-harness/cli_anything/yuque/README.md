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
cli-anything-yuque repo tree --repo owner/book-slug --json

cli-anything-yuque export run \
  --repo https://www.yuque.com/owner/book-slug \
  --format markdown \
  --all \
  --download-images \
  --json
```

`--repo-id 123` remains supported for compatibility. `--repo` accepts exactly `owner/book-slug` or a Yuque repository URL; direct targets are resolved without first querying the common-used repository list.

Use `repo list --source favorites --json` to enumerate only favorites-page cards explicitly identified as knowledge bases. Document favorites and their owning knowledge bases are intentionally excluded.

The option is disabled by default. Successful HTTP(S) images are stored beside each document in `<document>.assets/`; failed images keep their original URL and do not mark the document export as failed. The JSON response includes an `image_localization` summary. Other export formats reject this option.
