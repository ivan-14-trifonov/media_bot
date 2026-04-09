"""
Kit Runner - Main Entry Point
Orchestrates pipeline execution with resumable job support.
"""

import os
import sys
import time
import signal
import argparse
from pathlib import Path
from datetime import datetime, timezone
from typing import Dict, Any, List, Optional

import yaml

from .job import JobCard, JobStatus, StepCard, StepStatus, JobStorage, create_job
from .executor import StepExecutor, ExecutionStatus, ExecutionResult
from .validator import OutputValidator, ValidationStatus
from .pipeline import PipelineBuilder, PipelinePlan
from .debug import DebugCollector, collect_debug_archive
from .proxy import ProxyManager


class KitRunner:
    """Main runner orchestrating pipeline execution"""

    def __init__(self, config_path: Optional[str] = None):
        self.config = self._load_config(config_path)
        self.storage: Optional[JobStorage] = None
        self.executor: Optional[StepExecutor] = None
        self.pipeline_builder: Optional[PipelineBuilder] = None
        self.manifests: Dict[str, Dict[str, Any]] = {}
        self._running_job: Optional[JobCard] = None
        self._shutdown_requested = False

        self._setup_signal_handlers()

    def _load_config(self, config_path: Optional[str]) -> Dict[str, Any]:
        """Load configuration from YAML file"""
        if config_path is None:
            # Try default locations
            default_paths = [
                Path(__file__).parent.parent / 'config.yaml',
                Path('~/.kit/config.yaml').expanduser(),
                Path('./config.yaml'),
            ]
            for path in default_paths:
                if path.exists():
                    config_path = str(path)
                    break
        
        if config_path and Path(config_path).exists():
            with open(config_path, 'r', encoding='utf-8') as f:
                return yaml.safe_load(f)
        
        # Default configuration
        return {
            'llm': {
                'provider': 'openai',
                'model': 'gpt-4o-mini',
                'api_key_env': 'OPENAI_API_KEY',
            },
            'storage': {
                'base_dir': '~/.kit',
                'jobs_db': 'jobs.db',
                'outputs_dir': 'outputs',
                'manifests_dir': 'manifests',
            },
            'runner': {
                'max_retries': 3,
                'retry_base_delay': 1.0,
                'retry_max_delay': 30.0,
                'step_timeout': 3600,
            },
            'web': {
                'host': 'localhost',
                'port': 7700,
            },
        }

    def _setup_signal_handlers(self):
        """Setup graceful shutdown handlers"""
        def handler(signum, frame):
            self._shutdown_requested = True
            if self.executor:
                self.executor.cancel()
            print("\nShutdown requested, finishing current step...")
        
        signal.signal(signal.SIGINT, handler)
        signal.signal(signal.SIGTERM, handler)

    def initialize(self):
        """Initialize storage, executor, and pipeline builder"""
        # Setup paths
        base_dir = Path(self.config.get('storage', {}).get('base_dir', '~/.kit')).expanduser()
        base_dir.mkdir(parents=True, exist_ok=True)

        db_path = base_dir / self.config.get('storage', {}).get('jobs_db', 'jobs.db')
        outputs_dir = base_dir / self.config.get('storage', {}).get('outputs_dir', 'outputs')
        manifests_dir = Path(self.config.get('storage', {}).get('manifests_dir', 'manifests'))
        
        # If manifests_dir is relative, resolve from config location
        if not manifests_dir.is_absolute():
            config_dir = Path(self.config.get('_config_path', '.')).parent
            manifests_dir = config_dir / manifests_dir
        
        # Initialize storage
        self.storage = JobStorage(str(db_path), str(outputs_dir))

        # Load manifests
        self.manifests = self._load_manifests(manifests_dir)

        # Initialize executor
        runner_config = self.config.get('runner', {})
        tool_config = self.config.get('tools', {})  # Tool-specific config (e.g., proxy)
        self.executor = StepExecutor(
            max_retries=runner_config.get('max_retries', 3),
            base_delay=runner_config.get('retry_base_delay', 1.0),
            max_delay=runner_config.get('retry_max_delay', 30.0),
            timeout=runner_config.get('step_timeout', 3600),
            manifests=self.manifests,
            tool_config=tool_config,  # Pass tool-specific config
        )

        # Initialize pipeline builder
        llm_config = self.config.get('llm', {})
        self.pipeline_builder = PipelineBuilder(
            manifests_dir=str(manifests_dir),
            llm_provider=llm_config.get('provider', 'openai'),
            llm_model=llm_config.get('model', 'gpt-4o-mini'),
            api_key_env=llm_config.get('api_key_env', 'OPENAI_API_KEY'),
            api_key_value=llm_config.get('api_key'),  # Direct API key
            api_base=llm_config.get('api_base'),  # Custom API base
            temperature=llm_config.get('temperature', 0.1),
            max_tokens=llm_config.get('max_tokens', 2048),
            detect_shortcuts=self.config.get('pipeline', {}).get('detect_shortcuts', True),
        )

        print(f"Initialized runner with {len(self.manifests)} manifests")
        print(f"Storage: {db_path}")
        print(f"Outputs: {outputs_dir}")

    def _load_manifests(self, manifests_dir: Path) -> Dict[str, Dict[str, Any]]:
        """Load all manifests from directory"""
        manifests = {}
        
        # Try multiple locations
        search_dirs = [
            manifests_dir,
            Path(__file__).parent.parent / 'manifests',
            Path('./manifests'),
        ]
        
        for search_dir in search_dirs:
            if search_dir.exists():
                for manifest_file in search_dir.glob('*.yaml'):
                    try:
                        with open(manifest_file, 'r', encoding='utf-8') as f:
                            manifest = yaml.safe_load(f)
                            tool_name = manifest.get('tool', manifest_file.stem)
                            manifests[tool_name] = manifest
                    except Exception as e:
                        print(f"Warning: Could not load {manifest_file}: {e}")
                break
        
        return manifests

    def run_goal(
        self,
        goal: str,
        input_data: Dict[str, Any],
        expected_output: Optional[List[str]] = None,
        step_by_step: bool = False,
    ) -> JobCard:
        """
        Run a goal by building and executing a pipeline.
        
        Args:
            goal: Natural language description of the goal
            input_data: Input parameters
            expected_output: Expected output types
            step_by_step: If True, wait for confirmation between steps
        
        Returns:
            Completed JobCard
        """
        if not self.pipeline_builder:
            self.initialize()

        # Build pipeline
        print(f"Building pipeline for goal: {goal}")
        plan = self.pipeline_builder.build_pipeline(goal, input_data, expected_output)
        
        if plan.shortcut_detected:
            print(f"Shortcut detected: {plan.shortcut_reason}")
        
        # Create job
        job = self.pipeline_builder.create_job_from_plan(goal, input_data, plan, expected_output)
        
        # Execute pipeline
        return self.execute_job(job, step_by_step=step_by_step)

    def execute_job(
        self,
        job: JobCard,
        step_by_step: bool = False,
        start_step: int = 0,
    ) -> JobCard:
        """
        Execute a job pipeline.
        
        Args:
            job: JobCard to execute
            step_by_step: If True, wait for confirmation between steps
            start_step: Step index to start from (for resume)
        
        Returns:
            Updated JobCard
        """
        if not self.storage:
            self.initialize()

        self._running_job = job
        job.status = JobStatus.RUNNING
        self.storage.save_job(job)

        job_dir = self.storage.create_output_dir(job.job_id)
        previous_outputs: Dict[str, Any] = {}

        print(f"\nStarting job {job.job_id}")
        print(f"Goal: {job.goal}")
        print(f"Steps: {len(job.steps)}")

        for i, step in enumerate(job.steps):
            if self._shutdown_requested:
                job.status = JobStatus.PAUSED
                job.error_message = "Shutdown requested"
                self.storage.save_job(job)
                break

            # Skip completed steps (resume support)
            if i < start_step or step.status == StepStatus.COMPLETED:
                print(f"\n[SKIP] Step {i+1}/{len(job.steps)}: {step.step_name} (already completed)")
                # Restore outputs from completed step
                for key, value in step.output_params.items():
                    previous_outputs[key] = value
                continue

            # Skip skipped steps
            if step.status == StepStatus.SKIPPED:
                continue

            print(f"\n[STEP {i+1}/{len(job.steps)}] {step.step_name}")
            print(f"  Tool: {step.tool}, Mode: {step.mode}")

            # Step-by-step confirmation
            if step_by_step:
                confirm = input("  Execute this step? [Y/n]: ").strip().lower()
                if confirm in ('n', 'no'):
                    step.status = StepStatus.SKIPPED
                    self.storage.save_job(job)
                    continue

            # Execute step
            step.status = StepStatus.RUNNING
            step.started_at = datetime.now(timezone.utc).isoformat()
            self.storage.save_job(job)

            result = self.executor.execute_step(step, job_dir, previous_outputs, job.input_data)

            step.completed_at = datetime.now(timezone.utc).isoformat()
            step.duration_seconds = result.duration_seconds

            if result.status == ExecutionStatus.SUCCESS:
                step.status = StepStatus.COMPLETED
                step.output_params = result.output_params
                step.output_files = result.output_files
                step.preview = result.preview
                
                # Validate output
                manifest = self.manifests.get(step.tool, {})
                modes = manifest.get('modes', {})
                mode_config = modes.get(step.mode, {})
                # Merge mode-specific outputs into manifest for validation
                mode_outputs = mode_config.get('output', {})
                if mode_outputs:
                    # Create a validation schema with mode-specific outputs
                    validation_schema = {**manifest, 'outputs': {}}
                    for out_name, out_rules in mode_outputs.items():
                        file_rule = out_rules.get('file', True)
                        # file can be bool (True) or dict with 'extension'
                        if isinstance(file_rule, dict):
                            validation_schema['outputs'][out_name] = {'file': file_rule}
                        else:
                            validation_schema['outputs'][out_name] = {'file': True}
                else:
                    validation_schema = manifest
                validator = OutputValidator(validation_schema)
                validation = validator.validate(result.output_params, result.output_files)
                step.validation_result = validation.to_dict()

                # Store outputs for next steps
                for key, value in result.output_params.items():
                    previous_outputs[key] = value
                
                print(f"  ✓ Completed in {result.duration_seconds:.1f}s")
                if result.output_files:
                    print(f"  Output: {', '.join(result.output_files)}")

            elif result.status == ExecutionStatus.FAILED:
                step.status = StepStatus.FAILED
                step.error_message = result.error_message
                job.status = JobStatus.FAILED
                job.error_message = f"Step {i+1} failed: {result.error_message}"
                print(f"  [FAILED] {result.error_message}")
                self.storage.save_job(job)
                break

            elif result.status == ExecutionStatus.TIMEOUT:
                step.status = StepStatus.FAILED
                step.error_message = result.error_message
                job.status = JobStatus.FAILED
                job.error_message = f"Step {i+1} timed out"
                print(f"  [TIMEOUT] {result.error_message}")
                self.storage.save_job(job)
                break

            elif result.status == ExecutionStatus.CANCELLED:
                step.status = StepStatus.PENDING
                job.status = JobStatus.PAUSED
                print(f"  [CANCELLED]")
                self.storage.save_job(job)
                break

            self.storage.save_job(job)

        # Check if all steps completed
        if job.status == JobStatus.RUNNING:
            all_completed = all(s.status == StepStatus.COMPLETED for s in job.steps)
            if all_completed:
                job.status = JobStatus.COMPLETED
                job.completed_at = datetime.now(timezone.utc).isoformat()
                print(f"\n[OK] Job completed successfully!")
            else:
                job.status = JobStatus.FAILED
                job.completed_at = datetime.now(timezone.utc).isoformat()

        self.storage.save_job(job)
        self._running_job = None
        return job

    def resume_job(self, job_id: str, step_by_step: bool = False) -> JobCard:
        """Resume a paused or interrupted job"""
        if not self.storage:
            self.initialize()

        job = self.storage.get_job(job_id)
        if not job:
            raise ValueError(f"Job {job_id} not found")

        if job.status in (JobStatus.COMPLETED, JobStatus.CANCELLED):
            raise ValueError(f"Cannot resume job with status {job.status}")

        # Find first incomplete step
        start_step = job.get_current_step_index()
        
        # Reset running steps to pending
        for step in job.steps[start_step:]:
            if step.status in (StepStatus.RUNNING, StepStatus.RETRYING):
                step.status = StepStatus.PENDING
                step.error_message = None

        print(f"Resuming job {job_id} from step {start_step + 1}")
        return self.execute_job(job, step_by_step=step_by_step, start_step=start_step)

    def get_job(self, job_id: str) -> Optional[JobCard]:
        """Get job by ID"""
        if not self.storage:
            self.initialize()
        return self.storage.get_job(job_id)

    def list_jobs(self, limit: int = 50) -> List[JobCard]:
        """List recent jobs"""
        if not self.storage:
            self.initialize()
        return self.storage.list_jobs(limit=limit)

    def get_resumable_jobs(self) -> List[JobCard]:
        """Get jobs that can be resumed"""
        if not self.storage:
            self.initialize()
        return self.storage.get_resumable_jobs()

    def get_manifest(self, tool_name: str) -> Optional[Dict[str, Any]]:
        """Get manifest for a tool"""
        return self.manifests.get(tool_name)

    def get_available_tools(self) -> List[Dict[str, Any]]:
        """Get list of available tools"""
        return self.pipeline_builder.get_available_tools() if self.pipeline_builder else []

    def debug_job(
        self,
        job_id: str,
        output_dir: Optional[str] = None
    ) -> Optional[Dict[str, Any]]:
        """
        Collect debug archive for a failed job.
        
        Args:
            job_id: ID of the job to debug
            output_dir: Output directory for debug archive
            
        Returns:
            Debug archive information or None if job not found
        """
        if not self.storage:
            self.initialize()
        
        job = self.storage.get_job(job_id)
        if not job:
            return None
        
        # Find failed step
        failed_step_index = None
        for i, step in enumerate(job.steps):
            if step.status == StepStatus.FAILED:
                failed_step_index = i
                break
        
        # Create debug collector
        storage_outputs_dir = Path(self.config.get('storage', {}).get('outputs_dir', 'outputs'))
        base_dir = Path(self.config.get('storage', {}).get('base_dir', '~/.kit')).expanduser()
        default_output = base_dir / 'logs'
        
        collector = DebugCollector(
            storage=self.storage,
            output_dir=Path(output_dir) if output_dir else default_output
        )
        
        archive = collector.collect_for_job(
            job,
            failed_step_index,
            self.manifests
        )
        
        return archive.to_dict()


