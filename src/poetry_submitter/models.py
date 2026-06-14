from datetime import date, datetime
from enum import Enum
from typing import Optional

from sqlmodel import Field, Relationship, SQLModel


class SubmissionStatus(str, Enum):
    pending = "pending"
    accepted = "accepted"
    rejected = "rejected"
    withdrawn = "withdrawn"


class SubmissionMethod(str, Enum):
    email = "email"
    submittable = "submittable"
    duotrope = "duotrope"
    other = "other"


class Poem(SQLModel, table=True):
    id: Optional[int] = Field(default=None, primary_key=True)
    title: str
    body: str
    notes: Optional[str] = None
    tags: Optional[str] = None  # comma-separated
    created_at: datetime = Field(default_factory=datetime.utcnow)
    telegram_message_id: Optional[int] = None

    submissions: list["Submission"] = Relationship(back_populates="poem")


class Journal(SQLModel, table=True):
    id: Optional[int] = Field(default=None, primary_key=True)
    name: str
    url: str
    chillsubs_id: Optional[str] = None
    submission_method: SubmissionMethod = SubmissionMethod.submittable
    submission_email: Optional[str] = None
    submission_url: Optional[str] = None
    guidelines_url: Optional[str] = None
    guidelines_text: Optional[str] = None
    aesthetic_notes: Optional[str] = None
    poem_samples: Optional[str] = None    # newline-separated excerpts of published poems
    simultaneous_submissions: bool = True
    response_time_days: Optional[int] = None
    last_scraped: Optional[datetime] = None

    submissions: list["Submission"] = Relationship(back_populates="journal")
    open_calls: list["OpenCall"] = Relationship(back_populates="journal")


class OpenCall(SQLModel, table=True):
    id: Optional[int] = Field(default=None, primary_key=True)
    journal_id: int = Field(foreign_key="journal.id")
    opens: Optional[date] = None
    closes: Optional[date] = None
    genre: Optional[str] = None
    notes: Optional[str] = None
    scraped_at: datetime = Field(default_factory=datetime.utcnow)

    journal: Journal = Relationship(back_populates="open_calls")


class Submission(SQLModel, table=True):
    id: Optional[int] = Field(default=None, primary_key=True)
    poem_id: int = Field(foreign_key="poem.id")
    journal_id: int = Field(foreign_key="journal.id")
    submitted_at: date = Field(default_factory=date.today)
    status: SubmissionStatus = SubmissionStatus.pending
    responded_at: Optional[date] = None
    notes: Optional[str] = None

    poem: Poem = Relationship(back_populates="submissions")
    journal: Journal = Relationship(back_populates="submissions")


class Profile(SQLModel, table=True):
    id: Optional[int] = Field(default=None, primary_key=True)
    default_bio_short: Optional[str] = None  # ~50 words
    default_bio_long: Optional[str] = None   # ~100 words
    name: str = "Anastasiia Iurshina"
    email: str = ""
    website: Optional[str] = None
    updated_at: datetime = Field(default_factory=datetime.utcnow)
