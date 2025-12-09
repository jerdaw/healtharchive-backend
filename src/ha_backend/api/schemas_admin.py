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
    indexedPageCount: int


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
    indexedPageCount: int
    pagesCrawled: int
    pagesTotal: int
    pagesFailed: int
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


__all__ = [
    "JobSummarySchema",
    "JobDetailSchema",
    "JobSnapshotSummarySchema",
    "JobListResponseSchema",
    "JobStatusCountsSchema",
]
