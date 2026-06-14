"""CLI entry point."""
import asyncio
import os
import subprocess
import tempfile
from pathlib import Path

import typer
from rich.console import Console
from rich.table import Table
from sqlmodel import select

from poetry_submitter.db import get_session, init_db
from poetry_submitter.models import Journal, OpenCall, Poem, Profile, Submission

app = typer.Typer(help="Poetry submission agent")
console = Console()


@app.command()
def chillsubs_import_cookies(path: Path = typer.Argument(..., help="Path to cookies.txt exported from your browser")):
    """
    Import Chill Subs session from a cookies.txt file (Netscape format).

    How to export: log in to chillsubs.com in your real browser, then use the
    'Get cookies.txt LOCALLY' extension to export cookies for chillsubs.com.
    """
    from poetry_submitter.scrapers.chillsubs import import_cookies_from_file
    import_cookies_from_file(path)
    console.print("[green]Session imported. You can now run 'run-saturday'.[/green]")


@app.command()
def init():
    """Initialize the database."""
    init_db()
    console.print("[green]Database initialised.[/green]")
    _ensure_profile()


@app.command()
def add_poem(title: str, file: Path = typer.Option(None), text: str = typer.Option(None)):
    """Add a single poem by file or inline text."""
    if file:
        body = file.read_text(encoding="utf-8")
    elif text:
        body = text
    else:
        console.print("[red]Provide --file or --text[/red]")
        raise typer.Exit(1)

    with get_session() as session:
        poem = Poem(title=title, body=body)
        session.add(poem)
        session.commit()
        console.print(f"[green]Added poem: {title}[/green]")


POEMS_DIR = Path(__file__).parent.parent.parent / "poems"


@app.command()
def sync_poems(
    poems_dir: Path = typer.Option(None, help="Directory of .txt poem files (default: ./poems/)"),
):
    """Sync poems from the poems/ directory into the database. Skips already-imported files."""
    directory = poems_dir or POEMS_DIR
    if not directory.exists():
        console.print(f"[red]Directory not found: {directory}[/red]")
        raise typer.Exit(1)

    txt_files = sorted(directory.glob("*.txt"))
    if not txt_files:
        console.print(f"[yellow]No .txt files found in {directory}[/yellow]")
        return

    added = 0
    skipped = 0
    with get_session() as session:
        for f in txt_files:
            title = f.stem
            existing = session.exec(select(Poem).where(Poem.title == title)).first()
            if existing:
                skipped += 1
                continue
            body = f.read_text(encoding="utf-8").strip()
            if not body:
                continue
            session.add(Poem(title=title, body=body))
            added += 1
        session.commit()

    console.print(f"[green]Synced:[/green] {added} added, {skipped} already in database.")


@app.command()
def new_poem(title: str = typer.Argument(..., help="Poem title")):
    """Open $EDITOR to write a new poem, then save it to poems/ and the database."""
    POEMS_DIR.mkdir(exist_ok=True)
    out_file = POEMS_DIR / f"{title}.txt"
    if out_file.exists():
        console.print(f"[yellow]{out_file} already exists — editing existing file.[/yellow]")

    editor = os.environ.get("EDITOR", "nano")
    with tempfile.NamedTemporaryFile(suffix=".txt", mode="w", delete=False, encoding="utf-8") as tmp:
        if out_file.exists():
            tmp.write(out_file.read_text(encoding="utf-8"))
        tmp_path = Path(tmp.name)

    subprocess.call([editor, str(tmp_path)])

    body = tmp_path.read_text(encoding="utf-8").strip()
    tmp_path.unlink()

    if not body:
        console.print("[red]Empty — nothing saved.[/red]")
        raise typer.Exit(1)

    out_file.write_text(body + "\n", encoding="utf-8")

    with get_session() as session:
        existing = session.exec(select(Poem).where(Poem.title == title)).first()
        if existing:
            existing.body = body
            console.print(f"[green]Updated:[/green] {title}")
        else:
            session.add(Poem(title=title, body=body))
            console.print(f"[green]Added:[/green] {title}")
        session.commit()


