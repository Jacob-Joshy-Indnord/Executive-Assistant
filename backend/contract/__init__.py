"""Reusable contract tests for the boundary and the store.

Each suite is a mixin. To run it against an implementation, subclass it
together with ``unittest.IsolatedAsyncioTestCase`` and implement ``make``.
Pytest and ``python -m unittest`` both collect such subclasses unchanged.
"""
