"""The management commands (#92): setup, config, secrets, profiles, stats, history and doctor.

Each module holds thin Typer wrappers over the services (config_store, deploy, history,
profiles, doctor, smoke). cli.py loads a module only when its command runs or --help lists it,
so improve and persona never import this package.
"""
