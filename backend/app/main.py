from fastapi import Depends, FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.auth import AuthUser, current_user
from app.config import get_settings

app = FastAPI(title="Halal Trading Lab", version="0.1.0")

app.add_middleware(
    CORSMiddleware,
    allow_origins=get_settings().cors_origin_list,
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.get("/health")
def health() -> dict:
    return {"status": "ok"}


@app.get("/me")
def me(user: AuthUser = Depends(current_user)) -> dict:
    return {"id": str(user.id), "email": user.email}
