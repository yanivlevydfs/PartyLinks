"""Settings the admin changes, kept in the database (``config_overrides``) on top of config/settings.yaml.

The file stays the base, versioned in git; a deploy replaces it. What the admin edits is stored as a
sparse override (only the changed keys) and survives deploys and restarts. Priority: environment
variables > database overrides > settings.yaml. Both processes (web and worker) load the overrides
from the database at startup and when the admin applies a change.
"""
