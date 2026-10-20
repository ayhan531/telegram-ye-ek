import os
import secrets
from typing import List, Optional, Dict, Any
from contextlib import asynccontextmanager

from fastapi import FastAPI, HTTPException, Request, Depends, Header
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import HTMLResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

from database import (
    create_user,
    verify_user,
    get_user_by_token,
    create_session,
    delete_session,
    update_user_api,
)
from telegram_service import manager, UserTelegramService

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
TEMPLATES_DIR = os.path.join(BASE_DIR, "templates")
STATIC_DIR = os.path.join(BASE_DIR, "static")
os.makedirs(TEMPLATES_DIR, exist_ok=True)
os.makedirs(STATIC_DIR, exist_ok=True)

@asynccontextmanager
async def lifespan(app: FastAPI):
    yield
    for svc in manager.user_services.values():
        if svc.client and svc.client.is_connected():
            try:
                await svc.client.disconnect()
            except Exception:
                pass

app = FastAPI(title="Telegram Üye Aktarıcı (Çok Kullanıcılı)", lifespan=lifespan)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.mount("/static", StaticFiles(directory=STATIC_DIR), name="static")

# Auth Modelleri
class RegisterLoginModel(BaseModel):
    username: str
    password: str

class SettingsModel(BaseModel):
    api_id: str
    api_hash: str

class PasswordModel(BaseModel):
    password: str

class ScrapeModel(BaseModel):
    source_group: str

class TransferModel(BaseModel):
    target_group: str
    user_ids: List[int]
    exclude_mode: bool = False
    all_members: Optional[List[Dict[str, Any]]] = None
    delay_seconds: float = 3.0

# Kimlik Doğrulama Dependency
async def get_current_user(authorization: Optional[str] = Header(None)) -> Dict[str, Any]:
    token = None
    if authorization:
        parts = authorization.split(" ")
        if len(parts) == 2 and parts[0].lower() == "bearer":
            token = parts[1]
        else:
            token = authorization

    if not token:
        raise HTTPException(status_code=401, detail="Oturum açmanız gerekiyor.")

    user = get_user_by_token(token)
    if not user:
        raise HTTPException(status_code=401, detail="Geçersiz oturum. Lütfen tekrar giriş yapın.")
    return user

def get_user_service(user: Dict[str, Any] = Depends(get_current_user)) -> UserTelegramService:
    return manager.get_service(
        user_id=user["id"],
        username=user["username"],
        api_id=user.get("api_id", ""),
        api_hash=user.get("api_hash", "")
    )

# --- AUTH ENDPOINTS ---

@app.get("/", response_class=HTMLResponse)
async def get_index():
    index_path = os.path.join(TEMPLATES_DIR, "index.html")
    if os.path.exists(index_path):
        with open(index_path, "r", encoding="utf-8") as f:
            return f.read()
    return "<h1>Telegram Üye Aktarıcı Yükleniyor...</h1>"

@app.post("/api/auth/register")
async def register(data: RegisterLoginModel):
    uname = data.username.strip()
    pwd = data.password.strip()
    if not uname or not pwd:
        raise HTTPException(status_code=400, detail="İsim ve şifre boş bırakılamaz.")
    
    user = create_user(uname, pwd)
    if not user:
        raise HTTPException(status_code=400, detail="Bu isimde bir hesap zaten var. Başka bir isim seçin.")

    token = secrets.token_hex(24)
    create_session(user["id"], token)
    return {
        "success": True,
        "token": token,
        "user": {"id": user["id"], "username": user["username"], "has_api": False}
    }

@app.post("/api/auth/login")
async def login(data: RegisterLoginModel):
    uname = data.username.strip()
    pwd = data.password.strip()
    user = verify_user(uname, pwd)
    if not user:
        raise HTTPException(status_code=400, detail="İsim veya şifre hatalı!")

    token = secrets.token_hex(24)
    create_session(user["id"], token)
    has_api = bool(user.get("api_id") and user.get("api_hash"))
    return {
        "success": True,
        "token": token,
        "user": {"id": user["id"], "username": user["username"], "has_api": has_api}
    }

@app.get("/api/auth/me")
async def get_me(user: Dict[str, Any] = Depends(get_current_user)):
    has_api = bool(user.get("api_id") and user.get("api_hash"))
    return {
        "id": user["id"],
        "username": user["username"],
        "has_api": has_api,
        "api_id": user.get("api_id", ""),
        "api_hash": (user.get("api_hash", "")[:4] + "****") if user.get("api_hash") else "",
    }

