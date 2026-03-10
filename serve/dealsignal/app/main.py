from __future__ import annotations

from fastapi import FastAPI

from dealsignal.app.routes import router
from dealsignal.models.database import init_db

app = FastAPI(title="DealSignal")
app.include_router(router)


@app.on_event("startup")
def startup() -> None:
    init_db()

