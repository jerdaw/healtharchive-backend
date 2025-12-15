from __future__ import annotations

from typing import List, Optional

from pydantic import BaseModel


class TopicRefSchema(BaseModel):
    slug: str
    label: str


class SourceSummarySchema(BaseModel):
    sourceCode: str
    sourceName: str
    recordCount: int
    firstCapture: str
    lastCapture: str
    topics: List[TopicRefSchema]
    latestRecordId: Optional[int]


class SnapshotSummarySchema(BaseModel):
    id: int
    title: Optional[str]
    sourceCode: str
    sourceName: str
    language: Optional[str]
    topics: List[TopicRefSchema]
    captureDate: str
    originalUrl: str
    snippet: Optional[str]
    rawSnapshotUrl: Optional[str]


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
    topics: List[TopicRefSchema]
    captureDate: str
    originalUrl: str
    snippet: Optional[str]
    rawSnapshotUrl: Optional[str]
    mimeType: Optional[str]
    statusCode: Optional[int]


class ArchiveStatsSchema(BaseModel):
    snapshotsTotal: int
    pagesTotal: int
    sourcesTotal: int
    latestCaptureDate: Optional[str]


__all__ = [
    "TopicRefSchema",
    "SourceSummarySchema",
    "SnapshotSummarySchema",
    "SearchResponseSchema",
    "SnapshotDetailSchema",
    "ArchiveStatsSchema",
]
