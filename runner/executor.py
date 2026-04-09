"""
Step Executor Module
Executes pipeline steps with retry logic and exponential backoff.
"""

import os
import sys
import time
import asyncio
import subprocess
import shlex
from datetime import datetime
from pathlib import Path
from dataclasses import dataclass
from typing import Dict, Any, List, Optional, Callable
from enum import Enum

from .job import StepCard, StepStatus, JobCard
from .validator import OutputValidator, ValidationResult, ValidationStatus
from .proxy import ProxyManager, ProxyConfig


class ExecutionStatus(str, Enum):
    SUCCESS = "success"
    FAILED = "failed"
    RETRY = "retry"
    TIMEOUT = "timeout"
    CANCELLED = "cancelled"


@dataclass
class ExecutionResult:
    """Result of step execution"""
    status: ExecutionStatus
    output_params: Dict[str, Any]
    output_files: List[str]
    stdout: str = ""
    stderr: str = ""
    return_code: int = 0
    duration_seconds: float = 0.0
    error_message: Optional[str] = None
    preview: Optional[Dict[str, Any]] = None

    def to_dict(self) -> Dict[str, Any]:
        return {
            'status': self.status.value,
            'output_params': self.output_params,
            'output_files': self.output_files,
            'stdout': self.stdout,
            'stderr': self.stderr,
            'return_code': self.return_code,
            'duration_seconds': self.duration_seconds,
            'error_message': self.error_message,
            'preview': self.preview,
        }


