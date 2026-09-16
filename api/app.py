from fastapi import FastAPI

from api.routes import router

app = FastAPI(title="Dating Bot Admin API")
app.include_router(router, prefix="/api")
