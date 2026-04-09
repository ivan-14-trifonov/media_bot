"""
Debug Collector Module
Collects diagnostic information for failed jobs.
Implements Principle 40: Automatic debug archive generation.
"""

import os
import re
import sys
import json
import shutil
import zipfile
import platform
import tempfile
from pathlib import Path
from datetime import datetime
from dataclasses import dataclass, field
from typing import Dict, Any, List, Optional, Set

from .job import JobCard, JobStatus, StepCard, StepStatus, JobStorage


@dataclass
class SanitizationRule:
    """Rule for sanitizing sensitive data."""
    pattern: str
    replacement: str = "[REDACTED]"
    description: str = ""
    priority: int = 0  # Higher priority rules run first


@dataclass
class DebugArchive:
    """Represents a generated debug archive."""
    path: Path
    job_id: str
    created_at: datetime
    size_bytes: int
    contents: List[str]
    sanitized_items: int = 0
    
    def to_dict(self) -> Dict[str, Any]:
        return {
            "path": str(self.path),
            "job_id": self.job_id,
            "created_at": self.created_at.isoformat(),
            "size_bytes": self.size_bytes,
            "size_mb": round(self.size_bytes / (1024 * 1024), 2),
            "contents": self.contents,
            "sanitized_items": self.sanitized_items,
        }


