# Layer boundaries

```text
domain      <- no framework or infrastructure imports
ports       <- domain contracts
application <- domain + ports
adapters    <- port implementations and external libraries
protocol    <- public DTO projection
apps        <- composition roots
web         <- generated protocol types only
```

The architecture test rejects FastAPI, SQLAlchemy, OpenAI, Uvicorn and Typer imports from `domain/` and `application/`.

