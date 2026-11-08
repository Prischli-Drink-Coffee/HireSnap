"""Celery application, task registry and beat schedule.

Celery itself is imported only inside `build_celery_app()`, so this package
imports on an image that has no celery installed (the API image does not need it
for a plain request; only a worker process does).
"""