@app.post("/api/auth/logout")
async def logout_auth(authorization: Optional[str] = Header(None)):
    token = None
    if authorization:
        parts = authorization.split(" ")
        token = parts[1] if len(parts) == 2 else authorization
    if token:
        delete_session(token)
    return {"success": True}

# --- TELEGRAM ENDPOINTS (KULLANICI BAZLI) ---

@app.get("/api/status")
async def get_status(
    user: Dict[str, Any] = Depends(get_current_user),
    svc: UserTelegramService = Depends(get_user_service)
):
    has_config = bool(user.get("api_id") and user.get("api_hash"))
    return {
        "has_config": has_config,
        "api_id": user.get("api_id", ""),
        "api_hash": (user.get("api_hash", "")[:4] + "****") if user.get("api_hash") else "",
        "auth_state": svc.auth_state,
        "auth_message": svc.auth_message,
        "user": svc.current_user,
        "transfer_running": svc.transfer_is_running,
    }

OFFICIAL_API_ID = "2040"
OFFICIAL_API_HASH = "b18441a1ff607e10a989891a5462e627"

@app.post("/api/settings/default")
async def set_default_api(
    user: Dict[str, Any] = Depends(get_current_user),
    svc: UserTelegramService = Depends(get_user_service)
):
    update_user_api(user["id"], OFFICIAL_API_ID, OFFICIAL_API_HASH)
    svc.update_credentials(OFFICIAL_API_ID, OFFICIAL_API_HASH)
    await svc.init_client()
    return {"success": True, "message": "Resmi Telegram API bilgileri tanımlandı."}

@app.post("/api/settings")
async def save_settings(
    data: SettingsModel,
    user: Dict[str, Any] = Depends(get_current_user),
    svc: UserTelegramService = Depends(get_user_service)
):
    api_id = data.api_id.strip()
    api_hash = data.api_hash.strip()
    if not api_id or not api_hash:
        raise HTTPException(status_code=400, detail="API ID ve API HASH boş bırakılamaz.")
    
    try:
        int(api_id)
    except ValueError:
        raise HTTPException(status_code=400, detail="API ID sadece sayılardan oluşmalıdır.")

    update_user_api(user["id"], api_id, api_hash)
    svc.update_credentials(api_id, api_hash)
    await svc.init_client()
    return {"success": True, "message": "API Ayarları başarıyla kaydedildi."}

@app.post("/api/qr/start")
async def start_qr(svc: UserTelegramService = Depends(get_user_service)):
    res = await svc.start_qr_login()
    return res

@app.get("/api/qr/poll")
async def poll_qr(svc: UserTelegramService = Depends(get_user_service)):
    return {
        "auth_state": svc.auth_state,
        "auth_message": svc.auth_message,
        "qr_image": svc.qr_image_b64,
        "user": svc.current_user,
    }

@app.post("/api/qr/2fa")
async def submit_2fa(data: PasswordModel, svc: UserTelegramService = Depends(get_user_service)):
    res = await svc.submit_2fa_password(data.password)
    return res

@app.post("/api/telegram/logout")
async def telegram_logout(svc: UserTelegramService = Depends(get_user_service)):
    res = await svc.logout()
    return res

@app.get("/api/admin-groups")
async def get_admin_groups(svc: UserTelegramService = Depends(get_user_service)):
    groups = await svc.get_admin_groups()
    return {"groups": groups}

@app.post("/api/scrape")
async def scrape_members(data: ScrapeModel, svc: UserTelegramService = Depends(get_user_service)):
    res = await svc.scrape_source_members(data.source_group)
    return res

@app.post("/api/transfer/start")
async def start_transfer(data: TransferModel, svc: UserTelegramService = Depends(get_user_service)):
    res = await svc.start_transfer(
        target_group_ref=data.target_group,
        user_ids=data.user_ids,
        exclude_mode=data.exclude_mode,
        all_members=data.all_members,
        delay_seconds=data.delay_seconds,
    )
    return res

@app.post("/api/transfer/stop")
async def stop_transfer(svc: UserTelegramService = Depends(get_user_service)):
    res = svc.stop_transfer()
    return res

@app.get("/api/transfer/status")
async def get_transfer_status(svc: UserTelegramService = Depends(get_user_service)):
    return {
        "is_running": svc.transfer_is_running,
        "stats": svc.transfer_stats,
        "logs": svc.transfer_logs[-50:],
    }

if __name__ == "__main__":
    import uvicorn
    port = int(os.environ.get("PORT", 8000))
    uvicorn.run("app:app", host="0.0.0.0", port=port, reload=True)
