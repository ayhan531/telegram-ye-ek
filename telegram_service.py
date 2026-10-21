import asyncio
import base64
import io
import os
import re
import datetime
from typing import List, Dict, Any, Optional, Union

import qrcode
from telethon import TelegramClient, errors, types, functions, utils
from telethon.tl.functions.channels import InviteToChannelRequest, GetParticipantRequest
from telethon.tl.functions.messages import AddChatUserRequest, CheckChatInviteRequest

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
SESSION_DIR = os.path.join(BASE_DIR, "sessions")
os.makedirs(SESSION_DIR, exist_ok=True)

def generate_qr_base64(url: str) -> str:
    """tg:// URL'sini base64 PNG resmine çevirir."""
    qr = qrcode.QRCode(
        version=1,
        error_correction=qrcode.constants.ERROR_CORRECT_M,
        box_size=8,
        border=2,
    )
    qr.add_data(url)
    qr.make(fit=True)
    img = qr.make_image(fill_color="#1e293b", back_color="#ffffff")
    buf = io.BytesIO()
    img.save(buf, format="PNG")
    b64 = base64.b64encode(buf.getvalue()).decode("utf-8")
    return f"data:image/png;base64,{b64}"

def get_status_str(status) -> str:
    if not status:
        return "Bilinmiyor"
    if isinstance(status, types.UserStatusOnline):
        return "Çevrimiçi"
    elif isinstance(status, types.UserStatusRecently):
        return "Yakın zamanda görüldü"
    elif isinstance(status, types.UserStatusLastWeek):
        return "Son 1 hafta içinde görüldü"
    elif isinstance(status, types.UserStatusLastMonth):
        return "Son 1 ay içinde görüldü"
    elif isinstance(status, types.UserStatusOffline):
        if status.was_online:
            dt = status.was_online.strftime("%d.%m.%Y %H:%M")
            return f"Çevrimdışı ({dt})"
        return "Çevrimdışı"
    return "Bilinmiyor"

async def resolve_entity(client: TelegramClient, ref: Union[str, int]) -> Any:
    """
    Grup veya kanal referansını (ID, @username, link, vb.) hatasız olarak çözer.
    Özellikle kanal ID'lerinin (-100...) bulunamama sorununu kesin çözer.
    """
    if isinstance(ref, (types.Channel, types.Chat, types.User, types.InputPeerChannel, types.InputPeerChat)):
        return ref

    s_ref = str(ref).strip()
    if not s_ref:
        raise ValueError("Grup referansı boş olamaz.")

    # 1. t.me linklerini ayıkla
    # Örn: https://t.me/joinchat/XXXX veya https://t.me/+XXXX
    m_invite = re.search(r"(?:t\.me|telegram\.me)/(?:\+|joinchat/)([\w-]+)", s_ref)
    if m_invite:
        hash_str = m_invite.group(1)
        try:
            invite_check = await client(CheckChatInviteRequest(hash_str))
            if hasattr(invite_check, 'chat'):
                return invite_check.chat
        except Exception:
            pass

    # Örn: https://t.me/grupadi
    m_user = re.search(r"(?:t\.me|telegram\.me)/([A-Za-z]\w{3,})", s_ref)
    if m_user:
        s_ref = m_user.group(1)

    # 2. Sayısal ID denemeleri
    # Eğer saf sayı veya başında eksi olan sayı ise
    clean_num = s_ref.lstrip("-")
    if clean_num.isdigit():
        num_val = int(clean_num)
        
        # Olası ID varyasyonları
        candidates = []
        if not s_ref.startswith("-"):
            # Pozitif geldiyse (örn 2165264333)
            candidates.append(int(f"-100{num_val}")) # En yaygın süpergrup formatı
            candidates.append(-num_val)
            candidates.append(num_val)
        else:
            candidates.append(int(s_ref))
            if not s_ref.startswith("-100"):
                candidates.append(int(f"-100{num_val}"))

        for cid in candidates:
            try:
                ent = await client.get_entity(cid)
                if ent:
                    return ent
            except Exception:
                pass

    # 3. Doğrudan Telethon get_entity dene (@username veya standart)
    try:
        ent = await client.get_entity(s_ref)
        if ent:
            return ent
    except Exception:
        pass

    # 4. KESİN ÇÖZÜM: Dialogları (Sohbetleri) tara ve eşleştir
    # Session hafızasındaki tüm sohbetleri yükleyip arar
    try:
        async for dialog in client.iter_dialogs(limit=200):
            d_ent = dialog.entity
            d_id = getattr(d_ent, 'id', None)
            
            # ID kontrolü
            if d_id and (str(d_id) == clean_num or str(dialog.id) == s_ref or str(dialog.id) == f"-100{clean_num}"):
                return d_ent
            
            # Başlık / İsim kontrolü
            if dialog.name and dialog.name.strip().lower() == s_ref.lower():
                return d_ent
            
            # Username kontrolü
            d_uname = getattr(d_ent, 'username', '') or ''
            if d_uname and d_uname.lower() == s_ref.lstrip('@').lower():
                return d_ent
    except Exception as ex:
        print(f"Dialog arama uyarısı: {ex}")

    raise ValueError(f"Hedef grup bulunamadı: '{s_ref}'. Lütfen grubun @kullanıcıadını veya tam davet linkini girin.")

