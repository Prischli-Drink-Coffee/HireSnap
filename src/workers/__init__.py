"""Celery workers: application, task implementations, beat schedule.

Importing this package must not require celery. src/workers/celery_app.py
builds the Celery application inside a factory, and callers that only need the
routing table (tests, the inline fallback in src/services/jobs_services.py) read
the plain module-level data instead.
"""
