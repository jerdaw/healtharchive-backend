from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional


@dataclass
class ArchiveToolOptions:
    """
    Typed representation of the archive_tool-specific options we store under
    ArchiveJob.config["tool_options"].

    This mirrors the CLI argument model in archive_tool.cli and provides a
    single place to define defaults.
    """

    cleanup: bool = False
    overwrite: bool = False
    enable_monitoring: bool = False
    enable_adaptive_workers: bool = False
    enable_adaptive_restart: bool = False
    enable_vpn_rotation: bool = False
    initial_workers: int = 1
    log_level: str = "INFO"
    docker_image: Optional[str] = None
    docker_shm_size: Optional[str] = None
    skip_final_build: bool = False

    # Optional monitoring/adaptive fields.
    monitor_interval_seconds: Optional[int] = None
    stall_timeout_minutes: Optional[int] = None
    error_threshold_timeout: Optional[int] = None
    error_threshold_http: Optional[int] = None
    min_workers: Optional[int] = None
    max_worker_reductions: Optional[int] = None
    max_container_restarts: Optional[int] = None

    # VPN and backoff fields.
    vpn_connect_command: Optional[str] = None
    max_vpn_rotations: Optional[int] = None
    vpn_rotation_frequency_minutes: Optional[int] = None
    backoff_delay_minutes: Optional[int] = None

    # Misc flags.
    relax_perms: bool = False

    def to_dict(self) -> Dict[str, Any]:
        """
        Convert this options object to the JSON-compatible dict stored on
        ArchiveJob.config["tool_options"].
        """
        data: Dict[str, Any] = {
            "cleanup": self.cleanup,
            "overwrite": self.overwrite,
            "skip_final_build": self.skip_final_build,
            "enable_monitoring": self.enable_monitoring,
            "enable_adaptive_workers": self.enable_adaptive_workers,
            "enable_adaptive_restart": self.enable_adaptive_restart,
            "enable_vpn_rotation": self.enable_vpn_rotation,
            "initial_workers": self.initial_workers,
            "log_level": self.log_level,
            "relax_perms": self.relax_perms,
        }

        # Only include optional fields when they are set to a non-None value
        # to keep the stored JSON compact and backwards-compatible.
        if self.docker_image is not None:
            docker_image = str(self.docker_image).strip()
            if docker_image:
                data["docker_image"] = docker_image
        if self.docker_shm_size is not None:
            docker_shm_size = str(self.docker_shm_size).strip()
            if docker_shm_size:
                data["docker_shm_size"] = docker_shm_size
        if self.monitor_interval_seconds is not None:
            data["monitor_interval_seconds"] = self.monitor_interval_seconds
        if self.stall_timeout_minutes is not None:
            data["stall_timeout_minutes"] = self.stall_timeout_minutes
        if self.error_threshold_timeout is not None:
            data["error_threshold_timeout"] = self.error_threshold_timeout
        if self.error_threshold_http is not None:
            data["error_threshold_http"] = self.error_threshold_http
        if self.min_workers is not None:
            data["min_workers"] = self.min_workers
        if self.max_worker_reductions is not None:
            data["max_worker_reductions"] = self.max_worker_reductions
        if self.max_container_restarts is not None:
            data["max_container_restarts"] = self.max_container_restarts
        if self.vpn_connect_command is not None:
            data["vpn_connect_command"] = self.vpn_connect_command
        if self.max_vpn_rotations is not None:
            data["max_vpn_rotations"] = self.max_vpn_rotations
        if self.vpn_rotation_frequency_minutes is not None:
            data["vpn_rotation_frequency_minutes"] = self.vpn_rotation_frequency_minutes
        if self.backoff_delay_minutes is not None:
            data["backoff_delay_minutes"] = self.backoff_delay_minutes

        return data

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "ArchiveToolOptions":
        """
        Construct an ArchiveToolOptions from a raw dict as stored in the DB.

        Unknown keys are ignored; missing keys fall back to defaults.
        """
        return cls(
            cleanup=bool(data.get("cleanup", False)),
            overwrite=bool(data.get("overwrite", False)),
            skip_final_build=bool(data.get("skip_final_build", False)),
            enable_monitoring=bool(data.get("enable_monitoring", False)),
            enable_adaptive_workers=bool(data.get("enable_adaptive_workers", False)),
            enable_adaptive_restart=bool(data.get("enable_adaptive_restart", False)),
            enable_vpn_rotation=bool(data.get("enable_vpn_rotation", False)),
            initial_workers=int(data.get("initial_workers", 1)),
            log_level=str(data.get("log_level", "INFO")),
            docker_image=data.get("docker_image"),
            docker_shm_size=data.get("docker_shm_size"),
            monitor_interval_seconds=data.get("monitor_interval_seconds"),
            stall_timeout_minutes=data.get("stall_timeout_minutes"),
            error_threshold_timeout=data.get("error_threshold_timeout"),
            error_threshold_http=data.get("error_threshold_http"),
            min_workers=data.get("min_workers"),
            max_worker_reductions=data.get("max_worker_reductions"),
            max_container_restarts=data.get("max_container_restarts"),
            vpn_connect_command=data.get("vpn_connect_command"),
            max_vpn_rotations=data.get("max_vpn_rotations"),
            vpn_rotation_frequency_minutes=data.get("vpn_rotation_frequency_minutes"),
            backoff_delay_minutes=data.get("backoff_delay_minutes"),
            relax_perms=bool(data.get("relax_perms", False)),
        )


