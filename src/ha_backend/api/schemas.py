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
    mimeType: Optional[str] = None


class SnapshotLatestSchema(BaseModel):
    found: bool
    snapshotId: Optional[int] = None
    captureTimestamp: Optional[str] = None
    mimeType: Optional[str] = None


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


class UsageMetricsCountsSchema(BaseModel):
    searchRequests: int
    snapshotDetailViews: int
    rawSnapshotViews: int
    reportSubmissions: int


class UsageMetricsDaySchema(UsageMetricsCountsSchema):
    date: str


class UsageMetricsSchema(BaseModel):
    enabled: bool
    windowDays: int
    totals: UsageMetricsCountsSchema
    daily: List[UsageMetricsDaySchema]


class ExportResourceSchema(BaseModel):
    path: str
    description: str
    formats: List[str]


class ExportManifestSchema(BaseModel):
    enabled: bool
    formats: List[str]
    defaultLimit: int
    maxLimit: int
    dataDictionaryUrl: Optional[str] = None
    snapshots: ExportResourceSchema
    changes: ExportResourceSchema


class ChangeEventSchema(BaseModel):
    changeId: int
    changeType: str
    summary: Optional[str]
    highNoise: bool
    diffAvailable: bool
    sourceCode: Optional[str]
    sourceName: Optional[str]
    normalizedUrlGroup: Optional[str]
    fromSnapshotId: Optional[int]
    toSnapshotId: int
    fromCaptureTimestamp: Optional[str]
    toCaptureTimestamp: Optional[str]
    fromJobId: Optional[int]
    toJobId: Optional[int]
    addedSections: Optional[int]
    removedSections: Optional[int]
    changedSections: Optional[int]
    addedLines: Optional[int]
    removedLines: Optional[int]
    changeRatio: Optional[float]


class ChangeFeedSchema(BaseModel):
    enabled: bool
    total: int
    page: int
    pageSize: int
    results: List[ChangeEventSchema]


class ChangeCompareSnapshotSchema(BaseModel):
    snapshotId: int
    title: Optional[str]
    captureDate: str
    captureTimestamp: Optional[str]
    originalUrl: str
    jobId: Optional[int]
    jobName: Optional[str]


class ChangeCompareSchema(BaseModel):
    event: ChangeEventSchema
    fromSnapshot: Optional[ChangeCompareSnapshotSchema]
    toSnapshot: ChangeCompareSnapshotSchema
    diffFormat: Optional[str]
    diffHtml: Optional[str]
    diffTruncated: bool
    diffVersion: Optional[str]
    normalizationVersion: Optional[str]


class CompareLiveFetchSchema(BaseModel):
    requestedUrl: str
    finalUrl: str
    statusCode: int
    contentType: Optional[str]
    bytesRead: int
    fetchedAt: datetime


class CompareLiveStatsSchema(BaseModel):
    summary: str
    addedSections: int
    removedSections: int
    changedSections: int
    addedLines: int
    removedLines: int
    changeRatio: float
    highNoise: bool


class CompareLiveDiffSchema(BaseModel):
    diffFormat: str
    diffHtml: str
    diffTruncated: bool
    diffVersion: str
    normalizationVersion: str


class CompareLiveRenderInstructionSchema(BaseModel):
    type: str
    lineIndexA: Optional[int] = None
    lineIndexB: Optional[int] = None


class CompareLiveRenderSchema(BaseModel):
    archivedLines: List[str]
    liveLines: List[str]
    renderInstructions: List[CompareLiveRenderInstructionSchema]
    renderTruncated: bool
    renderLineLimit: int


class CompareLiveSchema(BaseModel):
    archivedSnapshot: ChangeCompareSnapshotSchema
    liveFetch: CompareLiveFetchSchema
    stats: CompareLiveStatsSchema
    diff: CompareLiveDiffSchema
    render: CompareLiveRenderSchema
    textModeRequested: str
    textModeUsed: str
    textModeFallback: bool


class SnapshotTimelineItemSchema(BaseModel):
    snapshotId: int
    captureDate: str
    captureTimestamp: Optional[str]
    jobId: Optional[int]
    jobName: Optional[str]
    title: Optional[str]
    statusCode: Optional[int]
    compareFromSnapshotId: Optional[int]
    browseUrl: Optional[str] = None


class SnapshotTimelineSchema(BaseModel):
    sourceCode: Optional[str]
    sourceName: Optional[str]
    normalizedUrlGroup: Optional[str]
    snapshots: List[SnapshotTimelineItemSchema]


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
    "UsageMetricsCountsSchema",
    "UsageMetricsDaySchema",
    "UsageMetricsSchema",
    "ExportManifestSchema",
    "ExportResourceSchema",
    "ChangeEventSchema",
    "ChangeFeedSchema",
    "ChangeCompareSnapshotSchema",
    "ChangeCompareSchema",
    "CompareLiveFetchSchema",
    "CompareLiveStatsSchema",
    "CompareLiveDiffSchema",
    "CompareLiveSchema",
    "SnapshotTimelineItemSchema",
    "SnapshotTimelineSchema",
    "IssueReportCategory",
    "IssueReportCreateSchema",
    "IssueReportReceiptSchema",
]