class StepExecutor:
    """Executes pipeline steps with retry logic"""

    def __init__(
        self,
        max_retries: int = 3,
        base_delay: float = 1.0,
        max_delay: float = 30.0,
        timeout: int = 3600,
        manifests: Optional[Dict[str, Dict[str, Any]]] = None,
        tool_config: Optional[Dict[str, Dict[str, Any]]] = None,
        proxy_manager: Optional[ProxyManager] = None,
    ):
        self.max_retries = max_retries
        self.base_delay = base_delay
        self.max_delay = max_delay
        self.timeout = timeout
        self.manifests = manifests or {}
        self.tool_config = tool_config or {}  # Tool-specific config (e.g., proxy)
        self.proxy_manager = proxy_manager or ProxyManager()  # Proxy manager for network tools
        self._cancelled = False

    def cancel(self):
        """Signal cancellation"""
        self._cancelled = True

    def reset_cancel(self):
        """Reset cancellation flag"""
        self._cancelled = False

    def execute_step(
        self,
        step: StepCard,
        job_dir: Path,
        previous_outputs: Optional[Dict[str, Any]] = None,
        input_data: Optional[Dict[str, Any]] = None,
    ) -> ExecutionResult:
        """
        Execute a single step with retry logic.
        
        Args:
            step: StepCard with step configuration
            job_dir: Directory for job output files
            previous_outputs: Outputs from previous steps for chaining
            input_data: Original job input data for $input.* references
        
        Returns:
            ExecutionResult with execution details
        """
        self._cancelled = False
        previous_outputs = previous_outputs or {}

        # Get tool manifest
        manifest = self.manifests.get(step.tool, {})
        modes = manifest.get('modes', {})
        mode_config = modes.get(step.mode, {})
        
        # Add known_warnings from manifest root to mode_config
        known_warnings = manifest.get('known_warnings', [])
        if known_warnings:
            mode_config = {**mode_config, 'known_warnings': known_warnings}

        # Build command
        cmd = self._build_command(step, mode_config, previous_outputs, job_dir, input_data)
        if not cmd:
            return ExecutionResult(
                status=ExecutionStatus.FAILED,
                output_params={},
                output_files=[],
                error_message=f"Could not build command for {step.tool}:{step.mode}"
            )

        # Execute with retries
        last_result = None
        for attempt in range(self.max_retries + 1):
            if self._cancelled:
                return ExecutionResult(
                    status=ExecutionStatus.CANCELLED,
                    output_params={},
                    output_files=[],
                    error_message="Execution cancelled"
                )

            step.retry_count = attempt
            
            result = self._execute_command(cmd, step, job_dir, mode_config)
            last_result = result

            if result.status == ExecutionStatus.SUCCESS:
                # Validate output
                validation = self._validate_output(manifest, result)
                if validation.status == ValidationStatus.VALID:
                    result.preview = self._generate_preview(result, mode_config)
                    return result
                elif validation.status == ValidationStatus.WARNING:
                    result.preview = self._generate_preview(result, mode_config)
                    return result
                else:
                    result.error_message = f"Validation failed: {validation.message}"
                    result.status = ExecutionStatus.FAILED

            # Check if retry is appropriate
            if not self._should_retry(result, attempt):
                break

            # Exponential backoff
            if attempt < self.max_retries:
                delay = min(self.base_delay * (2 ** attempt), self.max_delay)
                time.sleep(delay)

        return last_result or ExecutionResult(
            status=ExecutionStatus.FAILED,
            output_params={},
            output_files=[],
            error_message="Max retries exceeded"
        )

    def _build_command(
        self,
        step: StepCard,
        mode_config: Dict[str, Any],
        previous_outputs: Dict[str, Any],
        job_dir: Path,
        input_data: Optional[Dict[str, Any]] = None,
    ) -> Optional[List[str]]:
        """Build command from step configuration"""
        template = mode_config.get('command', '')
        if not template:
            return None

        # Build parameter map
        params = {**step.input_params}

        # Add tool-specific config (e.g., proxy)
        tool_cfg = self.tool_config.get(step.tool, {})
        for key, value in tool_cfg.items():
            if key not in params:  # Don't override step-specific params
                params[key] = value

        # Add previous outputs for chaining
        for key, value in previous_outputs.items():
            if key not in params:
                params[key] = value

        # Resolve $input.* references from input_data
        input_data = input_data or {}
        for key, value in params.items():
            if isinstance(value, str) and value.startswith('$input.'):
                input_key = value[7:]  # Remove '$input.' prefix
                params[key] = input_data.get(input_key, value)

        # Resolve $prev.* references from previous outputs
        for key, value in params.items():
            if isinstance(value, str) and value.startswith('$prev.'):
                prev_key = value[6:]  # Remove '$prev.' prefix
                params[key] = previous_outputs.get(prev_key, value)

        # Generate output paths
        output_template = mode_config.get('output', {})
        for out_name, out_config in output_template.items():
            if 'path' in out_config:
                path_template = out_config['path']
                # Replace placeholders
                path = path_template.replace('{job_dir}', str(job_dir))
                path = path.replace('{step_id}', step.step_id)
                for key, value in params.items():
                    if isinstance(value, str):
                        path = path.replace(f'{{{key}}}', value)
                params[f'__out_{out_name}'] = path

        # Build command from template
        cmd_str = template
        for key, value in params.items():
            if not key.startswith('__'):
                cmd_str = cmd_str.replace(f'{{{key}}}', str(value))

        # Replace output placeholders
        for key, value in params.items():
            if key.startswith('__out_'):
                out_name = key[6:]
                cmd_str = cmd_str.replace(f'{{out.{out_name}}}', value)

        # Parse command
        try:
            return shlex.split(cmd_str)
        except ValueError as e:
            return None

    def _execute_command(
        self,
        cmd: List[str],
        step: StepCard,
        job_dir: Path,
        mode_config: Dict[str, Any],
    ) -> ExecutionResult:
        """Execute command and capture output"""
        start_time = time.time()

        # Get tool manifest for proxy configuration
        manifest = self.manifests.get(step.tool, {})
        
        # Inject proxy settings
        env_vars = os.environ.copy()
        proxy_env, proxy_params = self.proxy_manager.inject_for_step(manifest, env_vars)
        
        # Add proxy parameters to command if needed
        if proxy_params:
            # Insert proxy params after the main command
            cmd = cmd[:1] + proxy_params + cmd[1:]

        try:
            process = subprocess.Popen(
                cmd,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                cwd=str(job_dir),
                text=True,
                env=proxy_env,  # Use environment with proxy
            )

            try:
                stdout, stderr = process.communicate(timeout=self.timeout)
                return_code = process.returncode
            except subprocess.TimeoutExpired:
                process.kill()
                stdout, stderr = process.communicate()
                return ExecutionResult(
                    status=ExecutionStatus.TIMEOUT,
                    output_params={},
                    output_files=[],
                    stdout=stdout,
                    stderr=stderr,
                    return_code=-1,
                    duration_seconds=time.time() - start_time,
                    error_message=f"Step timed out after {self.timeout}s"
                )

            duration = time.time() - start_time

            # Check for success
            expected_codes = mode_config.get('success_codes', [0])
            print(f"    [DEBUG] return_code={return_code}, expected_codes={expected_codes}")
            if return_code in expected_codes:
                print(f"    [DEBUG] Return code OK, parsing outputs")
                # Parse outputs
                output_params, output_files = self._parse_outputs(
                    mode_config.get('output', {}),
                    stdout,
                    stderr,
                    job_dir,
                    step,
                )
                print(f"    [DEBUG] Parsed outputs: {output_params}, files: {output_files}")
                
                # Detect known warnings
                warnings = self._detect_known_warnings(stderr, mode_config)
                
                return ExecutionResult(
                    status=ExecutionStatus.SUCCESS,
                    output_params=output_params,
                    output_files=output_files,
                    stdout=stdout,
                    stderr=stderr,
                    return_code=return_code,
                    duration_seconds=duration,
                    error_message=warnings.get('message') if warnings else None,
                )
            else:
                return ExecutionResult(
                    status=ExecutionStatus.FAILED,
                    output_params={},
                    output_files=[],
                    stdout=stdout,
                    stderr=stderr,
                    return_code=return_code,
                    duration_seconds=duration,
                    error_message=f"Command failed with code {return_code}: {stderr[:500]}"
                )

        except FileNotFoundError as e:
            return ExecutionResult(
                status=ExecutionStatus.FAILED,
                output_params={},
                output_files=[],
                error_message=f"Command not found: {cmd[0]}"
            )
        except Exception as e:
            return ExecutionResult(
                status=ExecutionStatus.FAILED,
                output_params={},
                output_files=[],
                error_message=str(e)
            )

    def _parse_outputs(
        self,
        output_config: Dict[str, Any],
        stdout: str,
        stderr: str,
        job_dir: Path,
        step: StepCard,
    ) -> tuple[Dict[str, Any], List[str]]:
        """Parse command outputs"""
        output_params = {}
        output_files = []

        # Debug: print output_config
        print(f"    [DEBUG] output_config: {output_config}")
        print(f"    [DEBUG] step_dir: {job_dir / step.step_id}")

        for out_name, out_rules in output_config.items():
            # File outputs — ищем любой файл в папке шага
            if 'file' in out_rules or 'path' in out_rules:
                step_dir = job_dir / step.step_id
                print(f"    [DEBUG] Checking {out_name}: file in rules={('file' in out_rules)}, path in rules={('path' in out_rules)}")
                if step_dir.exists():
                    all_files = list(step_dir.iterdir())
                    print(f"    [DEBUG] Files in dir: {all_files}")
                    if all_files:
                        # Берём самый большой файл — это и есть результат
                        biggest = max(all_files, key=lambda f: f.stat().st_size)
                        output_params[out_name] = str(biggest)
                        output_files.append(str(biggest))
                        print(f"    [DEBUG] Found: {biggest}")

            # stdout/stderr parsing
            if 'parse' in out_rules:
                import re
                parse_config = out_rules['parse']
                source = stdout if parse_config.get('source', 'stdout') == 'stdout' else stderr
                pattern = parse_config.get('pattern')
                if pattern:
                    match = re.search(pattern, source)
                    if match:
                        output_params[out_name] = match.group(1) if match.groups() else match.group(0)

        return output_params, output_files

    def _validate_output(self, manifest: Dict[str, Any], result: ExecutionResult) -> ValidationResult:
        """Validate execution output"""
        if not manifest:
            return ValidationResult(
                status=ValidationStatus.SKIPPED,
                message="No manifest for validation"
            )

        validator = OutputValidator(manifest)
        return validator.validate(result.output_params, result.output_files)

    def _detect_known_warnings(
        self,
        stderr: str,
        mode_config: Dict[str, Any],
    ) -> Optional[Dict[str, Any]]:
        """Detect known warnings from stderr using manifest definitions"""
        import re
        
        # Get known warnings from mode config (passed via manifest)
        known_warnings = mode_config.get('known_warnings', [])
        if not known_warnings:
            return None
        
        detected = []
        for warning_def in known_warnings:
            pattern = warning_def.get('pattern', '')
            if pattern and re.search(pattern, stderr, re.IGNORECASE):
                detected.append({
                    'pattern': pattern,
                    'severity': warning_def.get('severity', 'warning'),
                    'action': warning_def.get('action', ''),
                    'impact': warning_def.get('impact', ''),
                })
        
        if detected:
            messages = [f"{w['severity']}: {w['action']}" for w in detected]
            return {
                'warnings': detected,
                'message': '; '.join(messages),
                'count': len(detected),
            }
        
        return None

    def _should_retry(self, result: ExecutionResult, attempt: int) -> bool:
        """Determine if step should be retried"""
        if attempt >= self.max_retries:
            return False
        
        # Retry on specific error patterns
        retry_patterns = [
            'rate limit',
            'timeout',
            'connection',
            'temporary',
            'retry',
            'network',
        ]
        
        error_text = (result.error_message or '').lower() + (result.stderr or '').lower()
        for pattern in retry_patterns:
            if pattern in error_text:
                return True
        
        # Retry on non-zero exit codes that might be transient
        if result.return_code in (1, 2, 137, 143):  # General error, SIGKILL, SIGTERM
            return True
        
        return False

    def _generate_preview(
        self,
        result: ExecutionResult,
        mode_config: Dict[str, Any],
    ) -> Optional[Dict[str, Any]]:
        """Generate step preview information"""
        preview = {}
        
        # File info
        for file_path in result.output_files:
            path = Path(file_path)
            if path.exists():
                preview['size'] = path.stat().st_size
                preview['format'] = path.suffix.lower()
        
        # Duration from output params
        if 'duration' in result.output_params:
            preview['duration'] = result.output_params['duration']
        
        # Media probing
        if result.output_files:
            from .validator import OutputValidator
            probe_result = OutputValidator({})._probe_media(result.output_files[0])
            if probe_result:
                preview.update(probe_result)
        
        return preview if preview else None


async def execute_step_async(
    executor: StepExecutor,
    step: StepCard,
    job_dir: Path,
    previous_outputs: Optional[Dict[str, Any]] = None,
) -> ExecutionResult:
    """Async wrapper for step execution"""
    loop = asyncio.get_event_loop()
    return await loop.run_in_executor(
        None,
        executor.execute_step,
        step,
        job_dir,
        previous_outputs,
    )
