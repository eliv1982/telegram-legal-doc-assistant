# Synthetic demo

A small, fully **synthetic** example of one review, so the output can be seen without running the bot or calling any API.

| File | What it is |
|---|---|
| [`sample_contract.pdf`](sample_contract.pdf) | The input: a one-page fictitious supply contract with a text layer |
| [`sample_report.txt`](sample_report.txt) | The Telegram text report, exactly as the code composes it |
| [`sample_voice_script.txt`](sample_voice_script.txt) | The text that would be sent to text-to-speech (no audio is included) |
| [`sample_checklist.pdf`](sample_checklist.pdf) | The PDF checklist for the same run |
| [`sample_checklist.png`](sample_checklist.png) | A cropped preview of the checklist's first page, used in the main README |
| [`generate_demo.py`](generate_demo.py) | Regenerates all of the above |

## What is real and what is scripted

- **Real:** the contract goes through this repository's own validation, text extraction, deterministic requisite checks, evidence verification, report composition and checklist rendering.
- **Scripted:** the model's output (three findings, the report prose, the voice script, the checklist items) is written by hand in `generate_demo.py`. **No OpenAI or Telegram call is made**, and the generator does not import `config`, so no `.env` is read. The voice instruction for this scenario would be something like "check this contract for risks to the buyer".
- The three scripted findings show the three evidence outcomes: a verbatim quote (shown as a verified quotation), a paraphrase (kept as a finding but not shown as a quotation), and a finding about something missing (no quote).

## Everything is fictitious

The companies, the contract and every identifier are made up. The INNs use region code `00` and the OGRN starts with `0`; neither exists in reality, so the values are valid by checksum only and cannot belong to a real entity. The buyer's INN carries a deliberate one-digit typo, so the report shows a failed check. The sample has no real user, chat, token or document data.

## Regenerate

From the repository root (the preview image needs Poppler):

```bash
uv run python -m docs.demo.generate_demo
```

`tests/test_demo_assets.py` regenerates the demo in memory and fails if the committed text samples or the PDFs' text differ, so the committed files cannot silently go stale.
