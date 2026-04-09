"""
Output Validator Module
Validates step outputs against manifest schema requirements.
"""

import os
import re
import json
import subprocess
from pathlib import Path
from dataclasses import dataclass
from typing import Dict, Any, List, Optional, Tuple
from enum import Enum


class ValidationStatus(str, Enum):
    VALID = "valid"
    INVALID = "invalid"
    WARNING = "warning"
    SKIPPED = "skipped"


@dataclass
class ValidationResult:
    """Result of output validation"""
    status: ValidationStatus
    message: str
    details: Dict[str, Any] = None
    warnings: List[str] = None

    def __post_init__(self):
        if self.details is None:
            self.details = {}
        if self.warnings is None:
            self.warnings = []

    def to_dict(self) -> Dict[str, Any]:
        return {
            'status': self.status.value,
            'message': self.message,
            'details': self.details,
            'warnings': self.warnings,
        }


class OutputValidator:
    """Validates outputs from tool execution steps"""

    def __init__(self, manifest_schema: Dict[str, Any]):
        self.schema = manifest_schema
        self.version = manifest_schema.get('schema_version', '0.1.0')

    def validate(self, output_params: Dict[str, Any], output_files: List[str]) -> ValidationResult:
        """
        Validate step output against manifest requirements.
        
        Args:
            output_params: Dictionary of output parameters from step execution
            output_files: List of output file paths
        
        Returns:
            ValidationResult with status and details
        """
        outputs_def = self.schema.get('outputs', {})
        if not outputs_def:
            return ValidationResult(
                status=ValidationStatus.SKIPPED,
                message="No output validation rules defined in manifest"
            )

        all_valid = True
        warnings = []
        details = {}

        # Validate each expected output
        for output_name, output_rules in outputs_def.items():
            print(f"    [VALIDATE] Checking {output_name}: {output_rules}")
            result, warn = self._validate_output(output_name, output_rules, output_params, output_files)
            print(f"    [VALIDATE] Result: {result}, warnings: {warn}")
            if not result:
                all_valid = False
                details[output_name] = {'valid': False, 'error': warn}
            else:
                details[output_name] = {'valid': True}
            if warn:
                warnings.extend(warn if isinstance(warn, list) else [warn])

        if all_valid:
            status = ValidationStatus.WARNING if warnings else ValidationStatus.VALID
            return ValidationResult(
                status=status,
                message="All outputs validated successfully" if not warnings else "Validated with warnings",
                details=details,
                warnings=warnings
            )
        else:
            return ValidationResult(
                status=ValidationStatus.INVALID,
                message="Output validation failed",
                details=details,
                warnings=warnings
            )

    def _validate_output(
        self, 
        output_name: str, 
        rules: Dict[str, Any],
        output_params: Dict[str, Any],
        output_files: List[str]
    ) -> Tuple[bool, Optional[List[str]]]:
        """Validate a single output against its rules"""
        warnings = []
        
        # Check file output
        if 'file' in rules:
            file_rules = rules['file']
            
            # Handle shorthand: file: True means "required file, no extra constraints"
            if isinstance(file_rules, bool):
                if file_rules:
                    # Check if file exists in output_params or output_files
                    file_found = (
                        output_name in output_params
                        or any(Path(f).name.startswith(output_name) for f in output_files)
                        or len(output_files) > 0
                    )
                    if not file_found:
                        return False, [f"Required file output '{output_name}' not found"]
                # If file_rules is False, nothing to check
                return True, warnings
            
            file_param = file_rules.get('param', output_name)
            file_path = output_params.get(file_param)
            
            if file_path:
                valid, warn = self._validate_file(file_path, file_rules)
                if not valid:
                    return False, warn
                if warn:
                    warnings.extend(warn if isinstance(warn, list) else [warn])
            elif file_rules.get('required', True):
                return False, [f"Required file output '{output_name}' not found"]
            
            # Check extension from file rules
            if 'extension' in file_rules:
                ext_rules = file_rules['extension']
                expected_exts = ext_rules if isinstance(ext_rules, list) else [ext_rules]
                found_ext = False
                
                # Check the file_path extension
                if file_path:
                    actual_ext = Path(file_path).suffix.lower().lstrip('.')
                    if actual_ext in [e.lower().lstrip('.') for e in expected_exts]:
                        found_ext = True
                
                # Also check output_files
                if not found_ext:
                    for f in output_files:
                        ext = Path(f).suffix.lower().lstrip('.')
                        if ext in [e.lower().lstrip('.') for e in expected_exts]:
                            found_ext = True
                            break
                
                if not found_ext and file_rules.get('required', True):
                    return False, [f"Unexpected file extension: expected {expected_exts}"]

        # Check parameter output
        if 'param' in rules:
            param_rules = rules['param']
            param_name = param_rules.get('name', output_name)
            param_value = output_params.get(param_name)
            
            if param_value is not None:
                valid, warn = self._validate_param_value(param_value, param_rules)
                if not valid:
                    return False, warn
                if warn:
                    warnings.extend(warn if isinstance(warn, list) else [warn])
            elif param_rules.get('required', True):
                return False, [f"Required parameter '{param_name}' not found"]

        # Check file extension at top level (legacy support)
        if 'extension' in rules and 'extension' not in rules.get('file', {}):
            ext_rules = rules['extension']
            expected_exts = ext_rules if isinstance(ext_rules, list) else [ext_rules]
            found_ext = False
            
            for f in output_files:
                ext = Path(f).suffix.lower().lstrip('.')
                if ext in [e.lower().lstrip('.') for e in expected_exts]:
                    found_ext = True
                    break
            
            if not found_ext and rules.get('required', True):
                return False, [f"Expected file extension: {expected_exts}"]

        return True, warnings

    def _validate_file(self, file_path: str, rules: Dict[str, Any]) -> Tuple[bool, Optional[List[str]]]:
        """Validate a file output"""
        warnings = []
        path = Path(file_path)

        # Check file exists
        if not path.exists():
            return False, [f"File does not exist: {file_path}"]

        # Check file size
        if 'min_size' in rules:
            if path.stat().st_size < rules['min_size']:
                return False, [f"File size {path.stat().st_size} < minimum {rules['min_size']}"]
        
        if 'max_size' in rules:
            if path.stat().st_size > rules['max_size']:
                warnings.append(f"File size {path.stat().st_size} > maximum {rules['max_size']}")

        # Check file extension
        if 'extension' in rules:
            expected = rules['extension']
            expected_list = expected if isinstance(expected, list) else [expected]
            actual_ext = path.suffix.lower().lstrip('.')
            if actual_ext not in [e.lower().lstrip('.') for e in expected_list]:
                return False, [f"Unexpected extension: {actual_ext}, expected: {expected_list}"]

        # Check file format using ffprobe for media files
        if rules.get('probe', False):
            probe_result = self._probe_media(file_path)
            if probe_result:
                if 'video_codec' in rules and probe_result.get('video_codec') not in rules['video_codec']:
                    return False, [f"Unexpected video codec: {probe_result.get('video_codec')}"]
                if 'audio_codec' in rules and probe_result.get('audio_codec') not in rules['audio_codec']:
                    return False, [f"Unexpected audio codec: {probe_result.get('audio_codec')}"]

        return True, warnings

    def _validate_param_value(self, value: Any, rules: Dict[str, Any]) -> Tuple[bool, Optional[List[str]]]:
        """Validate a parameter value"""
        warnings = []

        # Type check
        if 'type' in rules:
            expected_type = rules['type']
            if expected_type == 'string' and not isinstance(value, str):
                return False, [f"Expected string, got {type(value).__name__}"]
            elif expected_type == 'integer' and not isinstance(value, int):
                return False, [f"Expected integer, got {type(value).__name__}"]
            elif expected_type == 'float' and not isinstance(value, (int, float)):
                return False, [f"Expected float, got {type(value).__name__}"]
            elif expected_type == 'boolean' and not isinstance(value, bool):
                return False, [f"Expected boolean, got {type(value).__name__}"]
            elif expected_type == 'array' and not isinstance(value, list):
                return False, [f"Expected array, got {type(value).__name__}"]
            elif expected_type == 'object' and not isinstance(value, dict):
                return False, [f"Expected object, got {type(value).__name__}"]

        # Range check
        if 'min' in rules and value < rules['min']:
            return False, [f"Value {value} < minimum {rules['min']}"]
        if 'max' in rules and value > rules['max']:
            warnings.append(f"Value {value} > maximum {rules['max']}")

        # Pattern check for strings
        if 'pattern' in rules and isinstance(value, str):
            if not re.match(rules['pattern'], value):
                return False, [f"Value does not match pattern: {rules['pattern']}"]

        # Enum check
        if 'enum' in rules and value not in rules['enum']:
            return False, [f"Value {value} not in allowed values: {rules['enum']}"]

        return True, warnings

    def _probe_media(self, file_path: str) -> Optional[Dict[str, Any]]:
        """Probe media file using ffprobe"""
        try:
            result = subprocess.run(
                [
                    'ffprobe', '-v', 'quiet', '-print_format', 'json',
                    '-show_format', '-show_streams', file_path
                ],
                capture_output=True,
                text=True,
                timeout=30
            )
            if result.returncode == 0:
                data = json.loads(result.stdout)
                info = {}
                for stream in data.get('streams', []):
                    if stream.get('codec_type') == 'video':
                        info['video_codec'] = stream.get('codec_name')
                        info['width'] = stream.get('width')
                        info['height'] = stream.get('height')
                    elif stream.get('codec_type') == 'audio':
                        info['audio_codec'] = stream.get('codec_name')
                        info['sample_rate'] = stream.get('sample_rate')
                format_info = data.get('format', {})
                info['duration'] = float(format_info.get('duration', 0))
                info['size'] = int(format_info.get('size', 0))
                return info
        except (subprocess.TimeoutExpired, FileNotFoundError, json.JSONDecodeError):
            pass
        return None


def validate_output(
    manifest: Dict[str, Any],
    output_params: Dict[str, Any],
    output_files: List[str]
) -> ValidationResult:
    """Convenience function to validate output"""
    validator = OutputValidator(manifest)
    return validator.validate(output_params, output_files)