class DebugCollector:
    """
    Collects diagnostic information for failed jobs.
    
    Features:
    - Collects job state, logs, manifests
    - Sanitizes sensitive data (tokens, passwords, keys)
    - Creates ZIP archive for sharing
    - CLI command for manual collection
    
    Principle 40: When tools fail, agents need diagnostic data to fix them.
    """
    
    # Default sanitization rules for sensitive data
    DEFAULT_SANITIZATION_RULES = [
        # API Keys
        SanitizationRule(
            pattern=r'sk-[a-zA-Z0-9]{20,}',
            replacement='[API_KEY_REDACTED]',
            description="OpenAI-style API keys",
            priority=100
        ),
        SanitizationRule(
            pattern=r'Bearer\s+[a-zA-Z0-9\-_]+\.[a-zA-Z0-9\-_]+\.[a-zA-Z0-9\-_]+',
            replacement='Bearer [TOKEN_REDACTED]',
            description="JWT Bearer tokens",
            priority=100
        ),
        # Passwords in URLs
        SanitizationRule(
            pattern=r'://([^:]+):([^@]+)@',
            replacement='://[USER]:[PASSWORD_REDACTED]@',
            description="Passwords in URLs",
            priority=90
        ),
        # AWS credentials
        SanitizationRule(
            pattern=r'AKIA[0-9A-Z]{16}',
            replacement='[AWS_KEY_REDACTED]',
            description="AWS Access Key IDs",
            priority=100
        ),
        # Private keys
        SanitizationRule(
            pattern=r'-----BEGIN\s+(RSA\s+)?PRIVATE\s+KEY-----[\s\S]*?-----END\s+(RSA\s+)?PRIVATE\s+KEY-----',
            replacement='[PRIVATE_KEY_REDACTED]',
            description="Private keys",
            priority=100
        ),
        # Generic secrets/passwords
        SanitizationRule(
            pattern=r'(?i)(password|passwd|pwd|secret|token|api_key|apikey)\s*[=:]\s*["\']?[\w\-@#$%^&*!]+["\']?',
            replacement=r'\1=[REDACTED]',
            description="Generic password/secret patterns",
            priority=80
        ),
        # Email addresses (optional privacy)
        SanitizationRule(
            pattern=r'[a-zA-Z0-9._%+-]+@[a-zA-Z0-9.-]+\.[a-zA-Z]{2,}',
            replacement='[EMAIL_REDACTED]',
            description="Email addresses",
            priority=50
        ),
    ]
    
    def __init__(
        self,
        storage: Optional[JobStorage] = None,
        output_dir: Optional[Path] = None,
        sanitization_rules: Optional[List[SanitizationRule]] = None,
    ):
        """
        Initialize DebugCollector.
        
        Args:
            storage: JobStorage instance for retrieving job data
            output_dir: Directory for storing debug archives
            sanitization_rules: Custom sanitization rules (or use defaults)
        """
        self.storage = storage
        self.output_dir = output_dir or Path.home() / ".kit" / "logs"
        self.sanitization_rules = sanitization_rules or self.DEFAULT_SANITIZATION_RULES.copy()
        
        # Sort rules by priority (higher first)
        self.sanitization_rules.sort(key=lambda r: r.priority, reverse=True)
        
        # Statistics
        self._sanitized_count = 0
        
        # Ensure output directory exists
        self.output_dir.mkdir(parents=True, exist_ok=True)
    
    def collect_for_job(
        self,
        job: JobCard,
        step_index: Optional[int] = None,
        manifests: Optional[Dict[str, Dict[str, Any]]] = None,
    ) -> DebugArchive:
        """
        Collect debug information for a failed job.
        
        Args:
            job: Failed JobCard
            step_index: Specific step that failed (or None for all steps)
            manifests: Tool manifests dictionary
            
        Returns:
            DebugArchive with collected information
        """
        self._sanitized_count = 0
        
        # Create temporary directory for collection
        with tempfile.TemporaryDirectory() as temp_dir:
            temp_path = Path(temp_dir)
            contents = []
            
            # 1. Collect job card
            job_file = temp_path / "job.json"
            self._save_json(job_file, job.to_dict())
            contents.append("job.json")
            
            # 2. Collect system information
            sys_info = self._collect_system_info()
            sys_file = temp_path / "system_info.json"
            self._save_json(sys_file, sys_info)
            contents.append("system_info.json")
            
            # 3. Collect step-specific data
            target_steps = [step_index] if step_index is not None else range(len(job.steps))
            
            for idx in target_steps:
                if idx < 0 or idx >= len(job.steps):
                    continue
                    
                step = job.steps[idx]
                step_dir = temp_path / f"step_{idx:03d}_{step.tool}"
                step_dir.mkdir(exist_ok=True)
                
                # Step card
                step_file = step_dir / "step.json"
                self._save_json(step_file, step.to_dict())
                contents.append(f"step_{idx:03d}/step.json")
                
                # Step logs (stdout/stderr)
                if step.stdout:
                    stdout_file = step_dir / "stdout.log"
                    sanitized_stdout = self._sanitize_content(step.stdout)
                    stdout_file.write_text(sanitized_stdout, encoding='utf-8')
                    contents.append(f"step_{idx:03d}/stdout.log")
                
                if step.stderr:
                    stderr_file = step_dir / "stderr.log"
                    sanitized_stderr = self._sanitize_content(step.stderr)
                    stderr_file.write_text(sanitized_stderr, encoding='utf-8')
                    contents.append(f"step_{idx:03d}/stderr.log")
                
                # Tool manifest
                if manifests and step.tool in manifests:
                    manifest_file = step_dir / "manifest.yaml"
                    import yaml
                    with open(manifest_file, 'w', encoding='utf-8') as f:
                        yaml.safe_dump(manifests[step.tool], f, allow_unicode=True)
                    contents.append(f"step_{idx:03d}/manifest.yaml")
                
                # Job directory contents (if available)
                if self.storage:
                    self._collect_job_outputs(temp_path, job.job_id, idx, contents)
            
            # 4. Add collection metadata
            metadata = {
                "collected_at": datetime.now().isoformat(),
                "job_id": job.job_id,
                "job_status": job.status.value,
                "total_steps": len(job.steps),
                "failed_step": step_index,
                "sanitized_items": self._sanitized_count,
                "kit_version": "0.2.0",
            }
            meta_file = temp_path / "collection_metadata.json"
            self._save_json(meta_file, metadata)
            contents.append("collection_metadata.json")
            
            # 5. Create ZIP archive
            archive_path = self.output_dir / f"debug_{job.job_id}_{datetime.now().strftime('%Y%m%d_%H%M%S')}.zip"
            self._create_zip(temp_path, archive_path)
            
            return DebugArchive(
                path=archive_path,
                job_id=job.job_id,
                created_at=datetime.now(),
                size_bytes=archive_path.stat().st_size,
                contents=contents,
                sanitized_items=self._sanitized_count,
            )
    
    def collect_from_execution_result(
        self,
        job: JobCard,
        step: StepCard,
        step_index: int,
        stdout: str,
        stderr: str,
        return_code: int,
        manifests: Optional[Dict[str, Dict[str, Any]]] = None,
    ) -> DebugArchive:
        """
        Collect debug information directly from execution result.
        
        Useful when called immediately after step failure.
        
        Args:
            job: Parent job
            step: Failed step
            step_index: Index of failed step
            stdout: Step stdout
            stderr: Step stderr
            return_code: Step return code
            manifests: Tool manifests
            
        Returns:
            DebugArchive with collected information
        """
        self._sanitized_count = 0
        
        with tempfile.TemporaryDirectory() as temp_dir:
            temp_path = Path(temp_dir)
            contents = []
            
            # Job card
            job_file = temp_path / "job.json"
            self._save_json(job_file, job.to_dict())
            contents.append("job.json")
            
            # System info
            sys_info = self._collect_system_info()
            sys_file = temp_path / "system_info.json"
            self._save_json(sys_file, sys_info)
            contents.append("system_info.json")
            
            # Step data
            step_dir = temp_path / f"step_{step_index:03d}_{step.tool}"
            step_dir.mkdir(exist_ok=True)
            
            # Step card
            step_file = step_dir / "step.json"
            self._save_json(step_file, step.to_dict())
            contents.append(f"step_{step_index:03d}/step.json")
            
            # Logs with sanitization
            if stdout:
                stdout_file = step_dir / "stdout.log"
                sanitized_stdout = self._sanitize_content(stdout)
                stdout_file.write_text(sanitized_stdout, encoding='utf-8')
                contents.append(f"step_{step_index:03d}/stdout.log")
            
            if stderr:
                stderr_file = step_dir / "stderr.log"
                sanitized_stderr = self._sanitize_content(stderr)
                stderr_file.write_text(sanitized_stderr, encoding='utf-8')
                contents.append(f"step_{step_index:03d}/stderr.log")
            
            # Return code info
            rc_info = {
                "return_code": return_code,
                "success": return_code == 0,
                "step_status": step.status.value,
            }
            rc_file = step_dir / "return_code.json"
            self._save_json(rc_file, rc_info)
            contents.append(f"step_{step_index:03d}/return_code.json")
            
            # Tool manifest
            if manifests and step.tool in manifests:
                manifest_file = step_dir / "manifest.yaml"
                import yaml
                with open(manifest_file, 'w', encoding='utf-8') as f:
                    yaml.safe_dump(manifests[step.tool], f, allow_unicode=True)
                contents.append(f"step_{step_index:03d}/manifest.yaml")
            
            # Metadata
            metadata = {
                "collected_at": datetime.now().isoformat(),
                "job_id": job.job_id,
                "step_id": step.id,
                "step_tool": step.tool,
                "step_mode": step.mode,
                "return_code": return_code,
                "sanitized_items": self._sanitized_count,
            }
            meta_file = temp_path / "collection_metadata.json"
            self._save_json(meta_file, metadata)
            contents.append("collection_metadata.json")
            
            # Create ZIP
            archive_path = self.output_dir / f"debug_{job.job_id}_{step.tool}_{datetime.now().strftime('%Y%m%d_%H%M%S')}.zip"
            self._create_zip(temp_path, archive_path)
            
            return DebugArchive(
                path=archive_path,
                job_id=job.job_id,
                created_at=datetime.now(),
                size_bytes=archive_path.stat().st_size,
                contents=contents,
                sanitized_items=self._sanitized_count,
            )
    
    def _sanitize_content(self, content: str) -> str:
        """
        Sanitize sensitive data from content.
        
        Args:
            content: Raw content string
            
        Returns:
            Sanitized content
        """
        sanitized = content
        
        for rule in self.sanitization_rules:
            pattern = re.compile(rule.pattern, re.IGNORECASE | re.MULTILINE)
            matches = pattern.findall(sanitized)
            if matches:
                self._sanitized_count += len(matches)
                sanitized = pattern.sub(rule.replacement, sanitized)
        
        return sanitized
    
    def _collect_system_info(self) -> Dict[str, Any]:
        """Collect system information."""
        import sys
        
        return {
            "platform": {
                "system": platform.system(),
                "release": platform.release(),
                "version": platform.version(),
                "machine": platform.machine(),
                "processor": platform.processor(),
            },
            "python": {
                "version": sys.version,
                "implementation": platform.python_implementation(),
                "version_info": list(sys.version_info),
            },
            "environment": {
                "cwd": os.getcwd(),
                "path": os.environ.get("PATH", ""),
                "pythonpath": os.environ.get("PYTHONPATH", ""),
                "kit_base": os.environ.get("KIT_BASE", ""),
            },
            "collected_at": datetime.now().isoformat(),
        }
    
    def _collect_job_outputs(
        self,
        temp_path: Path,
        job_id: str,
        step_index: int,
        contents: List[str],
    ) -> None:
        """Copy job output files to debug archive."""
        if not self.storage:
            return
        
        try:
            output_files = self.storage.get_output_files(job_id)
            if not output_files:
                return
            
            outputs_dir = temp_path / "outputs"
            outputs_dir.mkdir(exist_ok=True)
            
            for file_info in output_files:
                file_path = file_info.get("path")
                if not file_path:
                    continue
                
                src_path = Path(file_path)
                if src_path.exists():
                    # Copy file, sanitizing if text
                    dst_file = outputs_dir / src_path.name
                    
                    if self._is_text_file(src_path):
                        content = src_path.read_text(encoding='utf-8', errors='ignore')
                        sanitized = self._sanitize_content(content)
                        dst_file.write_text(sanitized, encoding='utf-8')
                    else:
                        shutil.copy2(src_path, dst_file)
                    
                    contents.append(f"outputs/{src_path.name}")
                    
        except Exception:
            pass  # Ignore errors collecting outputs
    
    def _is_text_file(self, path: Path) -> bool:
        """Check if file is likely a text file."""
        text_extensions = {
            '.txt', '.log', '.json', '.yaml', '.yml', '.xml', '.csv',
            '.md', '.rst', '.html', '.css', '.js', '.py', '.sh', '.bat',
            '.srt', '.vtt', '.sub', '.txt', '.cfg', '.conf', '.ini'
        }
        return path.suffix.lower() in text_extensions
    
    def _save_json(self, path: Path, data: Dict[str, Any]) -> None:
        """Save data as JSON file."""
        with open(path, 'w', encoding='utf-8') as f:
            json.dump(data, f, indent=2, ensure_ascii=False, default=str)
    
    def _create_zip(self, source_dir: Path, archive_path: Path) -> None:
        """Create ZIP archive from directory."""
        with zipfile.ZipFile(archive_path, 'w', zipfile.ZIP_DEFLATED) as zipf:
            for file_path in source_dir.rglob('*'):
                if file_path.is_file():
                    arcname = file_path.relative_to(source_dir)
                    zipf.write(file_path, arcname)
    
    def add_sanitization_rule(self, rule: SanitizationRule) -> None:
        """Add custom sanitization rule."""
        self.sanitization_rules.append(rule)
        self.sanitization_rules.sort(key=lambda r: r.priority, reverse=True)
    
    def remove_sanitization_rule(self, pattern: str) -> bool:
        """Remove sanitization rule by pattern."""
        for i, rule in enumerate(self.sanitization_rules):
            if rule.pattern == pattern:
                del self.sanitization_rules[i]
                return True
        return False
    
    def get_sanitization_stats(self) -> Dict[str, Any]:
        """Get sanitization statistics."""
        return {
            "total_rules": len(self.sanitization_rules),
            "rules": [
                {
                    "description": rule.description,
                    "priority": rule.priority,
                    "replacement": rule.replacement,
                }
                for rule in self.sanitization_rules
            ],
        }


def create_debug_collector(
    storage: Optional[JobStorage] = None,
    output_dir: Optional[str] = None
) -> DebugCollector:
    """
    Create DebugCollector instance.
    
    Args:
        storage: JobStorage for retrieving job data
        output_dir: Output directory for debug archives
        
    Returns:
        Configured DebugCollector
    """
    out_path = Path(output_dir) if output_dir else None
    return DebugCollector(storage=storage, output_dir=out_path)


def collect_debug_archive(
    job_id: str,
    storage: JobStorage,
    manifests: Optional[Dict[str, Dict[str, Any]]] = None,
    output_dir: Optional[str] = None
) -> Optional[DebugArchive]:
    """
    Convenience function to collect debug archive for a job.
    
    Args:
        job_id: ID of failed job
        storage: JobStorage instance
        manifests: Tool manifests
        output_dir: Output directory
        
    Returns:
        DebugArchive or None if job not found
    """
    job = storage.get_job(job_id)
    if not job:
        return None
    
    # Find failed step
    failed_step_index = None
    for i, step in enumerate(job.steps):
        if step.status == StepStatus.FAILED:
            failed_step_index = i
            break
    
    collector = create_debug_collector(storage, output_dir)
    return collector.collect_for_job(job, failed_step_index, manifests)
