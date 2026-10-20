import os
from typing import List, Optional, Dict, Any
from contextlib import asynccontextmanager

from fastapi import FastAPI, HTTPException, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import HTMLResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

from telegram_service import telegram_service, save_config

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
TEMPLATES_DIR = os.path.join(BASE_DIR, "templates")
STATIC_DIR = os.path.join(BASE_DIR, "static")
os.makedirs(TEMPLATES_DIR, exist_ok=True)
os.makedirs(STATIC_DIR, exist_ok=True)

@asynccontextmanager
async def lifespan(app: FastAPI):
    # Uygulama açılışında kayıtlı session varsa bağlanmayı dene
    try:
        await telegram_service.init_client()
    except Exception as e:
        print(f"Başlangıç bağlantı uyarısı: {e}")
    yield
    # Uygulama kapanışında
    if telegram_service.client and telegram_service.client.is_connected():
        await telegram_service.client.disconnect()

app = FastAPI(title="Telegram Üye Aktarıcı Dashboard", lifespan=lifespan)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Statik dosyaları bağla
app.mount("/static", StaticFiles(directory=STATIC_DIR), name="static")

# Request Modelleri
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

@app.get("/", response_class=HTMLResponse)
async def get_index():
    index_path = os.path.join(TEMPLATES_DIR, "index.html")
    if os.path.exists(index_path):
        with open(index_path, "r", encoding="utf-8") as f:
            return f.read()
    return "<h1>Telegram Üye Aktarıcı Arayüzü Yükleniyor...</h1>"

@app.get("/api/status")
async def get_status():
    cfg = telegram_service.config
    has_config = bool(cfg.get("api_id") and cfg.get("api_hash"))
    return {
        "has_config": has_config,
        "api_id": cfg.get("api_id", ""),
        "api_hash": cfg.get("api_hash", "")[:4] + "****" if cfg.get("api_hash") else "",
        "auth_state": telegram_service.auth_state,
        "auth_message": telegram_service.auth_message,
        "user": telegram_service.current_user,
        "transfer_running": telegram_service.transfer_is_running,
    }

@app.post("/api/settings")
async def save_settings(data: SettingsModel):
    api_id = data.api_id.strip()
    api_hash = data.api_hash.strip()
    if not api_id or not api_hash:
        raise HTTPException(status_code=400, detail="API ID ve API HASH boş bırakılamaz.")
    
    try:
        int(api_id)
    except ValueError:
        raise HTTPException(status_code=400, detail="API ID sadece sayılardan oluşmalıdır.")

    telegram_service.config["api_id"] = api_id
    telegram_service.config["api_hash"] = api_hash
    save_config(telegram_service.config)
    
    # Yeni client başlatmayı dene
    await telegram_service.init_client()
    return {"success": True, "message": "Ayarlar başarıyla kaydedildi."}

@app.post("/api/qr/start")
async def start_qr():
    res = await telegram_service.start_qr_login()
    return res

@app.get("/api/qr/poll")
async def poll_qr():
    return {
        "auth_state": telegram_service.auth_state,
        "auth_message": telegram_service.auth_message,
        "qr_image": telegram_service.qr_image_b64,
        "user": telegram_service.current_user,
    }

@app.post("/api/qr/2fa")
async def submit_2fa(data: PasswordModel):
    res = await telegram_service.submit_2fa_password(data.password)
    return res

@app.post("/api/logout")
async def logout():
    res = await telegram_service.logout()
    return res

@app.get("/api/admin-groups")
async def get_admin_groups():
    groups = await telegram_service.get_admin_groups()
    return {"groups": groups}

@app.post("/api/scrape")
async def scrape_members(data: ScrapeModel):
    res = await telegram_service.scrape_source_members(data.source_group)
    return res

@app.post("/api/transfer/start")
async def start_transfer(data: TransferModel):
    res = await telegram_service.start_transfer(
        target_group_ref=data.target_group,
        user_ids=data.user_ids,
        exclude_mode=data.exclude_mode,
        all_members=data.all_members,
        delay_seconds=data.delay_seconds,
    )
    return res

@app.post("/api/transfer/stop")
async def stop_transfer():
    res = telegram_service.stop_transfer()
    return res

@app.get("/api/transfer/status")
async def get_transfer_status():
    return {
        "is_running": telegram_service.transfer_is_running,
        "stats": telegram_service.transfer_stats,
        "logs": telegram_service.transfer_logs[-50:], # Son 50 log
    }

if __name__ == "__main__":
    import uvicorn
    port = int(os.environ.get("PORT", 8000))
    uvicorn.run("app:app", host="0.0.0.0", port=port, reload=True)
