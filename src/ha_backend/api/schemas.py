from __future__ import annotations

from datetime import datetime
from enum import Enum
from typing import List, Optional

from pydantic import BaseModel, Field


class SourceSummarySchema(BaseModel):
    sourceCode: str
    sourceName: str
    baseUrl: Optional[str] = None
    description: Optional[str] = None
    recordCount: int
    firstCapture: str
    lastCapture: str
    latestRecordId: Optional[int]
    entryRecordId: Optional[int] = None
    entryBrowseUrl: Optional[str] = None
    entryPreviewUrl: Optional[str] = None


class SourceEditionSchema(BaseModel):
    jobId: int
    jobName: str
    recordCount: int
    firstCapture: str
    lastCapture: str
    entryBrowseUrl: Optional[str] = None


class ReplayResolveSchema(BaseModel):
    found: bool
    snapshotId: Optional[int] = None
    captureTimestamp: Optional[str] = None
    resolvedUrl: Optional[str] = None
    browseUrl: Optional[str] = None


class SnapshotSummarySchema(BaseModel):
    id: int
    title: Optional[str]
    sourceCode: str
    sourceName: str
    language: Optional[str]
    captureDate: str
    captureTimestamp: Optional[str]
    jobId: Optional[int]
    originalUrl: str
    snippet: Optional[str]
    pageSnapshotsCount: Optional[int] = None
    rawSnapshotUrl: Optional[str]
    browseUrl: Optional[str]


class SearchResponseSchema(BaseModel):
    results: List[SnapshotSummarySchema]
    total: int
    page: int
    pageSize: int


class SnapshotDetailSchema(BaseModel):
    id: int
    title: Optional[str]
    sourceCode: str
    sourceName: str
    language: Optional[str]
    captureDate: str
    captureTimestamp: Optional[str]
    jobId: Optional[int]
    originalUrl: str
    snippet: Optional[str]
    rawSnapshotUrl: Optional[str]
    browseUrl: Optional[str]
    mimeType: Optional[str]
    statusCode: Optional[int]


class ArchiveStatsSchema(BaseModel):
    snapshotsTotal: int
    pagesTotal: int
    sourcesTotal: int
    latestCaptureDate: Optional[str]
    latestCaptureAgeDays: Optional[int]


class IssueReportCategory(str, Enum):
    broken_snapshot = "broken_snapshot"
    incorrect_metadata = "incorrect_metadata"
    missing_snapshot = "missing_snapshot"
    takedown = "takedown"
    general_feedback = "general_feedback"


class IssueReportCreateSchema(BaseModel):
    category: IssueReportCategory
    description: str = Field(min_length=20, max_length=4000)
    snapshotId: Optional[int] = Field(default=None, ge=1)
    originalUrl: Optional[str] = Field(default=None, max_length=4096)
    reporterEmail: Optional[str] = Field(default=None, max_length=255)
    pageUrl: Optional[str] = Field(default=None, max_length=4096)
    website: Optional[str] = Field(default=None, max_length=200)


class IssueReportReceiptSchema(BaseModel):
    reportId: Optional[int]
    status: str
    receivedAt: datetime


__all__ = [
    "SourceSummarySchema",
    "SourceEditionSchema",
    "ReplayResolveSchema",
    "SnapshotSummarySchema",
    "SearchResponseSchema",
    "SnapshotDetailSchema",
    "ArchiveStatsSchema",
    "IssueReportCategory",
    "IssueReportCreateSchema",
    "IssueReportReceiptSchema",
]