class UserTelegramService:
    def __init__(self, user_id: int, username: str, api_id: str = "", api_hash: str = ""):
        self.user_id = user_id
        self.username = username
        self.api_id = api_id
        self.api_hash = api_hash
        
        self.session_file = os.path.join(SESSION_DIR, f"user_{self.user_id}")
        self.client: Optional[TelegramClient] = None
        self.qr_login_obj = None
        self.qr_task: Optional[asyncio.Task] = None
        
        self.auth_state = "NOT_CONNECTED"
        self.auth_message = "Giriş bekleniyor"
        self.qr_image_b64 = None
        self.current_user: Optional[Dict[str, Any]] = None
        
        # Transfer Durumu
        self.transfer_task: Optional[asyncio.Task] = None
        self.transfer_is_running = False
        self.transfer_stop_requested = False
        self.transfer_stats = {
            "total": 0,
            "processed": 0,
            "success": 0,
            "failed": 0,
            "skipped": 0,
            "status": "idle",
            "message": "",
        }
        self.transfer_logs: List[Dict[str, Any]] = []

    def update_credentials(self, api_id: str, api_hash: str):
        self.api_id = api_id
        self.api_hash = api_hash

    def log_transfer(self, msg: str, level: str = "info"):
        now = datetime.datetime.now().strftime("%H:%M:%S")
        entry = {"time": now, "message": msg, "level": level}
        self.transfer_logs.append(entry)
        if len(self.transfer_logs) > 500:
            self.transfer_logs.pop(0)

    async def init_client(self):
        if not self.api_id or not self.api_hash:
            self.auth_state = "CONFIG_REQUIRED"
            self.auth_message = "API ID ve API HASH girilmesi gerekiyor."
            return

        if self.client and self.client.is_connected():
            try:
                if await self.client.is_user_authorized():
                    me = await self.client.get_me()
                    self._set_authorized_user(me)
                    return
            except Exception:
                pass

        try:
            self.client = TelegramClient(
                self.session_file,
                int(self.api_id),
                self.api_hash,
                device_model="Telegram Uye Aktarici",
                app_version="2.0",
                system_version=f"User-{self.user_id}"
            )
            await self.client.connect()

            if await self.client.is_user_authorized():
                me = await self.client.get_me()
                self._set_authorized_user(me)
            else:
                self.auth_state = "NOT_CONNECTED"
                self.auth_message = "QR kod ile giriş yapabilirsiniz."
        except Exception as e:
            self.auth_state = "ERROR"
            self.auth_message = f"Bağlantı hatası: {str(e)}"

    def _set_authorized_user(self, me):
        self.auth_state = "AUTHORIZED"
        self.auth_message = f"Giriş yapıldı: {me.first_name}"
        self.current_user = {
            "id": me.id,
            "first_name": me.first_name,
            "last_name": me.last_name or "",
            "username": me.username or "",
            "phone": me.phone or "",
        }

    async def start_qr_login(self) -> Dict[str, Any]:
        if not self.api_id or not self.api_hash:
            return {"success": False, "error": "Lütfen önce API ID ve API HASH ayarlarınızı kaydedin veya Resmi API kullanın."}

        if self.qr_task and not self.qr_task.done():
            self.qr_task.cancel()

        if self.client:
            try:
                await self.client.disconnect()
            except Exception:
                pass

        try:
            self.client = TelegramClient(
                self.session_file,
                int(self.api_id),
                self.api_hash,
                device_model="Telegram Uye Aktarici",
                app_version="2.0",
                system_version=f"User-{self.user_id}"
            )
            await self.client.connect()

            if await self.client.is_user_authorized():
                me = await self.client.get_me()
                self._set_authorized_user(me)
                return {"success": True, "status": "AUTHORIZED", "user": self.current_user}

            self.qr_login_obj = await self.client.qr_login()
            self.qr_image_b64 = generate_qr_base64(self.qr_login_obj.url)
            self.auth_state = "QR_READY"
            self.auth_message = "QR kodu telefonunuzdan tarayın: Ayarlar > Cihazlar > QR Kod Tara"
            
            self.qr_task = asyncio.create_task(self._qr_wait_loop())
            return {
                "success": True,
                "status": "QR_READY",
                "qr_image": self.qr_image_b64,
                "expires": self.qr_login_obj.expires.isoformat() if self.qr_login_obj.expires else None
            }
        except errors.ApiIdInvalidError:
            self.auth_state = "ERROR"
            msg = "Telegram API ID veya API Hash geçersiz! Lütfen bilgilerinizi kontrol edin veya 'Resmi API'yi Seçin'."
            self.auth_message = msg
            return {"success": False, "error": msg}
        except Exception as e:
            self.auth_state = "ERROR"
            err_str = str(e)
            if "combination is invalid" in err_str.lower() or "api_id_invalid" in err_str.lower():
                msg = "Telegram API ID veya API Hash geçersiz! my.telegram.org bilgilerinizi kontrol edin veya Resmi API'yi seçin."
            else:
                msg = f"Giriş hatası: {err_str}"
            self.auth_message = msg
            return {"success": False, "error": msg}

    async def _qr_wait_loop(self):
        while self.auth_state in ("QR_READY", "WAITING_PASSWORD"):
            try:
                user = await self.qr_login_obj.wait(timeout=25)
                if user:
                    self._set_authorized_user(user)
                    self.qr_image_b64 = None
                    break
            except asyncio.TimeoutError:
                try:
                    await self.qr_login_obj.recreate()
                    self.qr_image_b64 = generate_qr_base64(self.qr_login_obj.url)
                except Exception as e:
                    self.auth_state = "ERROR"
                    self.auth_message = f"QR yenileme hatası: {str(e)}"
                    break
            except errors.SessionPasswordNeededError:
                self.auth_state = "WAITING_PASSWORD"
                self.auth_message = "Hesabınızda İki Adımlı Doğrulama (2FA) aktif. Lütfen şifrenizi girin."
                break
            except asyncio.CancelledError:
                break
            except Exception as e:
                self.auth_state = "ERROR"
                self.auth_message = f"Giriş hatası: {str(e)}"
                break

    async def submit_2fa_password(self, password: str) -> Dict[str, Any]:
        if not self.client:
            return {"success": False, "error": "İstemci aktif değil."}
        try:
            me = await self.client.sign_in(password=password)
            self._set_authorized_user(me)
            self.qr_image_b64 = None
            return {"success": True, "user": self.current_user}
        except errors.PasswordHashInvalidError:
            return {"success": False, "error": "2FA şifresi yanlış! Lütfen tekrar deneyin."}
        except Exception as e:
            return {"success": False, "error": f"2FA doğrulaması başarısız: {str(e)}"}

    async def logout(self) -> Dict[str, Any]:
        try:
            if self.transfer_is_running:
                self.transfer_stop_requested = True
            if self.qr_task and not self.qr_task.done():
                self.qr_task.cancel()
            if self.client:
                if self.client.is_connected() and await self.client.is_user_authorized():
                    await self.client.log_out()
                await self.client.disconnect()
            
            full_sess_file = self.session_file + ".session"
            if os.path.exists(full_sess_file):
                os.remove(full_sess_file)
            
            self.client = None
            self.current_user = None
            self.auth_state = "NOT_CONNECTED"
            self.auth_message = "Çıkış yapıldı."
            self.qr_image_b64 = None
            return {"success": True}
        except Exception as e:
            return {"success": False, "error": str(e)}

    async def get_admin_groups(self) -> List[Dict[str, Any]]:
        """Admin olunan grupları hatasız çözümlenebilir ID (-100...) ile döndürür."""
        if not self.client or not await self.client.is_user_authorized():
            return []
        
        groups = []
        try:
            async for dialog in self.client.iter_dialogs():
                entity = dialog.entity
                if isinstance(entity, (types.Channel, types.Chat)):
                    if getattr(entity, 'broadcast', False):
                        continue
                    
                    is_admin = False
                    try:
                        if isinstance(entity, types.Channel):
                            participant = await self.client(GetParticipantRequest(channel=entity, participant="me"))
                            p = participant.participant
                            if isinstance(p, (types.ChannelParticipantCreator, types.ChannelParticipantAdmin)):
                                is_admin = True
                        elif isinstance(entity, types.Chat):
                            if getattr(entity, 'creator', False) or getattr(entity, 'admin_rights', False):
                                is_admin = True
                    except Exception:
                        if getattr(entity, 'creator', False) or getattr(entity, 'admin_rights', False):
                            is_admin = True

                    if is_admin:
                        username = getattr(entity, 'username', '')
                        # Telethon süpergrup ID'si için kesin -100 formatı
                        raw_id = entity.id
                        full_peer_id = f"-100{raw_id}" if isinstance(entity, types.Channel) else f"-{raw_id}"
                        
                        groups.append({
                            "id": full_peer_id,
                            "raw_id": raw_id,
                            "title": dialog.title,
                            "username": f"@{username}" if username else "",
                            "members_count": getattr(entity, 'participants_count', 0) or 0
                        })
        except Exception as ex:
            print(f"Grup listesi hatası: {ex}")
        return groups

    async def scrape_source_members(self, group_ref: str) -> Dict[str, Any]:
        """
        Kaynak gruptan üyeleri çeker.
        EĞER grup üyeleri gizliyse (Hide Members), otomatik olarak 'Mesaj ve Etkileşim Geçmişinden'
        üyeleri toplayarak gizlilik kısıtlamasını tamamen aşar!
        """
        if not self.client or not await self.client.is_user_authorized():
            return {"success": False, "error": "Telegram girişi yapılmamış."}

        group_ref = group_ref.strip()
        if not group_ref:
            return {"success": False, "error": "Kaynak grup linki veya kullanıcı adı boş olamaz."}

        try:
            entity = await resolve_entity(self.client, group_ref)
        except Exception as e:
            return {"success": False, "error": f"Kaynak gruba ulaşılamadı: {str(e)}"}

        members_dict: Dict[int, Dict[str, Any]] = {}
        scrape_method = "standart"
        is_hidden_group = False

        # 1. ADIM: Standart iter_participants ile deneme
        try:
            async for user in self.client.iter_participants(entity, aggressive=True):
                if user.deleted:
                    continue
                status_text = get_status_str(user.status)
                members_dict[user.id] = {
                    "id": user.id,
                    "first_name": user.first_name or "",
                    "last_name": user.last_name or "",
                    "name": f"{user.first_name or ''} {user.last_name or ''}".strip() or "İsimsiz",
                    "username": f"@{user.username}" if user.username else "",
                    "phone": user.phone or "",
                    "is_bot": bool(user.bot),
                    "status": status_text,
                    "is_active": isinstance(user.status, (types.UserStatusOnline, types.UserStatusRecently)),
                }
        except errors.ChatAdminRequiredError:
            is_hidden_group = True
        except Exception as ex:
            print(f"Standart scraping uyarısı: {ex}")
            is_hidden_group = True

        # Eğer grup üyeleri gizliyse veya sadece adminler geldiyse (< 5 üye)
        if is_hidden_group or len(members_dict) <= 3:
            scrape_method = "gizli_mesaj_taramasi"
            
            # 2. ADIM: AKILLI GİZLİ ÜYE KAZIMA (MESAJ GEÇMİŞİNDEN AKTİF ÜYELERİ ÇEKME)
            try:
                # Gruptaki son 3000 mesajı tarayıp mesaj atan, yanıtlayan ve katılan üyeleri çeker
                async for message in self.client.iter_messages(entity, limit=3000):
                    # Mesajı gönderen kullanıcı
                    sender = message.sender
                    if isinstance(sender, types.User) and not sender.deleted:
                        if sender.id not in members_dict:
                            status_text = get_status_str(sender.status)
                            members_dict[sender.id] = {
                                "id": sender.id,
                                "first_name": sender.first_name or "",
                                "last_name": sender.last_name or "",
                                "name": f"{sender.first_name or ''} {sender.last_name or ''}".strip() or "İsimsiz",
                                "username": f"@{sender.username}" if sender.username else "",
                                "phone": sender.phone or "",
                                "is_bot": bool(sender.bot),
                                "status": status_text,
                                "is_active": isinstance(sender.status, (types.UserStatusOnline, types.UserStatusRecently)),
                            }

                    # Servis mesajları (gruba katılanlar / eklenenler)
                    if hasattr(message, 'action') and message.action:
                        action_users = getattr(message.action, 'users', None)
                        if action_users:
                            for uid in action_users:
                                if uid not in members_dict:
                                    try:
                                        u_ent = await self.client.get_entity(uid)
                                        if isinstance(u_ent, types.User) and not u_ent.deleted:
                                            members_dict[u_ent.id] = {
                                                "id": u_ent.id,
                                                "first_name": u_ent.first_name or "",
                                                "last_name": u_ent.last_name or "",
                                                "name": f"{u_ent.first_name or ''} {u_ent.last_name or ''}".strip() or "İsimsiz",
                                                "username": f"@{u_ent.username}" if u_ent.username else "",
                                                "phone": u_ent.phone or "",
                                                "is_bot": bool(u_ent.bot),
                                                "status": get_status_str(u_ent.status),
                                                "is_active": isinstance(u_ent.status, (types.UserStatusOnline, types.UserStatusRecently)),
                                            }
                                    except Exception:
                                        pass

            except Exception as ex:
                print(f"Mesaj tarama hatası: {ex}")

            # 3. ADIM: HARF BAZLI EK ARAMA (Telegram Search Fallback)
            try:
                # Harf arayarak parça parça üye listesi çekme
                for letter in ["a", "e", "i", "o", "u", "s", "m", "t", "r"]:
                    if len(members_dict) >= 500:
                        break
                    try:
                        async for user in self.client.iter_participants(entity, search=letter, limit=50):
                            if user.deleted or user.id in members_dict:
                                continue
                            members_dict[user.id] = {
                                "id": user.id,
                                "first_name": user.first_name or "",
                                "last_name": user.last_name or "",
                                "name": f"{user.first_name or ''} {user.last_name or ''}".strip() or "İsimsiz",
                                "username": f"@{user.username}" if user.username else "",
                                "phone": user.phone or "",
                                "is_bot": bool(user.bot),
                                "status": get_status_str(user.status),
                                "is_active": isinstance(user.status, (types.UserStatusOnline, types.UserStatusRecently)),
                            }
                    except Exception:
                        pass
            except Exception:
                pass

        members = list(members_dict.values())
        group_title = getattr(entity, 'title', str(group_ref))

        method_note = ""
        if scrape_method == "gizli_mesaj_taramasi":
            method_note = f" (🔒 Grup Üyeleri Gizliydi: Mesaj geçmişinden {len(members)} aktif üye başarıyla kurtarıldı!)"

        return {
            "success": True,
            "group_title": group_title + method_note,
            "total_count": len(members),
            "members": members,
            "is_hidden": is_hidden_group
        }

    async def start_transfer(
        self,
        target_group_ref: str,
        user_ids: List[int],
        exclude_mode: bool = False,
        all_members: Optional[List[Dict[str, Any]]] = None,
        delay_seconds: float = 3.0,
    ) -> Dict[str, Any]:
        if not self.client or not await self.client.is_user_authorized():
            return {"success": False, "error": "Telegram girişi yapılmamış."}

        if self.transfer_is_running:
            return {"success": False, "error": "Zaten devam eden bir aktarma işleminiz var."}

        target_group_ref = target_group_ref.strip()
        if not target_group_ref:
            return {"success": False, "error": "Hedef grup belirtilmedi."}

        # resolve_entity ile Hedef Grubu Kesin Olarak Çöz!
        try:
            target_entity = await resolve_entity(self.client, target_group_ref)
        except Exception as e:
            return {"success": False, "error": f"Hedef grup bulunamadı: {str(e)}"}

        target_users_to_add: List[int] = []
        selected_set = set(user_ids)

        if exclude_mode:
            if not all_members:
                return {"success": False, "error": "Hariç tutma modu için tüm üye listesi gereklidir."}
            for m in all_members:
                uid = m.get("id")
                if uid and uid not in selected_set and not m.get("is_bot"):
                    target_users_to_add.append(uid)
        else:
            target_users_to_add = [uid for uid in user_ids]

        if not target_users_to_add:
            return {"success": False, "error": "Eklenecek üye bulunamadı (liste boş)."}

        self.transfer_is_running = True
        self.transfer_stop_requested = False
        self.transfer_stats = {
            "total": len(target_users_to_add),
            "processed": 0,
            "success": 0,
            "failed": 0,
            "skipped": 0,
            "status": "running",
            "message": f"{len(target_users_to_add)} üye aktarımı başlatıldı.",
        }
        self.transfer_logs.clear()
        
        target_name = getattr(target_entity, 'title', target_group_ref)
        self.log_transfer(f"Hedef Grup: '{target_name}'. Toplam: {len(target_users_to_add)} kişi. Bekleme: {delay_seconds}sn", "info")

        self.transfer_task = asyncio.create_task(
            self._transfer_worker(target_entity, target_users_to_add, delay_seconds)
        )

        return {"success": True, "message": "Aktarım işlemi başlatıldı."}

    async def _transfer_worker(self, target_entity, user_ids: List[int], delay: float):
        try:
            is_supergroup = getattr(target_entity, 'megagroup', False) or getattr(target_entity, 'broadcast', False) or isinstance(target_entity, types.Channel)

            for uid in user_ids:
                if self.transfer_stop_requested:
                    self.log_transfer("İşlem kullanıcı tarafından durduruldu.", "warning")
                    self.transfer_stats["status"] = "stopped"
                    self.transfer_stats["message"] = "İşlem durduruldu."
                    break

                self.transfer_stats["processed"] += 1
                try:
                    user_entity = await self.client.get_entity(uid)
                    user_name = f"{user_entity.first_name or ''} (@{user_entity.username or user_entity.id})".strip()
                    
                    if is_supergroup:
                        await self.client(InviteToChannelRequest(channel=target_entity, users=[user_entity]))
                    else:
                        await self.client(AddChatUserRequest(chat_id=target_entity.id, user_id=user_entity, fwd_limit=0))

                    self.transfer_stats["success"] += 1
                    self.log_transfer(f"✔ Başarıyla eklendi: {user_name}", "success")

                except errors.UserPrivacyRestrictedError:
                    self.transfer_stats["failed"] += 1
                    self.transfer_stats["skipped"] += 1
                    self.log_transfer(f"⚠ Gizlilik kısıtlaması (kullanıcı yabancıların eklemesine izin vermiyor): ID {uid}", "warning")

                except errors.UserAlreadyParticipantError:
                    self.transfer_stats["skipped"] += 1
                    self.log_transfer(f"ℹ Kullanıcı zaten grupta: ID {uid}", "info")

                except errors.UserChannelsTooMuchError:
                    self.transfer_stats["failed"] += 1
                    self.log_transfer(f"⚠ Kullanıcı çok fazla grupta/kanalda yer alıyor (limit dolu): ID {uid}", "warning")

                except errors.UserNotMutualContactError:
                    self.transfer_stats["failed"] += 1
                    self.log_transfer(f"⚠ Kullanıcı sadece rehberindeki kişilerin eklemesine izin veriyor: ID {uid}", "warning")

                except errors.PeerFloodError:
                    self.transfer_stats["failed"] += 1
                    self.log_transfer("⛔ Telegram geçici spam kısıtı koydu (PeerFloodError)! Hesabın güvenliği için aktarım durduruluyor.", "error")
                    self.transfer_stats["status"] = "stopped"
                    self.transfer_stats["message"] = "Telegram spam engeli koydu. Lütfen birkaç saat bekleyin."
                    break

                except errors.FloodWaitError as e:
                    self.log_transfer(f"⏳ Telegram FloodWait uyarısı: {e.seconds} saniye zorunlu bekleme istendi.", "warning")
                    if e.seconds <= 120:
                        for s in range(e.seconds, 0, -10):
                            if self.transfer_stop_requested:
                                break
                            self.log_transfer(f"⏳ Kalan bekleme: {s} saniye...", "info")
                            await asyncio.sleep(min(10, s))
                    else:
                        self.log_transfer(f"⛔ Flood bekleme süresi çok uzun ({e.seconds} sn). İşlem durduruluyor.", "error")
                        self.transfer_stats["status"] = "stopped"
                        break

                except errors.ChatAdminRequiredError:
                    self.transfer_stats["failed"] += 1
                    self.log_transfer("⛔ Hedef grupta kullanıcı ekleme yetkiniz yok veya admin değilsiniz!", "error")
                    self.transfer_stats["status"] = "stopped"
                    break

                except Exception as ex:
                    self.transfer_stats["failed"] += 1
                    self.log_transfer(f"❌ Hata (ID {uid}): {str(ex)}", "error")

                await asyncio.sleep(delay)

            if self.transfer_stats["status"] == "running":
                self.transfer_stats["status"] = "completed"
                self.transfer_stats["message"] = "Aktarım tamamlandı!"
                self.log_transfer("🎉 Aktarım işlemi başarıyla tamamlandı!", "success")

        except Exception as e:
            self.transfer_stats["status"] = "error"
            self.transfer_stats["message"] = f"Beklenmeyen hata: {str(e)}"
            self.log_transfer(f"Kritik Hata: {str(e)}", "error")
        finally:
            self.transfer_is_running = False

    def stop_transfer(self) -> Dict[str, Any]:
        if not self.transfer_is_running:
            return {"success": False, "error": "Zaten çalışan bir aktarma işlemi yok."}
        self.transfer_stop_requested = True
        self.log_transfer("Durdurma talebi alındı, mevcut istek bittikten sonra duracak...", "warning")
        return {"success": True, "message": "Aktarım durduruluyor..."}

class TelegramManager:
    def __init__(self):
        self.user_services: Dict[int, UserTelegramService] = {}

    def get_service(self, user_id: int, username: str, api_id: str = "", api_hash: str = "") -> UserTelegramService:
        if user_id not in self.user_services:
            svc = UserTelegramService(user_id, username, api_id, api_hash)
            self.user_services[user_id] = svc
        else:
            svc = self.user_services[user_id]
            svc.update_credentials(api_id, api_hash)
        return svc

manager = TelegramManager()
