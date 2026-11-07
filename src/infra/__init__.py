"""Platform infrastructure: cache, object storage, mail.

Everything here is optional at runtime. Each module picks a backend from an
environment flag, defaults to the behaviour HireSnap had before the platform
layer existed, and imports its third-party driver lazily so that a bare install
(no redis, no boto3) can still import and run the API.
"""
