from __future__ import annotations

from typing import List, Optional

from pydantic import BaseModel


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


__all__ = [
    "SourceSummarySchema",
    "SourceEditionSchema",
    "SnapshotSummarySchema",
    "SearchResponseSchema",
    "SnapshotDetailSchema",
    "ArchiveStatsSchema",
]
