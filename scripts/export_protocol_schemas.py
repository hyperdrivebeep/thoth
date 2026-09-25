from __future__ import annotations

from pathlib import Path

from thoth.protocol.schema_export import export_schemas

if __name__ == "__main__":
    export_schemas(Path("schemas/protocol"))
