"""Raw SQL, one module per table.

A repository takes a model or a dict and returns rows. It never raises an
HTTPException: mapping "no such row" to 404 is a business rule and belongs in
service/services/.
"""
