"""
Pipeline Builder Module
Uses LiteLLM to build tool pipelines from natural language goals.
Detects shortcuts and optimizes step ordering.
"""

import os
import json
from pathlib import Path
from typing import Dict, Any, List, Optional, Tuple
from dataclasses import dataclass

try:
    import litellm
    LITELLM_AVAILABLE = True
except ImportError:
    LITELLM_AVAILABLE = False

from .job import StepCard, create_job, JobCard


@dataclass
class PipelineStep:
    """A step in the generated pipeline"""
    tool: str
    mode: str
    input_params: Dict[str, Any]
    description: str


@dataclass
class PipelinePlan:
    """Complete pipeline plan from LLM"""
    steps: List[PipelineStep]
    shortcut_detected: bool
    shortcut_reason: Optional[str]
    manifest_refs: List[str]
    confidence: float


class PipelineBuilder:
    """Builds tool pipelines using LLM"""

    def __init__(
        self,
        manifests_dir: str,
        llm_provider: str = "openai",
        llm_model: str = "gpt-4o-mini",
        api_key_env: str = "OPENAI_API_KEY",
        api_key_value: str = None,  # Direct API key value
        api_base: str = None,  # Custom API base URL
        detect_shortcuts: bool = True,
        max_steps: int = 10,
        temperature: float = 0.1,
        max_tokens: int = 2048,
    ):
        self.manifests_dir = Path(manifests_dir)
        self.llm_provider = llm_provider
        self.llm_model = llm_model
        self.api_key_env = api_key_env
        self.api_key_value = api_key_value
        self.api_base = api_base
        self.detect_shortcuts = detect_shortcuts
        self.max_steps = max_steps
        self.manifests = self._load_manifests()
        self.temperature = temperature
        self.max_tokens = max_tokens

    def _load_manifests(self) -> Dict[str, Dict[str, Any]]:
        """Load all manifests from directory"""
        manifests = {}
        if not self.manifests_dir.exists():
            return manifests
        
        for manifest_file in self.manifests_dir.glob("*.yaml"):
            try:
                import yaml
                with open(manifest_file, 'r', encoding='utf-8') as f:
                    manifest = yaml.safe_load(f)
                    tool_name = manifest.get('tool', manifest_file.stem)
                    manifests[tool_name] = manifest
            except Exception as e:
                print(f"Warning: Could not load manifest {manifest_file}: {e}")
        
        return manifests

    def build_pipeline(
        self,
        goal: str,
        input_data: Dict[str, Any],
        expected_output: Optional[List[str]] = None,
    ) -> PipelinePlan:
        """
        Build a pipeline from natural language goal.
        
        Args:
            goal: Natural language description of what to achieve
            input_data: Input parameters (e.g., URL, file path)
            expected_output: Expected output types
        
        Returns:
            PipelinePlan with ordered steps
        """
        # Check for shortcuts first
        shortcut_result = self._detect_shortcuts(goal, input_data) if self.detect_shortcuts else None
        
        # Build LLM prompt
        prompt = self._build_prompt(goal, input_data, expected_output, shortcut_result)
        
        # Call LLM
        llm_response = self._call_llm(prompt)
        
        # Parse response
        steps, manifest_refs, confidence = self._parse_llm_response(llm_response)
        
        # Apply shortcut if detected
        if shortcut_result and shortcut_result.get('apply'):
            steps = self._apply_shortcut(steps, shortcut_result)
        
        # Limit steps
        steps = steps[:self.max_steps]
        
        return PipelinePlan(
            steps=steps,
            shortcut_detected=shortcut_result is not None and shortcut_result.get('detected', False),
            shortcut_reason=shortcut_result.get('reason') if shortcut_result else None,
            manifest_refs=manifest_refs,
            confidence=confidence,
        )

    def _detect_shortcuts(
        self,
        goal: str,
        input_data: Dict[str, Any],
    ) -> Optional[Dict[str, Any]]:
        """Detect if any steps can be skipped"""
        goal_lower = goal.lower()
        
        # Check for subtitle-related shortcuts
        if 'subtitle' in goal_lower or 'transcript' in goal_lower or 'caption' in goal_lower:
            # Check if input might already have subtitles
            url = input_data.get('url', '')
            if 'youtube' in url.lower():
                # YouTube often has auto-generated captions
                return {
                    'detected': True,
                    'apply': True,
                    'reason': 'YouTube videos often have auto-generated captions available',
                    'skip_tools': ['whisper'],
                    'alternative': 'yt-dlp with --write-subs option',
                }
        
        # Check for audio-only requests
        if 'audio' in goal_lower and 'video' not in goal_lower:
            return {
                'detected': True,
                'apply': True,
                'reason': 'Audio-only requested, skip video processing',
                'skip_modes': ['video'],
                'prefer_modes': ['audio'],
            }
        
        return None

    def _build_prompt(
        self,
        goal: str,
        input_data: Dict[str, Any],
        expected_output: Optional[List[str]],
        shortcut_result: Optional[Dict[str, Any]],
    ) -> str:
        """Build the LLM prompt"""
        # Create tool summaries
        tool_summaries = []
        for tool_name, manifest in self.manifests.items():
            modes = manifest.get('modes', {})
            mode_list = []
            for mode_name, mode_config in modes.items():
                mode_list.append(f"  - {mode_name}: {mode_config.get('description', '')}")
            
            tool_summaries.append({
                'name': tool_name,
                'description': manifest.get('description', ''),
                'modes': mode_list,
                'inputs': list(manifest.get('inputs', {}).keys()),
                'outputs': list(manifest.get('outputs', {}).keys()),
                'relationships': manifest.get('relationships', []),
            })
        
        # Build shortcut section separately to avoid f-string backslash issue
        shortcut_section = ""
        if shortcut_result and shortcut_result.get('detected'):
            shortcut_section = f"## Shortcut Detected\n{shortcut_result['reason']}\nConsider skipping: {shortcut_result.get('skip_tools', [])}\n"

        prompt = f"""You are a pipeline builder for a tool runner system. Your task is to create an ordered list of tool steps to achieve a goal.

## Available Tools

{json.dumps(tool_summaries, indent=2)}

## Goal

{goal}

## Input Data

{json.dumps(input_data, indent=2)}

## Expected Output

{json.dumps(expected_output or [], indent=2)}

{shortcut_section}
## Instructions

1. Select the minimum number of tools needed to achieve the goal
2. Order them logically (dependencies first)
3. For each step, specify:
   - tool: The tool name from available tools
   - mode: The mode to use
   - input_params: Parameters for this step
   - description: Brief description of what this step does

4. Use previous step outputs by referencing them as "$prev.output_name"
5. Consider tool relationships and dependencies

## Response Format

Respond with a JSON object in this exact format:

```json
{{
    "steps": [
        {{
            "tool": "tool_name",
            "mode": "mode_name",
            "input_params": {{"param1": "value1"}},
            "description": "What this step does"
        }}
    ],
    "manifest_refs": ["tool1", "tool2"],
    "confidence": 0.95
}}
```

Only respond with the JSON object, no other text.
"""

        return prompt

    def _call_llm(self, prompt: str) -> str:
        """Call LLM using LiteLLM"""
        if not LITELLM_AVAILABLE:
            # Fallback to simple rule-based pipeline
            return self._fallback_pipeline(prompt)

        # Get API key from direct value or environment
        api_key = self.api_key_value or os.environ.get(self.api_key_env)

        try:
            # Build completion kwargs
            kwargs = {
                "model": f"{self.llm_provider}/{self.llm_model}",
                "messages": [{"role": "user", "content": prompt}],
                "temperature": self.temperature,
                "max_tokens": self.max_tokens,
                "api_key": api_key,
                "timeout": 30,  # 30 second timeout
            }
            
            # Add custom API base if provided
            if self.api_base:
                kwargs["api_base"] = self.api_base
            
            response = litellm.completion(**kwargs)
            return response.choices[0].message.content
        except Exception as e:
            print(f"LLM call failed: {e}, using fallback")
            return self._fallback_pipeline(prompt)

    def _fallback_pipeline(self, prompt: str) -> str:
        """Fallback rule-based pipeline generation"""
        # Extract goal from prompt (it's between "## Goal" and "## Input Data")
        import re
        goal_match = re.search(r'## Goal\s*\n(.*?)\n## Input Data', prompt, re.DOTALL)
        goal_text = goal_match.group(1).strip().lower() if goal_match else prompt.lower()
        
        steps = []
        manifest_refs = []
        
        # Detect audio-only intent (English and Russian) — based on GOAL only
        audio_intent = any(kw in goal_text for kw in ['аудио', 'только звук', 'only audio', 'extract audio'])
        # Don't use 'audio' alone — it appears in tool summaries; use Russian 'аудио' or phrases
        video_intent = any(kw in goal_text for kw in ['видео', 'video', 'download video'])
        
        if 'youtube' in prompt.lower() or 'download' in prompt.lower() or 'скачать' in goal_text:
            if audio_intent and not video_intent:
                # Audio-only mode
                steps.append({
                    'tool': 'yt-dlp',
                    'mode': 'audio_only',
                    'input_params': {'url': '$input.url'},
                    'description': 'Download audio from YouTube'
                })
            else:
                # Full video download
                steps.append({
                    'tool': 'yt-dlp',
                    'mode': 'download',
                    'input_params': {'url': '$input.url'},
                    'description': 'Download video from URL'
                })
            manifest_refs.append('yt-dlp')
        
        if 'transcribe' in goal_text or 'subtitle' in goal_text or 'субтитр' in goal_text or 'транскриб' in goal_text:
            if 'youtube' in prompt.lower():
                # Try to get subs from yt-dlp first
                steps.append({
                    'tool': 'yt-dlp',
                    'mode': 'subtitles',
                    'input_params': {'url': '$input.url', 'lang': 'ru'},
                    'description': 'Extract subtitles from video'
                })
                manifest_refs.append('yt-dlp')
                # Then transcribe with whisper if no subs or for verification
                steps.append({
                    'tool': 'whisper',
                    'mode': 'transcribe',
                    'input_params': {'audio_file': '$prev.output_file', 'language': 'ru', 'language_param': ' --language ru'},
                    'description': 'Transcribe audio to text'
                })
                manifest_refs.append('whisper')
            else:
                steps.append({
                    'tool': 'whisper',
                    'mode': 'transcribe',
                    'input_params': {'audio_file': '$prev.output_file'},
                    'description': 'Transcribe audio to text'
                })
                manifest_refs.append('whisper')
        
        if 'convert' in goal_text or 'конверт' in goal_text or 'format' in goal_text or 'формат' in goal_text:
            steps.append({
                'tool': 'ffmpeg',
                'mode': 'convert',
                'input_params': {'input_file': '$prev.output_file'},
                'description': 'Convert media format'
            })
            manifest_refs.append('ffmpeg')
        
        if not steps:
            steps.append({
                'tool': 'yt-dlp',
                'mode': 'download',
                'input_params': {'url': '$input.url'},
                'description': 'Download content'
            })
            manifest_refs.append('yt-dlp')
        
        return json.dumps({
            'steps': steps,
            'manifest_refs': list(set(manifest_refs)),
            'confidence': 0.7
        })

    def _parse_llm_response(
        self,
        response: str,
    ) -> Tuple[List[PipelineStep], List[str], float]:
        """Parse LLM response into structured format"""
        # Extract JSON from response
        import re
        json_match = re.search(r'\{.*\}', response, re.DOTALL)
        if json_match:
            response = json_match.group(0)
        
        try:
            data = json.loads(response)
        except json.JSONDecodeError:
            # Fallback parsing
            data = {
                'steps': [],
                'manifest_refs': [],
                'confidence': 0.5
            }
        
        steps = []
        for step_data in data.get('steps', []):
            steps.append(PipelineStep(
                tool=step_data.get('tool', 'unknown'),
                mode=step_data.get('mode', 'default'),
                input_params=step_data.get('input_params', {}),
                description=step_data.get('description', ''),
            ))
        
        manifest_refs = data.get('manifest_refs', [])
        confidence = float(data.get('confidence', 0.5))
        
        return steps, manifest_refs, min(max(confidence, 0.0), 1.0)

    def _apply_shortcut(
        self,
        steps: List[PipelineStep],
        shortcut_result: Dict[str, Any],
    ) -> List[PipelineStep]:
        """Apply shortcut to skip unnecessary steps"""
        skip_tools = shortcut_result.get('skip_tools', [])
        
        filtered_steps = [
            step for step in steps
            if step.tool not in skip_tools
        ]
        
        return filtered_steps

    def create_job_from_plan(
        self,
        goal: str,
        input_data: Dict[str, Any],
        plan: PipelinePlan,
        expected_output: Optional[List[str]] = None,
    ) -> JobCard:
        """Create a JobCard from a pipeline plan"""
        steps = []
        for i, step_plan in enumerate(plan.steps):
            step = StepCard(
                step_id=f"step_{i:03d}",
                step_name=step_plan.description,
                tool=step_plan.tool,
                mode=step_plan.mode,
                input_params=step_plan.input_params,
            )
            steps.append(step)
        
        return create_job(
            goal=goal,
            input_data=input_data,
            expected_output=expected_output or [],
            steps=steps,
            manifest_refs=plan.manifest_refs,
            pipeline_config={
                'shortcut_detected': plan.shortcut_detected,
                'shortcut_reason': plan.shortcut_reason,
                'confidence': plan.confidence,
            },
        )

    def get_available_tools(self) -> List[Dict[str, Any]]:
        """Get list of available tools from manifests"""
        tools = []
        for tool_name, manifest in self.manifests.items():
            tools.append({
                'name': tool_name,
                'description': manifest.get('description', ''),
                'modes': list(manifest.get('modes', {}).keys()),
                'inputs': manifest.get('inputs', {}),
                'health_check': manifest.get('health_check'),
            })
        return tools


def build_pipeline(
    goal: str,
    input_data: Dict[str, Any],
    manifests_dir: str,
    expected_output: Optional[List[str]] = None,
    llm_config: Optional[Dict[str, Any]] = None,
) -> Tuple[JobCard, PipelinePlan]:
    """
    Convenience function to build a pipeline and create a job.
    
    Returns:
        Tuple of (JobCard, PipelinePlan)
    """
    llm_config = llm_config or {}
    
    builder = PipelineBuilder(
        manifests_dir=manifests_dir,
        llm_provider=llm_config.get('provider', 'openai'),
        llm_model=llm_config.get('model', 'gpt-4o-mini'),
        api_key_env=llm_config.get('api_key_env', 'OPENAI_API_KEY'),
        detect_shortcuts=llm_config.get('detect_shortcuts', True),
    )
    
    plan = builder.build_pipeline(goal, input_data, expected_output)
    job = builder.create_job_from_plan(goal, input_data, plan, expected_output)
    
    return job, plan