@dataclass
class ArchiveJobConfig:
    """
    Typed representation of the JSON blob stored under ArchiveJob.config.
    """

    seeds: List[str] = field(default_factory=list)
    zimit_passthrough_args: List[str] = field(default_factory=list)
    tool_options: ArchiveToolOptions = field(default_factory=ArchiveToolOptions)

    def to_dict(self) -> Dict[str, Any]:
        """
        Convert this object to a JSON-compatible dict suitable for
        ArchiveJob.config.
        """
        return {
            "seeds": list(self.seeds),
            "zimit_passthrough_args": list(self.zimit_passthrough_args),
            "tool_options": self.tool_options.to_dict(),
        }

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "ArchiveJobConfig":
        """
        Construct an ArchiveJobConfig from the JSON stored on ArchiveJob.config.
        """
        seeds = list(data.get("seeds") or [])
        zimit_args = list(data.get("zimit_passthrough_args") or [])
        tool_opts = ArchiveToolOptions.from_dict(data.get("tool_options") or {})
        return cls(seeds=seeds, zimit_passthrough_args=zimit_args, tool_options=tool_opts)


def validate_tool_options(opts: ArchiveToolOptions) -> None:
    """
    Basic validation of ArchiveToolOptions, mirroring archive_tool's
    expectations.

    - enable_adaptive_workers requires enable_monitoring.
    - enable_vpn_rotation requires enable_monitoring and vpn_connect_command.
    """
    if opts.enable_adaptive_workers and not opts.enable_monitoring:
        raise ValueError("tool_options.enable_adaptive_workers requires enable_monitoring=True")

    if opts.enable_adaptive_restart and not opts.enable_monitoring:
        raise ValueError("tool_options.enable_adaptive_restart requires enable_monitoring=True")

    if opts.enable_vpn_rotation and not opts.enable_monitoring:
        raise ValueError("tool_options.enable_vpn_rotation requires enable_monitoring=True")

    if opts.enable_vpn_rotation and not (opts.vpn_connect_command or "").strip():
        raise ValueError("tool_options.enable_vpn_rotation requires vpn_connect_command to be set")

    if opts.docker_image is not None and not str(opts.docker_image).strip():
        raise ValueError("tool_options.docker_image cannot be blank when set")

    if opts.docker_shm_size is not None and not str(opts.docker_shm_size).strip():
        raise ValueError("tool_options.docker_shm_size cannot be blank when set")


__all__ = ["ArchiveToolOptions", "ArchiveJobConfig", "validate_tool_options"]
