"""R2-A isolated shadow runner and scheduler reliability helpers.

This package is intentionally self-contained.  It does not import QwenPaw
agent APIs and it never sends email.  The production scheduler is expected to
invoke ``launcher.py`` directly (or through a supervised process) rather than
asking an agent to "start" the pipeline.
"""

__all__ = ["run_state", "launcher", "scheduler", "watchdog"]
