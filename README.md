# 🚀 Telegram Üye Aktarıcı (Web Dashboard & MTProto Pro)

Bu proje; bir Telegram hesabına **QR kod ile giriş yaparak**, o hesabın yönetici (Admin) olduğu **hedef gruba**, belirtilen herhangi bir **kaynak gruptan** çekilen üyeleri seçerek ya da hariç tutarak aktarmanızı sağlayan modern, hızlı ve tam otomatik bir web uygulamasıdır.

Render.com üzerinde tek tıkla deploy edilebilir veya yerel bilgisayarınızda anında çalıştırılabilir şekilde hazırlanmıştır.

---

## ✨ Özellikler

1. **📲 QR Kod ile Hızlı Giriş:**
   - Telefonunuzdan Telegram > *Ayarlar > Cihazlar > QR Kod Tara* diyerek saniyeler içinde oturum açın.
   - 2 Adımlı Doğrulama (2FA) şifresi desteği mevcuttur.

2. **👑 Hedef Grup Yönetimi (Admin Yetkisi):**
   - Giriş yaptığınız hesabın admin olduğu tüm gruplar açılır listede otomatik listelenir.
   - İsterseniz grup linkini (`https://t.me/...`), `@kullaniciadi` veya grup ID'sini manuel olarak da girebilirsiniz.

3. **🔍 Kaynak Gruptan Üye Çekme (Scraping):**
   - Açık olan veya üyesi olduğunuz herhangi bir kaynak grubun linkini girerek tüm üyeleri saniyeler içinde alt alta listeleyin.
   - İsim, kullanıcı adı (@username), Telegram ID, aktiflik durumu (Çevrimiçi, Yakın Zamanda, Çevrimdışı) ve Bot rozetleri görüntülenir.

4. **🎯 2 Farklı Aktarım Modu (Tam İstediğiniz Gibi):**
   - **Mod 1: Seçilenleri Aktar** → Listeden kutucuğunu işaretlediğiniz üyeler hedef gruba eklenir.
   - **Mod 2: Seçilenler HARİÇ Herkesi Aktar** → İşaretlediğiniz kişiler kara listeye (istisnaya) alınır; onların dışındaki tüm grup üyeleri otomatik olarak hedef gruba eklenir.

5. **🛡️ Akıllı Anti-Flood & Ban Koruma Sistemi:**
   - Ekleme işlemleri arasına ayarlanabilir gecikme (delay - varsayılan 3.0 sn) koyar.
   - Telegram `FloodWait`, `UserPrivacyRestricted` (gizlilik kısıtı) ve `PeerFlood` durumlarını otomatik yakalar; hesabı riske atmadan güvenle yönetir.
   - Canlı İlerleme Çubuğu (%0 - %100), Sayaçlar (Başarılı, Başarısız/Atlanan, Kalan) ve canlı terminal konsolu.

---

## ☁️ Render.com Üzerinde Deploy Etme (Adım Adım)

Proje Render üzerinde sorunsuz çalışması için `render.yaml`, `Dockerfile`, `Procfile` ve dinamik `$PORT` desteği ile hazırlanmıştır.

### Yöntem 1: GitHub ile Render Web Service (Önerilen)

1. Bu klasördeki dosyaları bir GitHub deposuna yükleyin (veya Render CLI kullanın).
2. [Render.com](https://render.com) hesabınıza giriş yapın ve **"New +" -> "Web Service"** seçeneğine tıklayın.
3. GitHub reponuzu seçin.
4. Açılan yapılandırma ekranında şu ayarları girin:
   - **Name:** `telegram-uye-aktarici` (veya istediğiniz bir isim)
   - **Language / Runtime:** `Python` (veya `Docker`)
   - **Build Command:** `pip install -r requirements.txt`
   - **Start Command:** `uvicorn app:app --host 0.0.0.0 --port $PORT`
5. **Environment Variables (Ortam Değişkenleri)** kısmına şunları ekleyin (isteğe bağlı, web panelinden de girebilirsiniz):
   - `TELEGRAM_API_ID`: my.telegram.org'dan aldığınız API ID
   - `TELEGRAM_API_HASH`: my.telegram.org'dan aldığınız API Hash
6. **"Create Web Service"** butonuna basın!
7. 1-2 dakika içinde Render size bir URL verecektir (örn: `https://telegram-uye-aktarici.onrender.com`). Bu linke tıklayarak web panelini kullanabilirsiniz!

---

## 💻 Bilgisayarınızda (Yerel) Çalıştırma

1. Klasördeki **`baslat.bat`** dosyasına çift tıklayın.
2. Gerekli kütüphaneler otomatik kurulacak ve tarayıcınızda `http://localhost:8000` sayfası anında açılacaktır.

---

## 🔑 Telegram API ID ve Hash Nereden Alınır?

Telegram MTProto Userbot altyapısı kullandığı için API kimliği gereklidir:
1. [my.telegram.org](https://my.telegram.org) adresine gidin.
2. Telefon numaranızla giriş yapın.
3. **API development tools** seçeneğine tıklayın.
4. Herhangi bir uygulama adı yazarak onaylayın.
5. Sayfada beliren **`api_id`** ve **`api_hash`** değerlerini kopyalayıp web panelimizin sağ üstündeki **"API Ayarları"** butonuna basarak kaydedin.

---

## ⚠️ Önemli Güvenlik Tavsiyeleri
- Telegram, yeni açılmış hesaplarda toplu üye eklemeye karşı daha hassastır. Tercihen en az 1-2 aylık, doğrulanmış hesaplar kullanın.
- Her üye ekleme arasına **3 ila 5 saniye gecikme (delay)** koymanız önerilir.
- Telegram'ın günlük kullanıcı ekleme limitlerine (genellikle günde 50-200 arası) dikkat edilmesi hesabınızın sağlığı açısından önemlidir.
