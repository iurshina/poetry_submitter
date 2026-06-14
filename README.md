# Poetry Submission Agent

Automates the Saturday ritual of finding literary journals, matching your poems to open calls, and preparing submission packages — so you can spend the time writing instead.

## What it does

1. **Logs into Chill Subs** and scrapes all currently open poetry calls.
2. **Visits each journal's website** to read recent content and submission guidelines.
3. **Matches your poems** to journals using Claude, scoring fit 1–10 and filtering out journals where a submission is already pending.
4. **Prepares a package** for each strong match: a formatted `.docx` (font, spacing, blind/named, margins all set per the journal's guidelines), a cover letter, and a bio adapted to the journal's tone.
5. **Tells you how to submit** — Submittable link, email address, or other method — so you can review and send.

Nothing is submitted automatically. You see the full package first.

## Setup

**Requirements:** Python 3.12+, [uv](https://docs.astral.sh/uv/)

```bash
git clone git@github.com:iurshina/poetry_submitter.git
cd poetry_submitter
uv sync
uv run playwright install chromium
```

Make sure [Ollama](https://ollama.com) is running and the model is available:

```bash
ollama pull qwen3:8b
ollama serve        # if not already running
```

Initialise the database:

```bash
uv run poetry-sub init
```

No Chill Subs login needed — the browse page is public.


## Adding poems

Poems live in a `poems/` directory (not committed to git). Each file is one poem; the filename is the title.

**Write a new poem in your editor:**
```bash
uv run poetry-sub new-poem "Title of poem"
```
Opens `$EDITOR`, saves to `poems/Title of poem.txt` and the database when you close it. Running the same command again updates an existing poem.

**Drop files directly:**  
Create any number of `.txt` files in `poems/`, then sync:
```bash
uv run poetry-sub sync-poems
```
Already-imported poems are skipped.

**List what's in the database:**
```bash
uv run poetry-sub poems
```

## Set your bio

The agent adapts your bio to each journal's tone, but needs a default to work from:

```bash
uv run poetry-sub set-bio \
  --short "Your ~50-word bio here." \
  --long "Your ~100-word bio here."
```

## Run the Saturday workflow

```bash
uv run poetry-sub run-saturday
```

This runs steps 1–5 above and prints:
- A ranked table of poem–journal matches with scores and reasoning
- For each high-confidence match (score ≥ 8): cover letter, bio, formatting summary, and the path to the generated `.docx`

## Data & privacy

| Path | In git | Contents |
|------|--------|----------|
| `poems/` | No | Your poem source files |
| `data/poetry.db` | No | Database: poems, submissions, journals |
| `data/chillsubs_session.json` | No | Saved browser session (replaces password) |
| `packages/` | No | Generated submission documents |

## Project layout

```
src/poetry_submitter/
├── models.py          # Poem, Journal, OpenCall, Submission, Profile
├── db.py              # SQLite via SQLModel
├── telegram.py        # Telegram JSON export parser
├── agents/
│   ├── matcher.py     # PydanticAI agent: poem–journal matching
│   └── packager.py    # PydanticAI agent: cover letter, bio, formatting
└── scrapers/
    ├── chillsubs.py   # Playwright: login + scrape open calls
    └── journals.py    # Playwright: scrape journal content + guidelines
```

**Stack:** Python · [uv](https://docs.astral.sh/uv/) · [PydanticAI](https://ai.pydantic.dev/) · [SQLModel](https://sqlmodel.tiangolo.com/) · [Playwright](https://playwright.dev/python/) · [python-docx](https://python-docx.readthedocs.io/) · [Ollama](https://ollama.com) (qwen3:8b)
