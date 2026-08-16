"""Queue names shared by producers and consumers without importing Celery."""

TASK_PROCESS_JOB = "invest_research.process_research_job"

__all__ = ["TASK_PROCESS_JOB"]
