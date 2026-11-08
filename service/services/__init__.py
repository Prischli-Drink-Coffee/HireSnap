"""Business rules, one module per vertical.

A service composes repositories, applies the rule and raises HTTPException when
the request cannot be satisfied; that exception is what the router surfaces
unchanged.
"""
