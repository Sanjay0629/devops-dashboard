"""Service operations: the lifecycle of a start, stop or restart (M4.2, ``OPEN-67`` - ``OPEN-77``).

``states``, ``codes`` and ``evaluator`` are pure -- no database, no framework -- so the rules the
integration tests then exercise against PostgreSQL are first pinned without one.
"""