def main():
    """CLI entry point"""
    parser = argparse.ArgumentParser(description='Kit Runner - Universal Tool Runner')
    parser.add_argument('--config', '-c', help='Path to config file')
    parser.add_argument('--goal', '-g', help='Natural language goal to execute')
    parser.add_argument('--input', '-i', action='append', help='Input parameter (key=value)')
    parser.add_argument('--resume', '-r', help='Resume job by ID')
    parser.add_argument('--step-by-step', '-s', action='store_true', help='Confirm each step')
    parser.add_argument('--list', '-l', action='store_true', help='List recent jobs')
    parser.add_argument('--tools', '-t', action='store_true', help='List available tools')
    parser.add_argument('--debug-job', '-d', help='Collect debug archive for failed job by ID')
    parser.add_argument('--debug-output', help='Output directory for debug archive (default: ~/.kit/logs)')

    args = parser.parse_args()

    runner = KitRunner(config_path=args.config)
    runner.initialize()

    if args.list:
        jobs = runner.list_jobs()
        print(f"\nRecent jobs ({len(jobs)}):")
        for job in jobs[:10]:
            print(f"  {job.job_id[:8]}... | {job.status.value:10} | {job.goal[:50]}")
        return

    if args.tools:
        tools = runner.get_available_tools()
        print(f"\nAvailable tools ({len(tools)}):")
        for tool in tools:
            print(f"  {tool['name']}: {tool['description']}")
            print(f"    Modes: {', '.join(tool['modes'])}")
        return

    if args.debug_job:
        archive_info = runner.debug_job(args.debug_job, output_dir=args.debug_output)
        if archive_info:
            print(f"\nDebug archive created:")
            print(f"  Path: {archive_info['path']}")
            print(f"  Size: {archive_info['size_mb']} MB")
            print(f"  Contents: {len(archive_info['contents'])} files")
            print(f"  Sanitized items: {archive_info['sanitized_items']}")
            print(f"\nFiles in archive:")
            for f in archive_info['contents'][:20]:
                print(f"    {f}")
            if len(archive_info['contents']) > 20:
                print(f"    ... and {len(archive_info['contents']) - 20} more")
        else:
            print(f"Error: Job {args.debug_job} not found")
        return

    if args.resume:
        try:
            job = runner.resume_job(args.resume, step_by_step=args.step_by_step)
            print(f"\nJob {job.job_id} finished with status: {job.status.value}")
        except ValueError as e:
            print(f"Error: {e}")
        return

    if args.goal:
        # Parse input parameters
        input_data = {}
        if args.input:
            for param in args.input:
                if '=' in param:
                    key, value = param.split('=', 1)
                    input_data[key] = value
        
        job = runner.run_goal(args.goal, input_data, step_by_step=args.step_by_step)
        print(f"\nJob {job.job_id} finished with status: {job.status.value}")
        
        if job.status == JobStatus.COMPLETED:
            print("\nOutput files:")
            for step in job.steps:
                for f in step.output_files:
                    print(f"  {f}")
        return

    # No command specified
    parser.print_help()


if __name__ == '__main__':
    main()
