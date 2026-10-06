"""The full-screen interface (#93): `promptmend ui`, or a bare `promptmend` on a
terminal. Every screen calls the same services as the headless commands (#92) and adds no
logic of its own.

Textual is imported by the modules in this package, and only commands/ui.py imports them,
when the interface opens: the triggers and every other command never load it.
"""
