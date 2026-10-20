#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Telegram Hesap Aktarıcı
-----------------------
Kendi Telegram hesaplarını (numaralarını) bir gruptan alıp başka bir gruba
katılmasını sağlar. Her hesap hedef gruba KENDİSİ katılır, böylece kimseyi
zorla eklemek gerekmez ve Telegram'ın "ekleme" kısıtlamalarına takılmazsın.

Kullanım: baslat.bat dosyasına çift tıkla.
"""
import asyncio
import json
import os
import queue
import random
import re
import threading
import tkinter as tk
from tkinter import ttk, messagebox, simpledialog

try:
    from telethon import TelegramClient, errors
    from telethon.tl.functions.channels import JoinChannelRequest
    from telethon.tl.functions.messages import (
        ImportChatInviteRequest,
        CheckChatInviteRequest,
        ExportChatInviteRequest,
    )
    from telethon.tl.types import ChatInviteAlready
except ImportError:
    _r = tk.Tk()
    _r.withdraw()
    messagebox.showerror(
        "Eksik paket",
        "Telethon kurulu değil.\n\nKomut satırında şunu çalıştır:\n  pip install telethon\n\n"
        "ya da baslat.bat ile aç.",
    )
    raise SystemExit(1)

BASE = os.path.dirname(os.path.abspath(__file__))
SESS = os.path.join(BASE, "sessions")
CFG = os.path.join(BASE, "ayarlar.json")
os.makedirs(SESS, exist_ok=True)


class Hata(Exception):
    """Kullanıcıya gösterilecek anlaşılır hata."""


def isim(me):
    ad = " ".join(x for x in [me.first_name, me.last_name] if x)
    if me.username:
        ad += f" (@{me.username})"
    return ad or str(me.id)


def parse_ref(text):
    """Grup/kanal girdisini çözer: davet linki, @kullanıcıadı, t.me linki ya da ID."""
    t = (text or "").strip()
    if not t:
        return None
    m = re.search(r"\[(-?\d+|@\w+)\]\s*$", t)   # listeden seçilen "Grup Adı  [id]"
    if m:
        return parse_ref(m.group(1))
    m = re.search(r"(?:t\.me|telegram\.me|telegram\.dog)/(?:\+|joinchat/)([\w-]+)", t)
    if m:
        return {"hash": m.group(1)}
    if t.startswith("+") and not t[1:].isdigit():
        return {"hash": t[1:]}
    m = re.search(r"(?:t\.me|telegram\.me|telegram\.dog)/([A-Za-z]\w{3,})", t)
    if m:
        return {"username": m.group(1).lower()}
    if re.fullmatch(r"-?\d+", t):
        return {"id": int(t)}
    if re.fullmatch(r"@?[A-Za-z]\w{3,}", t):
        return {"username": t.lstrip("@").lower()}
    return None


# --------------------------------------------------------------------------
#  Telegram tarafı (ayrı bir thread'de asyncio döngüsünde çalışır)
# --------------------------------------------------------------------------
class Worker:
    def __init__(self, log):
        self.log = log
        self.loop = asyncio.new_event_loop()
        threading.Thread(target=self.loop.run_forever, daemon=True).start()
        self.api_id = None
        self.api_hash = None
        self.clients = {}   # anahtar (sadece rakamlar) -> TelegramClient
        self.me = {}        # anahtar -> User (giriş yapılmış hesaplar)
        self.durdur = False

    async def client_for(self, key):
        if not self.api_id or not self.api_hash:
            raise Hata("Önce API ID ve API HASH gir ve Kaydet'e bas.")
        c = self.clients.get(key)
        if c is None:
            c = TelegramClient(
                os.path.join(SESS, key), self.api_id, self.api_hash,
                device_model="Hesap Aktarici", app_version="1.0",
            )
            self.clients[key] = c
        if not c.is_connected():
            await c.connect()
        return c

    # ---- hesaplar ----
    async def load_accounts(self):
        out = []
        for f in sorted(os.listdir(SESS)):
            if not f.endswith(".session"):
                continue
            key = f[:-8]
            try:
                c = await self.client_for(key)
                if await c.is_user_authorized():
                    me = await c.get_me()
                    self.me[key] = me
                    out.append((key, isim(me), me.id))
                else:
                    self.me.pop(key, None)
                    out.append((key, "(oturum kapalı - hesabı tekrar ekle)", ""))
            except Hata:
                raise
            except Exception as ex:
                out.append((key, f"HATA: {ex}", ""))
        return out

    async def start_login(self, key):
        c = await self.client_for(key)
        if await c.is_user_authorized():
            return "already"
        try:
            await c.send_code_request("+" + key)
        except errors.PhoneNumberInvalidError:
            raise Hata("Telefon numarası geçersiz.")
        except errors.FloodWaitError as e:
            raise Hata(f"Telegram bu numara için {e.seconds} sn beklemeni istiyor.")
        return "code"

    async def finish_login(self, key, code):
        c = await self.client_for(key)
        try:
            me = await c.sign_in("+" + key, code)
        except errors.SessionPasswordNeededError:
            return "password"
        except (errors.PhoneCodeInvalidError, errors.PhoneCodeExpiredError):
            raise Hata("Kod yanlış ya da süresi dolmuş. Hesabı tekrar eklemeyi dene.")
        self.me[key] = me
        return isim(me)

    async def finish_password(self, key, pw):
        c = await self.client_for(key)
        try:
            me = await c.sign_in(password=pw)
        except errors.PasswordHashInvalidError:
            raise Hata("Şifre yanlış.")
        self.me[key] = me
        return isim(me)

    async def remove(self, key):
        c = self.clients.pop(key, None)
        self.me.pop(key, None)
        if c:
            try:
                await c.log_out()
            except Exception:
                await c.disconnect()
        p = os.path.join(SESS, key + ".session")
        if os.path.exists(p):
            os.remove(p)

    # ---- gruplar ----
    async def find_chat(self, client, ref):
        """Hesap bu grubun üyesiyse grubu döndürür, değilse None."""
        if "hash" in ref:
            try:
                inv = await client(CheckChatInviteRequest(ref["hash"]))
            except (errors.InviteHashExpiredError, errors.InviteHashInvalidError):
                raise Hata("Davet linki geçersiz ya da süresi dolmuş.")
            return inv.chat if isinstance(inv, ChatInviteAlready) else None
        async for d in client.iter_dialogs():
            if not (d.is_group or d.is_channel):
                continue
            e = d.entity
            if "id" in ref and (d.id == ref["id"] or e.id == abs(ref["id"])):
                return e
            if "username" in ref and (getattr(e, "username", None) or "").lower() == ref["username"]:
                return e
        return None

    async def scan_groups(self):
        groups = {}
        for key in list(self.me):
            c = await self.client_for(key)
            n = 0
            async for d in c.iter_dialogs():
                if not (d.is_group or d.is_channel):
                    continue
                n += 1
                g = groups.setdefault(d.id, {
                    "title": d.name,
                    "username": getattr(d.entity, "username", None) or "",
                    "tur": "Grup" if d.is_group else "Kanal",
                    "hesaplar": [],
                })
                g["hesaplar"].append(key)
            self.log(f"+{key}: {n} grup/kanal okundu")
        return groups

    async def scan_source(self, ref):
        out = []
        for key, me in list(self.me.items()):
            c = await self.client_for(key)
            ent = await self.find_chat(c, ref)
            title = getattr(ent, "title", None) if ent else None
            out.append((key, isim(me), title))
        return out

    async def resolve_target(self, ref):
        """Hedefi katılınabilir bir şeye çevirir: ('hash', ...) ya da ('username', ...)."""
        if "hash" in ref:
            return ("hash", ref["hash"])
        if "username" in ref:
            return ("username", ref["username"])
        # Sadece ID verildi: hesaplarından biri o grupta ve yetkiliyse davet linki üret
        for key in list(self.me):
            c = await self.client_for(key)
            ent = await self.find_chat(c, ref)
            if not ent:
                continue
            if getattr(ent, "username", None):
                return ("username", ent.username)
            try:
                inv = await c(ExportChatInviteRequest(peer=ent, title="Hesap aktarma"))
                self.log(f"Hedef için +{key} hesabıyla davet linki oluşturuldu: {inv.link}")
                h = parse_ref(inv.link)
                if h and "hash" in h:
                    return ("hash", h["hash"])
            except (errors.ChatAdminRequiredError, errors.RPCError):
                continue
        raise Hata(
            "Hedef grup sadece ID ile bulunamadı / katılınamadı.\n\n"
            "Hedef olarak grubun davet linkini (t.me/+...) ya da @kullanıcıadını gir, "
            "veya hesaplarından birini o grupta 'kullanıcı davet etme' yetkili admin yap."
        )

    async def join_one(self, key, target):
        c = await self.client_for(key)
        kind, val = target
        for deneme in range(2):
            try:
                if kind == "hash":
                    await c(ImportChatInviteRequest(val))
                else:
                    ent = await c.get_entity(val)
                    await c(JoinChannelRequest(ent))
                return "katıldı ✓"
            except errors.UserAlreadyParticipantError:
                return "zaten üye"
            except errors.InviteRequestSentError:
                return "katılma isteği gönderildi (admin onayı bekliyor)"
            except errors.FloodWaitError as e:
                if e.seconds <= 300 and deneme == 0:
                    self.log(f"+{key}: Telegram {e.seconds} sn beklememi istiyor, bekliyorum...")
                    await asyncio.sleep(e.seconds + 2)
                    continue
                return f"HATA: Telegram {e.seconds} sn bekleme cezası verdi, bu hesabı sonra dene"
            except errors.ChannelsTooMuchError:
                return "HATA: hesap grup sınırında (500), önce birkaç gruptan çık"
            except (errors.InviteHashExpiredError, errors.InviteHashInvalidError):
                return "HATA: davet linki geçersiz / süresi dolmuş"
            except (errors.UsernameNotOccupiedError, errors.UsernameInvalidError, ValueError):
                return "HATA: hedef grup bulunamadı (kullanıcı adını kontrol et)"
            except errors.RPCError as e:
                return f"HATA: {e.__class__.__name__}: {e}"
        return "HATA: bekleme sonrası da olmadı"

    async def transfer(self, keys, target_ref, source_ref, leave, dmin, dmax):
        self.durdur = False
        target = await self.resolve_target(target_ref)
        ok = 0
        for i, key in enumerate(keys, 1):
            if self.durdur:
                self.log("Durduruldu.")
                break
            sonuc = await self.join_one(key, target)
            self.log(f"[{i}/{len(keys)}] +{key}: {sonuc}")
            basarili = sonuc.startswith("katıldı") or sonuc == "zaten üye"
            if basarili:
                ok += 1
                if leave and source_ref:
                    try:
                        c = await self.client_for(key)
                        src = await self.find_chat(c, source_ref)
                        if src:
                            await c.delete_dialog(src)
                            self.log(f"        +{key}: kaynak gruptan çıktı")
                    except Exception as ex:
                        self.log(f"        +{key}: kaynaktan çıkamadı ({ex})")
            if i < len(keys) and not self.durdur:
                bekle = random.uniform(dmin, dmax)
                self.log(f"        {bekle:.0f} sn bekleniyor...")
                await asyncio.sleep(bekle)
        return ok, len(keys)


# --------------------------------------------------------------------------
#  Arayüz
# --------------------------------------------------------------------------
class App(tk.Tk):
    def __init__(self):
        super().__init__()
        self.title("Telegram Hesap Aktarıcı")
        self.geometry("960x720")
        self.minsize(820, 600)
        self.q = queue.Queue()
        self.w = Worker(lambda m: self.q.put(("log", m)))
        self.secili = set()
        self.butonlar = []
        self._ui()
        self._ayar_yukle()
        self.after(100, self._pump)

    # ---- yardımcılar ----
    def call(self, coro, done=None):
        self.mesgul(True)
        fut = asyncio.run_coroutine_threadsafe(coro, self.w.loop)
        fut.add_done_callback(lambda f: self.q.put((done, f)))

    def _pump(self):
        try:
            while True:
                item = self.q.get_nowait()
                if item[0] == "log":
                    self._yaz(item[1])
                    continue
                done, f = item
                self.mesgul(False)
                exc = f.exception()
                if exc:
                    self._yaz(f"HATA: {exc}")
                    messagebox.showerror("Hata", str(exc), parent=self)
                elif done:
                    done(f.result())
        except queue.Empty:
            pass
        self.after(100, self._pump)

    def _yaz(self, msg):
        self.logbox.configure(state="normal")
        self.logbox.insert("end", msg + "\n")
        self.logbox.see("end")
        self.logbox.configure(state="disabled")

    def mesgul(self, evet):
        for b in self.butonlar:
            b.configure(state="disabled" if evet else "normal")
        self.btn_durdur.configure(state="normal" if evet else "disabled")
        self.configure(cursor="watch" if evet else "")

    def buton(self, parent, text, cmd, **kw):
        b = ttk.Button(parent, text=text, command=cmd, **kw)
        self.butonlar.append(b)
        return b

    # ---- arayüz kurulumu ----
    def _ui(self):
        st = ttk.Style(self)
        st.configure("Big.TButton", font=("Segoe UI", 12, "bold"), padding=8)

        api = ttk.LabelFrame(self, text="Telegram API (my.telegram.org → API development tools)")
        api.pack(fill="x", padx=10, pady=(10, 4))
        ttk.Label(api, text="API ID:").pack(side="left", padx=(8, 2), pady=6)
        self.e_id = ttk.Entry(api, width=12)
        self.e_id.pack(side="left")
        ttk.Label(api, text="API HASH:").pack(side="left", padx=(12, 2))
        self.e_hash = ttk.Entry(api, width=40, show="•")
        self.e_hash.pack(side="left")
        self.buton(api, "Kaydet", self.ayar_kaydet).pack(side="left", padx=8)

        nb = ttk.Notebook(self)
        nb.pack(fill="both", expand=True, padx=10, pady=4)
        self.nb = nb

        # --- Sekme 1: Hesaplar
        t1 = ttk.Frame(nb)
        nb.add(t1, text="  1) Hesaplarım  ")
        bar = ttk.Frame(t1)
        bar.pack(fill="x", pady=6)
        self.buton(bar, "+ Hesap Ekle", self.hesap_ekle).pack(side="left", padx=4)
        self.buton(bar, "Yenile", self.hesaplari_yenile).pack(side="left", padx=4)
        self.buton(bar, "Seçili Hesabı Kaldır", self.hesap_kaldir).pack(side="left", padx=4)
        self.tv_hesap = ttk.Treeview(t1, columns=("tel", "ad", "id"), show="headings")
        for c, w, h in [("tel", 160, "Telefon"), ("ad", 420, "Ad"), ("id", 160, "Kullanıcı ID")]:
            self.tv_hesap.heading(c, text=h)
            self.tv_hesap.column(c, width=w)
        self.tv_hesap.pack(fill="both", expand=True)

        # --- Sekme 2: Gruplarım
        t2 = ttk.Frame(nb)
        nb.add(t2, text="  2) Gruplarım  ")
        bar = ttk.Frame(t2)
        bar.pack(fill="x", pady=6)
        self.buton(bar, "Tüm hesaplardaki grupları tara", self.gruplari_tara).pack(side="left", padx=4)
        self.buton(bar, "Seçiliyi KAYNAK yap", lambda: self.gruptan_doldur("kaynak")).pack(side="left", padx=4)
        self.buton(bar, "Seçiliyi HEDEF yap", lambda: self.gruptan_doldur("hedef")).pack(side="left", padx=4)
        ttk.Label(bar, text="Ara:").pack(side="left", padx=(16, 2))
        self.e_ara = ttk.Entry(bar, width=24)
        self.e_ara.pack(side="left")
        self.e_ara.bind("<KeyRelease>", lambda e: self._gruplari_goster())
        self.tv_grup = ttk.Treeview(t2, columns=("ad", "tur", "id", "kadi", "n"), show="headings")
        for c, w, h in [("ad", 330, "Grup / Kanal"), ("tur", 60, "Tür"), ("id", 150, "ID"),
                        ("kadi", 160, "@kullanıcıadı"), ("n", 110, "Hesabım sayısı")]:
            self.tv_grup.heading(c, text=h)
            self.tv_grup.column(c, width=w)
        self.tv_grup.pack(fill="both", expand=True)
        self.gruplar = {}

        # --- Sekme 3: Aktar
        t3 = ttk.Frame(nb)
        nb.add(t3, text="  3) Aktar  ")
        f = ttk.Frame(t3)
        f.pack(fill="x", pady=6)
        ttk.Label(f, text="Kaynak grup (listeden seç):").grid(row=0, column=0, sticky="w", padx=4)
        self.e_kaynak = ttk.Combobox(f, width=60)
        self.e_kaynak.grid(row=0, column=1, sticky="we", padx=4)
        self.e_kaynak.bind("<<ComboboxSelected>>", lambda e: self.kaynak_tara())
        self.buton(f, "Bu gruptaki hesaplarımı bul", self.kaynak_tara).grid(row=0, column=2, padx=4)
        ttk.Label(f, text="Hedef grup (listeden seç):").grid(row=1, column=0, sticky="w", padx=4, pady=4)
        self.e_hedef = ttk.Combobox(f, width=60)
        self.e_hedef.grid(row=1, column=1, sticky="we", padx=4)
        self.buton(f, "Grup listesini yenile", self.gruplari_tara).grid(row=1, column=2, padx=4)
        f.columnconfigure(1, weight=1)

        bar = ttk.Frame(t3)
        bar.pack(fill="x")
        self.buton(bar, "Tümünü seç", lambda: self._hepsini(True)).pack(side="left", padx=4)
        self.buton(bar, "Hiçbirini seçme", lambda: self._hepsini(False)).pack(side="left", padx=4)
        self.buton(bar, "Sadece kaynaktakileri seç", self._kaynaktakileri).pack(side="left", padx=4)
        self.lbl_sayi = ttk.Label(bar, text="")
        self.lbl_sayi.pack(side="left", padx=12)

        self.tv_aktar = ttk.Treeview(t3, columns=("sec", "tel", "ad", "durum"), show="headings", height=10)
        for c, w, h in [("sec", 50, "Seç"), ("tel", 150, "Telefon"), ("ad", 380, "Ad"), ("durum", 250, "Kaynakta mı?")]:
            self.tv_aktar.heading(c, text=h)
            self.tv_aktar.column(c, width=w, anchor="center" if c == "sec" else "w")
        self.tv_aktar.pack(fill="both", expand=True, pady=4)
        self.tv_aktar.bind("<Button-1>", self._tikla)

        alt = ttk.Frame(t3)
        alt.pack(fill="x", pady=6)
        self.v_cik = tk.BooleanVar(value=False)
        ttk.Checkbutton(alt, text="Katıldıktan sonra kaynak gruptan çıkar (TAŞI)", variable=self.v_cik).pack(side="left", padx=4)
        ttk.Label(alt, text="Hesaplar arası bekleme (sn):").pack(side="left", padx=(16, 2))
        self.s_min = ttk.Spinbox(alt, from_=0, to=600, width=5)
        self.s_min.set(15)
        self.s_min.pack(side="left")
        ttk.Label(alt, text="–").pack(side="left")
        self.s_max = ttk.Spinbox(alt, from_=0, to=900, width=5)
        self.s_max.set(40)
        self.s_max.pack(side="left")
        self.btn_durdur = ttk.Button(alt, text="Durdur", command=self.durdur, state="disabled")
        self.btn_durdur.pack(side="right", padx=4)
        self.buton(alt, "ÇEK", self.cek, style="Big.TButton").pack(side="right", padx=4)
        self.hesap_satir = {}  # anahtar -> (ad, kaynakta_mi)

        # --- Log
        lf = ttk.LabelFrame(self, text="İşlem kaydı")
        lf.pack(fill="both", padx=10, pady=(0, 10))
        self.logbox = tk.Text(lf, height=9, state="disabled", font=("Consolas", 9))
        self.logbox.pack(fill="both", expand=True)

    # ---- ayarlar ----
    def _ayar_yukle(self):
        try:
            with open(CFG, encoding="utf-8") as f:
                d = json.load(f)
            self.e_id.insert(0, str(d.get("api_id", "")))
            self.e_hash.insert(0, d.get("api_hash", ""))
            self.e_kaynak.insert(0, d.get("son_kaynak", ""))
            self.e_hedef.insert(0, d.get("son_hedef", ""))
            self._api_uygula()
            self.hesaplari_yenile()
        except FileNotFoundError:
            self._yaz("Hoş geldin! Önce my.telegram.org'dan API ID ve API HASH alıp yukarıya gir, Kaydet'e bas.")
        except Exception as ex:
            self._yaz(f"Ayarlar okunamadı: {ex}")

    def _api_uygula(self):
        try:
            self.w.api_id = int(self.e_id.get().strip())
        except ValueError:
            self.w.api_id = None
        self.w.api_hash = self.e_hash.get().strip() or None
        return bool(self.w.api_id and self.w.api_hash)

    def ayar_kaydet(self, sessiz=False):
        if not self._api_uygula() and not sessiz:
            messagebox.showwarning("Eksik", "API ID (sayı) ve API HASH gir.", parent=self)
            return
        d = {"api_id": self.w.api_id, "api_hash": self.w.api_hash,
             "son_kaynak": self.e_kaynak.get().strip(), "son_hedef": self.e_hedef.get().strip()}
        with open(CFG, "w", encoding="utf-8") as f:
            json.dump(d, f, ensure_ascii=False, indent=2)
        if not sessiz:
            self._yaz("Ayarlar kaydedildi.")
            self.hesaplari_yenile()

    # ---- hesaplar ----
    def hesaplari_yenile(self):
        if not self._api_uygula():
            return
        self.call(self.w.load_accounts(), self._hesaplar_geldi)

    def _hesaplar_geldi(self, rows):
        self.tv_hesap.delete(*self.tv_hesap.get_children())
        for key, ad, uid in rows:
            self.tv_hesap.insert("", "end", iid=key, values=("+" + key, ad, uid))
        aktif = sum(1 for r in rows if r[2] != "")
        self._yaz(f"{aktif} hesap hazır.")
        # aktar listesini de güncelle (kaynak bilgisi bilinmiyor)
        for key, ad, uid in rows:
            if uid != "" and key not in self.hesap_satir:
                self.hesap_satir[key] = (ad, None)
        for key in list(self.hesap_satir):
            if key not in self.w.me:
                self.hesap_satir.pop(key)
                self.secili.discard(key)
        self._aktar_listesi()
        # hesaplar hazırsa grup listesini kendiliğinden doldur
        if self.w.me and not self.gruplar and not getattr(self, "_ekleme_kuyrugu", None):
            self.gruplari_tara()

    def hesap_ekle(self):
        if not self._api_uygula():
            messagebox.showwarning("Eksik", "Önce API ID ve API HASH gir.", parent=self)
            return
        win = tk.Toplevel(self)
        win.title("Hesap Ekle")
        win.transient(self)
        ttk.Label(win, text="Numaraları alt alta yapıştır (ülke koduyla, ör. +905551112233).\n"
                            "Her numaraya Telegram'dan gelen kodu sırayla soracağım — her hesap için SADECE BİR KERE.").pack(padx=10, pady=8)
        txt = tk.Text(win, width=40, height=14)
        txt.pack(padx=10)
        txt.focus_set()

        def tamam():
            nums = re.findall(r"\+?\d[\d\s()-]{7,}\d", txt.get("1.0", "end"))
            keys = []
            for n in nums:
                k = re.sub(r"\D", "", n)
                if len(k) >= 8 and k not in keys and k not in self.w.me:
                    keys.append(k)
            win.destroy()
            if not keys:
                self._yaz("Eklenecek yeni numara yok.")
                return
            self._ekleme_kuyrugu = keys
            self._yaz(f"{len(keys)} numara sırayla eklenecek.")
            self._siradaki_hesap()

        self.buton(win, "Başla", tamam).pack(pady=8)

    def _siradaki_hesap(self):
        if not self._ekleme_kuyrugu:
            self._yaz("Hesap ekleme bitti.")
            self.hesaplari_yenile()
            return
        key = self._ekleme_kuyrugu.pop(0)
        self._yaz(f"+{key}: kod gönderiliyor...")
        fut = asyncio.run_coroutine_threadsafe(self.w.start_login(key), self.w.loop)
        fut.add_done_callback(lambda f: self.q.put((lambda r: self._kod_gonderildi(key, r), _Sarmal(f, self, key))))

    def _kod_gonderildi(self, key, r):
        if r is None:
            return self._siradaki_hesap()
        if r == "already":
            self._yaz(f"+{key} zaten giriş yapmış.")
            return self._siradaki_hesap()
        kod = simpledialog.askstring("Giriş Kodu", f"+{key} için Telegram'a gelen kodu gir\n(boş bırakırsan bu numarayı atlar):", parent=self)
        if not kod:
            self._yaz(f"+{key} atlandı.")
            return self._siradaki_hesap()
        fut = asyncio.run_coroutine_threadsafe(self.w.finish_login(key, kod.strip()), self.w.loop)
        fut.add_done_callback(lambda f: self.q.put((lambda r: self._giris_sonucu(key, r), _Sarmal(f, self, key))))

    def _giris_sonucu(self, key, r):
        if r is None:
            return self._siradaki_hesap()
        if r == "password":
            pw = simpledialog.askstring("İki Adımlı Doğrulama", f"+{key} hesabının bulut şifresi:", show="*", parent=self)
            if not pw:
                self._yaz(f"+{key} atlandı.")
                return self._siradaki_hesap()
            fut = asyncio.run_coroutine_threadsafe(self.w.finish_password(key, pw), self.w.loop)
            fut.add_done_callback(lambda f: self.q.put((lambda r: self._giris_sonucu(key, r), _Sarmal(f, self, key))))
            return
        self._yaz(f"+{key} eklendi: {r}")
        self._siradaki_hesap()

    def hesap_kaldir(self):
        sec = self.tv_hesap.selection()
        if not sec:
            return
        if not messagebox.askyesno("Kaldır", f"{len(sec)} hesabın oturumu kapatılıp listeden silinsin mi?", parent=self):
            return

        async def hepsi():
            for k in sec:
                await self.w.remove(k)
        self.call(hepsi(), lambda _: self.hesaplari_yenile())

    # ---- gruplar ----
    def gruplari_tara(self):
        if not self.w.me:
            messagebox.showinfo("Hesap yok", "Önce hesap ekle.", parent=self)
            return
        self._yaz("Gruplar taranıyor (hesap sayısına göre biraz sürebilir)...")
        self.call(self.w.scan_groups(), self._gruplar_geldi)

    def _grup_etiket(self, gid):
        g = self.gruplar[gid]
        return f"{g['title']}  [{('@' + g['username']) if g['username'] else gid}]"

    def _gruplar_geldi(self, g):
        self.gruplar = g
        self._yaz(f"Toplam {len(g)} farklı grup/kanal bulundu. Aktar sekmesinde listeden seçebilirsin.")
        self._gruplari_goster()
        etiketler = [self._grup_etiket(gid) for gid in sorted(g, key=lambda x: g[x]["title"].lower())]
        self.e_kaynak.configure(values=etiketler)
        self.e_hedef.configure(values=etiketler)

    def _gruplari_goster(self):
        ara = self.e_ara.get().strip().lower()
        self.tv_grup.delete(*self.tv_grup.get_children())
        for gid, g in sorted(self.gruplar.items(), key=lambda x: x[1]["title"].lower()):
            if ara and ara not in g["title"].lower() and ara not in g["username"].lower() and ara not in str(gid):
                continue
            self.tv_grup.insert("", "end", iid=str(gid), values=(
                g["title"], g["tur"], gid, ("@" + g["username"]) if g["username"] else "", len(g["hesaplar"])))

    def gruptan_doldur(self, ne):
        sec = self.tv_grup.selection()
        if not sec:
            messagebox.showinfo("Seç", "Listeden bir grup seç.", parent=self)
            return
        g = self.gruplar[int(sec[0])]
        deger = self._grup_etiket(int(sec[0]))
        e = self.e_kaynak if ne == "kaynak" else self.e_hedef
        e.delete(0, "end")
        e.insert(0, deger)
        self._yaz(f"{ne.upper()}: {g['title']} ({deger})")
        if ne == "kaynak":
            self.nb.select(2)
            self.kaynak_tara()

    # ---- aktar ----
    def kaynak_tara(self):
        ref = parse_ref(self.e_kaynak.get())
        if not ref:
            messagebox.showwarning("Kaynak", "Kaynak grubu gir (ID, @kullanıcıadı ya da link).", parent=self)
            return
        if not self.w.me:
            messagebox.showinfo("Hesap yok", "Önce hesap ekle.", parent=self)
            return
        self._yaz("Kaynak grupta hangi hesapların olduğu kontrol ediliyor...")
        self.call(self.w.scan_source(ref), self._kaynak_geldi)

    def _kaynak_geldi(self, rows):
        self.hesap_satir = {k: (ad, title) for k, ad, title in rows}
        self.secili = {k for k, ad, title in rows if title}
        n = len(self.secili)
        baslik = next((t for _, _, t in rows if t), None)
        self._yaz(f"Kaynak: {baslik or '?'} → {n} hesabın bu grupta (otomatik seçildi).")
        self._aktar_listesi()

    def _aktar_listesi(self):
        self.tv_aktar.delete(*self.tv_aktar.get_children())
        for k, (ad, title) in sorted(self.hesap_satir.items(), key=lambda x: (x[1][1] is None or x[1][1] is False, x[0])):
            durum = f"✓ {title}" if title else ("—" if title is None else "değil")
            if title is None and self.e_kaynak.get().strip() == "":
                durum = ""
            self.tv_aktar.insert("", "end", iid=k, values=("☑" if k in self.secili else "☐", "+" + k, ad, durum))
        self.lbl_sayi.configure(text=f"Seçili: {len(self.secili)} / {len(self.hesap_satir)}")

    def _tikla(self, ev):
        if self.tv_aktar.identify_region(ev.x, ev.y) != "cell":
            return
        k = self.tv_aktar.identify_row(ev.y)
        if not k:
            return
        self.secili.symmetric_difference_update({k})
        self.tv_aktar.set(k, "sec", "☑" if k in self.secili else "☐")
        self.lbl_sayi.configure(text=f"Seçili: {len(self.secili)} / {len(self.hesap_satir)}")

    def _hepsini(self, evet):
        self.secili = set(self.hesap_satir) if evet else set()
        self._aktar_listesi()

    def _kaynaktakileri(self):
        self.secili = {k for k, (ad, t) in self.hesap_satir.items() if t}
        self._aktar_listesi()

    def cek(self):
        hedef = parse_ref(self.e_hedef.get())
        if not hedef:
            messagebox.showwarning("Hedef", "Hedef grubu gir (davet linki, @kullanıcıadı ya da ID).", parent=self)
            return
        keys = [k for k in self.hesap_satir if k in self.secili and k in self.w.me]
        if not keys:
            messagebox.showwarning("Seçim", "Aktarılacak hesap seçmedin.", parent=self)
            return
        try:
            dmin, dmax = float(self.s_min.get()), float(self.s_max.get())
        except ValueError:
            dmin, dmax = 15, 40
        if dmax < dmin:
            dmin, dmax = dmax, dmin
        kaynak = parse_ref(self.e_kaynak.get())
        if self.v_cik.get() and not kaynak:
            messagebox.showwarning("Kaynak", "Kaynaktan çıkarmak için kaynak grubu da girmelisin.", parent=self)
            return
        islem = "TAŞINACAK (kaynaktan çıkılacak)" if self.v_cik.get() else "hedefe katılacak"
        if not messagebox.askyesno("Onay", f"{len(keys)} hesap {islem}.\nHedef: {self.e_hedef.get().strip()}\n\nBaşlasın mı?", parent=self):
            return
        self.ayar_kaydet(sessiz=True)
        self._yaz(f"--- Aktarım başladı: {len(keys)} hesap ---")
        self.call(self.w.transfer(keys, hedef, kaynak, self.v_cik.get(), dmin, dmax), self._bitti)

    def _bitti(self, r):
        ok, n = r
        self._yaz(f"--- Bitti: {ok}/{n} hesap hedef grupta ---")
        messagebox.showinfo("Bitti", f"{ok}/{n} hesap hedef grupta.", parent=self)

    def durdur(self):
        self.w.durdur = True
        self._yaz("Durdurma istendi, şu anki hesap bitince duracak...")


class _Sarmal:
    """Hesap eklerken bir numarada hata olursa gösterip sıradakine geçer."""
    def __init__(self, fut, app, key):
        self.fut, self.app, self.key = fut, app, key

    def exception(self):
        return None

    def result(self):
        exc = self.fut.exception()
        if exc:
            self.app._yaz(f"+{self.key}: {exc} — atlanıyor")
            return None
        return self.fut.result()


if __name__ == "__main__":
    App().mainloop()
