"""Deployment management: registered targets (M5.1) and the Jenkins client.

Nothing here accepts a Jenkins URL, a job name or a build parameter from a request
(PRD §4.10, CLAUDE.md §4 rules 1 and 9). A target is written by an administrator, validated
against closed grammars, and is the only thing a later deployment may name.
"""