@app.command()
def poems():
    """List all tracked poems."""
    with get_session() as session:
        all_poems = session.exec(select(Poem)).all()

    table = Table("ID", "Title", "Tags", "Lines")
    for p in all_poems:
        table.add_row(str(p.id), p.title, p.tags or "", str(len(p.body.splitlines())))
    console.print(table)


@app.command()
def run_saturday():
    """Run the full Saturday workflow: scrape open calls, match poems, prep packages."""
    asyncio.run(_saturday_workflow())


async def _saturday_workflow():
    from poetry_submitter.agents.matcher import MatchDeps, find_matches
    from poetry_submitter.agents.packager import PackageDeps, build_document, draft_package
    from poetry_submitter.scrapers.chillsubs import scrape_open_calls
    from poetry_submitter.scrapers.journals import scrape_journal

    init_db()
    console.rule("[bold]Saturday Submission Run[/bold]")

    # 1. Scrape Chill Subs
    console.print("\n[bold cyan]Step 1 — Fetching open poetry calls from Chill Subs[/bold cyan]")
    open_calls_raw = await scrape_open_calls()
    console.print(f"  Found [bold]{len(open_calls_raw)}[/bold] open calls with poetry.")
    if open_calls_raw:
        for oc in open_calls_raw:
            deadline = f"closes {oc.closes}" if oc.closes else "rolling"
            console.print(f"  · [cyan]{oc.journal_name}[/cyan] ({deadline})")

    # 2. Upsert journals + open calls into DB
    console.print(f"\n[bold cyan]Step 2 — Scraping journal details[/bold cyan]")
    with get_session() as session:
        journals: list[Journal] = []
        open_calls: list[OpenCall] = []

        for oc in open_calls_raw:
            journal = session.exec(
                select(Journal).where(Journal.chillsubs_id == oc.chillsubs_id)
            ).first()
            if not journal:
                journal = Journal(
                    name=oc.journal_name,
                    url=oc.journal_url,
                    chillsubs_id=oc.chillsubs_id,
                    simultaneous_submissions=oc.simultaneous_submissions,
                    response_time_days=oc.response_time_days,
                )
                session.add(journal)
                session.flush()

            if not journal.aesthetic_notes:
                console.print(f"  Scraping [cyan]{journal.name}[/cyan]...")
                try:
                    profile = await scrape_journal(oc.chillsubs_id)
                    journal.aesthetic_notes = profile.aesthetic_notes
                    journal.poem_samples = "\n---\n".join(profile.poem_samples) if profile.poem_samples else None
                    journal.guidelines_text = profile.guidelines_text
                    journal.submission_url = profile.submission_url or journal.submission_url
                    journal.submission_email = profile.submission_email or journal.submission_email
                    journal.simultaneous_submissions = profile.accepts_simultaneous
                    journal.guidelines_url = profile.guidelines_url or journal.guidelines_url

                    status_parts = []
                    if profile.submission_url:
                        status_parts.append(f"submit via {profile.submission_url[:40]}…")
                    if profile.accepts_pdf:
                        status_parts.append("PDF")
                    if profile.accepts_docx:
                        status_parts.append("DOCX")
                    if profile.poetry_fee:
                        status_parts.append(f"fee {profile.poetry_fee}")
                    if profile.poem_samples:
                        status_parts.append(f"{len(profile.poem_samples)} poem sample(s) found")
                    else:
                        status_parts.append("no poem samples")
                    console.print(f"    [dim]{' · '.join(status_parts) or 'scraped'}[/dim]")
                except Exception as e:
                    console.print(f"    [yellow]Could not scrape: {e}[/yellow]")
            else:
                console.print(f"  [dim]{journal.name} — cached[/dim]")

            call = OpenCall(
                journal_id=journal.id,
                closes=oc.closes,
                genre=oc.genre,
            )
            session.add(call)
            journals.append(journal)
            open_calls.append(call)

        poems = session.exec(select(Poem)).all()
        past_subs = session.exec(select(Submission)).all()
        user_profile = session.exec(select(Profile)).first()
        session.commit()

    if not poems:
        console.print("[red]No poems in database. Run 'sync-poems' or 'add-poem' first.[/red]")
        return

    console.print(f"\n  Poems in database: {', '.join(p.title for p in poems)}")

    # 3. Match poems to journals
    console.print(f"\n[bold cyan]Step 3 — Matching {len(poems)} poem(s) against {len(journals)} journal(s)[/bold cyan]")
    console.print("  Asking the model to evaluate fit… (this may take a moment)")
    deps = MatchDeps(
        poems=list(poems),
        journals=journals,
        open_calls=open_calls,
        past_submissions=list(past_subs),
    )
    matches = await find_matches(deps)

    console.print(f"  Got [bold]{len(matches.matches)}[/bold] match(es).")

    console.rule("[bold]Match Results[/bold]")
    if matches.summary:
        console.print(f"[dim]{matches.summary}[/dim]\n")

    table = Table("Poem", "Journal", "Score", "Reasoning")
    for m in sorted(matches.matches, key=lambda x: x.score, reverse=True):
        flag = " [yellow](pending)[/yellow]" if m.already_submitted else ""
        score_color = "green" if m.score >= 8 else "yellow" if m.score >= 6 else "red"
        table.add_row(
            m.poem_title,
            m.journal_name,
            f"[{score_color}]{m.score}/10[/{score_color}]",
            m.reasoning[:90] + flag,
        )
    console.print(table)

    # 4. Prep packages for top matches
    top = [m for m in matches.matches if m.score >= 8 and not m.already_submitted]
    if not top:
        console.print("\n[yellow]No high-confidence matches (score ≥ 8) this week.[/yellow]")
        return

    console.print(f"\n[bold cyan]Step 4 — Preparing {len(top)} submission package(s)[/bold cyan]")
    output_dir = Path("packages") / str(typer.get_app_dir("poetry-submitter"))

    with get_session() as session:
        for match in top:
            poem = session.exec(select(Poem).where(Poem.title == match.poem_title)).first()
            journal = session.exec(select(Journal).where(Journal.name == match.journal_name)).first()
            profile = session.exec(select(Profile)).first()
            if not poem or not journal or not profile:
                console.print(f"  [yellow]Skipping {match.poem_title} → {match.journal_name} (not found in DB)[/yellow]")
                continue

            console.print(f"  Drafting package: [bold]{poem.title}[/bold] → [bold]{journal.name}[/bold]")
            pkg_deps = PackageDeps(poem=poem, journal=journal, profile=profile)
            pkg = await draft_package(pkg_deps)

            fmt = pkg.formatting
            doc_path = build_document(poem, profile, fmt, output_dir / journal.name)
            console.print(f"    Document: {doc_path}")

            console.rule(f"[bold]{poem.title}[/bold] → [bold]{journal.name}[/bold]")
            if journal.submission_url:
                console.print(f"Submit at: [link]{journal.submission_url}[/link]")
            if journal.submission_email:
                console.print(f"Email: {journal.submission_email}")
            console.print(
                f"Format: {fmt.font_name} {fmt.font_size_pt}pt · "
                f"{fmt.line_spacing.value} spacing · "
                f"{'blind' if fmt.blind else 'named'} · "
                f"{fmt.file_format.value.upper()}"
                + (f" · max {fmt.max_poems_per_submission} poems" if fmt.max_poems_per_submission else "")
            )
            if fmt.extra_notes:
                console.print(f"Note: [dim]{fmt.extra_notes}[/dim]")
            console.print(f"\n[bold]Cover letter[/bold]\n{pkg.cover_letter}")
            console.print(f"\n[bold]Bio[/bold]\n{pkg.bio}")
            console.rule()


def _ensure_profile():
    with get_session() as session:
        profile = session.exec(select(Profile)).first()
        if not profile:
            profile = Profile(name="Anastasiia Iurshina", email="anastasiia.iurshina@gmail.com")
            session.add(profile)
            session.commit()
            console.print("[dim]Default profile created. Edit with 'set-bio'.[/dim]")


@app.command()
def set_bio(short: str = typer.Option(None), long: str = typer.Option(None)):
    """Update your default bio."""
    with get_session() as session:
        profile = session.exec(select(Profile)).first()
        if not profile:
            profile = Profile(name="Anastasiia Iurshina", email="anastasiia.iurshina@gmail.com")
            session.add(profile)
        if short:
            profile.default_bio_short = short
        if long:
            profile.default_bio_long = long
        session.commit()
    console.print("[green]Bio updated.[/green]")


def main():
    app()
