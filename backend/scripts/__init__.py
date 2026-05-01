"""Marker so 'from scripts import ...' resolves under the production Docker
layout (WORKDIR /app, with /app/scripts copied in). The pytest config also
adds the same dir via pythonpath, but that's a test-time crutch — without
this file, Python would not treat the directory as a package."""
