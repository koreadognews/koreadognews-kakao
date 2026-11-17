# Newspaper layout verification

The web edition uses a centered masthead, paper background, newspaper rules,
responsive spacing, keyboard focus styles and a print stylesheet. The full-text
copy button includes article and event source URLs, excludes its own controls,
tries selection copying if Clipboard API access fails, and announces the result.

Only `render()` in `bot.py` changes. The research/validation pipeline, CLI,
dated HTML/JSON and index paths, `daily.yml`, Kakao sending, token synchronization
and delivery ledger code remain unchanged. No inferred domestic supplement is
created because the current validated schema does not retain that classification.
Issue numbers remain unset unless `VERIFIED_ISSUE_NUMBER` contains a verified
number. The issue date remains the CLI news date plus one day.

## Offline checks (2026-11-07)

- Syntax compilation: bot.py, send_once.py, sync_kakao_token.py passed.
- `python -m unittest discover -s tests -p test_render.py -v`: 7 passed.
- The same 7 tests passed against the pre-change bot.py baseline.
- AST comparison confirmed all Python code outside render() is unchanged.
- Browser checks passed in headless Microsoft Edge: copy success with all source
  URLs, denied Clipboard API fallback, failure announcement, and no horizontal
  overflow at 390px. Desktop and mobile screenshots were inspected.
- CLI tests exercised reviewed JSON generation and `--send-only` without `--send`.
  Network requests were blocked; Kakao send was asserted not called.
- Duplicate ledger and absent renewed token paths were tested with mocks.
- Workflow remains byte-for-byte unchanged: its generation command and Pages
  verification marker (`기사 기준일 YYYY-MM-DD`) are preserved. A real workflow
  was not dispatched, deployed or used to send messages.

## Pre-existing test failure

Full discovery (`python -m unittest discover -s tests -v`) cannot pass because
`tests/test_safety.py` imports missing `research_split`. This failure was present
before the layout change and is outside this PR's scope.

Fixture articles and events used during verification are examples, not news.
