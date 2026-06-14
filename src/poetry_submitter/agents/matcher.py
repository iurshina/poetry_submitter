"""Agent that matches poems to open journal calls."""
from dataclasses import dataclass

from pydantic import BaseModel, field_validator
from pydantic_ai import Agent

from poetry_submitter.agents.model import get_model
from poetry_submitter.models import Journal, OpenCall, Poem, Submission


@dataclass
class MatchDeps:
    poems: list[Poem]
    journals: list[Journal]
    open_calls: list[OpenCall]
    past_submissions: list[Submission]


class PoemJournalMatch(BaseModel):
    poem_title: str
    journal_name: str
    score: int  # 1-10
    reasoning: str
    already_submitted: bool

    @field_validator("score")
    @classmethod
    def normalise_score(cls, v: int) -> int:
        # Local models sometimes score 1-100; map back to 1-10
        if v > 10:
            return round(v / 10)
        return max(1, min(10, v))

    @field_validator("poem_title")
    @classmethod
    def must_be_known(cls, v: str) -> str:
        # Stripped for comparison — actual validation happens in find_matches
        return v.strip()


class MatchResult(BaseModel):
    matches: list[PoemJournalMatch]
    summary: str


matcher_agent = Agent(
    get_model(),
    deps_type=MatchDeps,
    output_type=MatchResult,
    system_prompt="""You are a literary submissions assistant. Your job is to match a poet's existing poems to open literary journals.

IMPORTANT RULES:
- You must ONLY use poem titles from the "THE POET'S POEMS" section below. Do not invent titles.
- You must ONLY use journal names from the "OPEN JOURNALS" section below. Do not invent journals.
- Scores must be integers from 1 to 10.
- Only return matches with score 6 or higher.
- The "PUBLISHED SAMPLES" in each journal entry are examples of what that journal publishes — they are NOT the poet's poems.

Evaluate fit based on: thematic alignment, tone/style resonance with the journal's published work, and practical constraints.""",
)


@matcher_agent.system_prompt
async def add_context(ctx) -> str:
    deps: MatchDeps = ctx.deps

    submitted_pairs = {
        (s.poem_id, s.journal_id)
        for s in deps.past_submissions
        if s.status.value == "pending"
    }

    poem_list = "\n".join(f"- {p.title}" for p in deps.poems)
    poems_text = "\n\n".join(
        f"TITLE: {p.title}\nTEXT:\n{p.body[:600]}"
        for p in deps.poems
    )

    journals_text = "\n\n".join(
        "---\n"
        f"JOURNAL NAME: {j.name}\n"
        f"VIBE: {j.aesthetic_notes or 'not available'}\n"
        f"ABOUT: {(j.guidelines_text or '')[:250]}\n"
        f"SIMULTANEOUS SUBMISSIONS: {j.simultaneous_submissions}\n"
        + (f"PUBLISHED SAMPLES (not the poet's work):\n{j.poem_samples[:400]}\n" if j.poem_samples else "")
        for j in deps.journals
    )

    pending_note = "\n".join(
        f"- poem_id={pid} already submitted to journal_id={jid} (pending)"
        for pid, jid in submitted_pairs
    ) or "none"

    return (
        f"=== THE POET'S POEMS (use ONLY these titles) ===\n"
        f"Titles available: {poem_list}\n\n"
        f"{poems_text}\n\n"
        f"=== OPEN JOURNALS (use ONLY these names) ===\n"
        f"{journals_text}\n\n"
        f"=== PENDING SUBMISSIONS (skip these) ===\n"
        f"{pending_note}"
    )


async def find_matches(deps: MatchDeps) -> MatchResult:
    known_titles = {p.title.lower() for p in deps.poems}
    known_journals = {j.name.lower() for j in deps.journals}

    result = await matcher_agent.run(
        "Match the poet's poems to the open journals. Use only the titles and journal names listed above.",
        deps=deps,
    )
    output = result.output

    # Filter out any hallucinated titles or journals the model invented
    valid = [
        m for m in output.matches
        if m.poem_title.lower() in known_titles
        and m.journal_name.lower() in known_journals
    ]
    if len(valid) < len(output.matches):
        dropped = len(output.matches) - len(valid)
        output.summary += f" ({dropped} hallucinated match(es) removed)"
    output.matches = valid

    return output
