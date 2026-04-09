"""
Kit Runner Package
Universal tool runner with LLM-based pipeline building.
"""

from .job import JobCard, StepCard, JobStatus, StepStatus, JobStorage, create_job
from .executor import StepExecutor, ExecutionResult, ExecutionStatus
from .validator import OutputValidator, ValidationResult, ValidationStatus
from .pipeline import PipelineBuilder, PipelinePlan, build_pipeline
from .main import KitRunner
from .installer import (
    ToolInstaller,
    InstallResult,
    InstallStatus,
    InstallMethod,
    install_tool,
    check_tool_installed,
)
from .proxy import ProxyManager, ProxyConfig, ProxyMethod, ProxyType, create_proxy_manager
from .debug import DebugCollector, DebugArchive, create_debug_collector, collect_debug_archive

__version__ = '0.2.0'
__all__ = [
    'JobCard',
    'StepCard',
    'JobStatus',
    'StepStatus',
    'JobStorage',
    'create_job',
    'StepExecutor',
    'ExecutionResult',
    'ExecutionStatus',
    'OutputValidator',
    'ValidationResult',
    'ValidationStatus',
    'PipelineBuilder',
    'PipelinePlan',
    'build_pipeline',
    'KitRunner',
    # Installer
    'ToolInstaller',
    'InstallResult',
    'InstallStatus',
    'InstallMethod',
    'install_tool',
    'check_tool_installed',
    # Proxy
    'ProxyManager',
    'ProxyConfig',
    'ProxyMethod',
    'ProxyType',
    'create_proxy_manager',
    # Debug
    'DebugCollector',
    'DebugArchive',
    'create_debug_collector',
    'collect_debug_archive',
]
