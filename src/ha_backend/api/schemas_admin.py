from __future__ import annotations

from datetime import datetime
from typing import Any, Dict, List, Optional

from pydantic import BaseModel


class JobSummarySchema(BaseModel):
    id: int
    sourceCode: str
    sourceName: str
    name: str
    status: str
    retryCount: int
    createdAt: datetime
    queuedAt: Optional[datetime]
    startedAt: Optional[datetime]
    finishedAt: Optional[datetime]
    cleanupStatus: str
    cleanedAt: Optional[datetime]
    crawlerExitCode: Optional[int]
    crawlerStatus: Optional[str]
    warcFileCount: int
    warcBytesTotal: int
    indexedPageCount: int
    storageScannedAt: Optional[datetime] = None


class JobDetailSchema(BaseModel):
    id: int
    sourceCode: str
    sourceName: str
    name: str
    status: str
    retryCount: int
    createdAt: datetime
    queuedAt: Optional[datetime]
    startedAt: Optional[datetime]
    finishedAt: Optional[datetime]
    cleanupStatus: str
    cleanedAt: Optional[datetime]
    outputDir: str
    crawlerExitCode: Optional[int]
    crawlerStatus: Optional[str]
    crawlerStage: Optional[str]
    warcFileCount: int
    warcBytesTotal: int
    indexedPageCount: int
    pagesCrawled: int
    pagesTotal: int
    pagesFailed: int
    outputBytesTotal: int
    tmpBytesTotal: int
    tmpNonWarcBytesTotal: int
    storageScannedAt: Optional[datetime] = None
    finalZimPath: Optional[str]
    combinedLogPath: Optional[str]
    stateFilePath: Optional[str]
    config: Optional[Dict[str, Any]]
    lastStats: Optional[Dict[str, Any]]


class JobSnapshotSummarySchema(BaseModel):
    id: int
    url: str
    captureTimestamp: datetime
    statusCode: Optional[int]
    language: Optional[str]
    title: Optional[str]


class JobListResponseSchema(BaseModel):
    items: List[JobSummarySchema]
    total: int
    limit: int
    offset: int


class JobStatusCountsSchema(BaseModel):
    counts: Dict[str, int]


class SearchDebugItemSchema(BaseModel):
    id: int
    title: Optional[str]
    sourceCode: str
    sourceName: str
    language: Optional[str]
    captureTimestamp: datetime
    statusCode: Optional[int]
    originalUrl: str
    normalizedUrlGroup: Optional[str]

    # Signals (raw)
    inlinkCount: Optional[int]
    outlinkCount: Optional[int]
    pagerank: Optional[float]

    # Score breakdown (components)
    rankText: Optional[float]
    titleBoost: float
    archivedPenalty: float
    queryPenalty: float
    trackingPenalty: float
    depthPenalty: float
    authorityBoost: float
    hubnessBoost: float
    pagerankBoost: float

    totalScore: Optional[float]

    # pages view only
    groupScore: Optional[float] = None
    bestSnapshotId: Optional[int] = None


class SearchDebugResponseSchema(BaseModel):
    results: List[SearchDebugItemSchema]
    total: int
    page: int
    pageSize: int

    dialect: str
    mode: str
    view: str
    sort: str
    rankingVersion: str
    queryMode: Optional[str]
    usedPageSignals: bool
    usedSnapshotOutlinks: bool
    usedPagerank: bool


__all__ = [
    "JobSummarySchema",
    "JobDetailSchema",
    "JobSnapshotSummarySchema",
    "JobListResponseSchema",
    "JobStatusCountsSchema",
    "SearchDebugItemSchema",
    "SearchDebugResponseSchema",
]
