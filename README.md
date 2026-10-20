# Harbor — Dokploy production kurulumu

Harbor **v2.15.4**, resmi image digest'lerine sabitlenmiş Docker Compose paketi. Güncel kararlı sürüm ve tüm image başlangıç komutları Docker Hub/GitHub üzerinden doğrulanır. Kullanıcı trafiğinde TLS'i Dokploy/Traefik sonlandırır. Veriler yerel named volume'larda tutulur. Yedekleme tek ayarla kapatılabilir.

Bu paket tek sunuculudur: sunucu arızasında otomatik failover sağlamaz. RPO/RTO hedefleri yedekleme açıkken ve gerçek restore testiyle ölçüldüğünde geçerlidir.

## 1. Dokploy arayüzünden kurulum

Gereksinimler: Linux **amd64**, Docker Engine **27+**, Docker Compose **2.35+**, Compose build desteği ve subpath volume mount'larını koruyan Dokploy sürümü. Harbor için başlangıçta 4 vCPU, 8 GB RAM ve en az 160 GB SSD ayırın; Dokploy ve diğer uygulamaların kaynaklarını ayrıca hesaba katın. Diskin gerçek kullanımını izleyin.

1. Bu repoyu erişebildiğiniz Git sağlayıcısına koyun. Dokploy'da bir **Compose** uygulaması oluşturun; türü **Docker Compose** seçin. Docker Stack kullanmayın.
2. Git kaynağını ve Compose Path olarak **compose.yaml** dosyasını seçin. Production Auto Deploy'u kapalı tutun. Derlemeler bu repodan yapılır; ek image registry'si veya SSH hazırlık adımı gerekmez.
3. Environment ekranına [.env.example](.env.example) içeriğini aktarın. Kendi domain'inizi, benzersiz kalıcı instance ID'nizi ve en az 20 karakterlik iki ayrı rastgele parolayı girin. Özel karakterli değerleri Compose .env kurallarına göre tek tırnakla çevreleyin; gerçek .env dosyasını Git'e koymayın.
4. Yedek kullanmayacaksanız **BACKUP_ENABLED=false** yapın. Bu durumda S3/Restic alanları boş kalabilir. Yedek kullanacaksanız aşağıdaki S3 ayarlarını tamamlayın.
5. Domains ekranında servis **proxy**, Container Port **8080**, Host kendi Harbor domain'iniz, Path **/**, HTTPS ve Let's Encrypt seçin. HTTP→HTTPS yönlendirmesini etkinleştirin. DNS A/AAAA kayıtları doğru sunucuya gitmeli; kullanılmayan hatalı AAAA kaydı bırakılmamalıdır.
6. Preview Compose'ta proxy'nin Harbor özel ağına ve Traefik ağına bağlı kaldığını doğrulayın. Traefik ağınızın adı farklıysa DOKPLOY_NETWORK'ü değiştirin. Diğer Harbor servislerini Traefik ağına bağlamayın. Host portu eklemeyin.
7. Deploy'a basın. Önce harbor-init başarıyla tamamlanır; ardından DB/cache, Harbor ve proxy sağlıklı duruma gelir. İlk sertifikanın çıkmasını bekleyin. prepare başarısızsa operations volume içindeki prepare-error.log ayrıntı içerir; bu özel dosyada hassas ayarlar bulunabilir, paylaşmadan önce temizleyin.
8. Harbor'a admin hesabıyla giriş yapın. Güncel admin parolasını HARBOR_API_PASSWORD olarak Dokploy'a girip yeniden deploy edin. Operations servisine bir **Compose Job** tanımlayıp bir kez **python3 /app/operations.py policies** çalıştırın.
9. Kullanıcı/robot eklemeden ve image yayımlamadan önce aşağıdaki kabul kontrollerini tamamlayın.

Domain bir kök adres olmalıdır: https://registry.sirketiniz.com. /harbor gibi alt yol desteklenmez. Harbor proxy özel ağda HTTP kullanır; internetten erişim HTTPS üzerinden gerçekleşir. Registry protokolünün çalışması için Harbor domain'inin önüne Dokploy Basic Auth/ForwardAuth eklemeyin; kimlik doğrulamayı Harbor yapar.

İlk açılıştan itibaren self-registration kapalı, auth_mode=db_auth ve proje oluşturma adminonly olarak core ortamından kilitlenir. Bu üç ayar UI'dan açılamaz. Başlangıç library projesi politika işiyle private yapılır. Proje oluşturduktan sonra politika işini yeni proje adıyla çalıştırın; yalnızca POLICY_PROJECTS listesindeki projeler yönetilir.

## 2. Değiştirilebilir ayarlar ve politikalar

| Ayar | Varsayılan / davranış |
|---|---|
| HARBOR_INSTANCE_ID | Volume isimlerinin kalıcı öneki; kurulumdan sonra değiştirmeyin, başka projede tekrar kullanmayın |
| HARBOR_HOSTNAME | HTTPS external_url buradan üretilir; Domains ekranıyla aynı olmalı |
| HARBOR_ADMIN_PASSWORD | Sadece ilk kurulum parolası; mevcut admin parolasını döndürmez |
| HARBOR_DB_PASSWORD | PostgreSQL ve Harbor bağlantısının ortak parolası; mevcut DB'de SQL rotasyonu gerekir |
| BACKUP_ENABLED | true; false ile otomatik yedek ve yedek gecikmesi kontrolü atlanır |
| PROJECT_QUOTA_GIB | 20 GiB; yeni proje varsayılanı, politika işinin yönettiği mevcut projelere de uygulanır |
| POLICY_PROJECTS | library,production,development; açıkça çalıştırılan politika işinin hedefleri |
| RETENTION_KEEP_COUNT / DAYS | dev-* tag'leri için son 20 artifact VEYA son 30 günde çekilenler korunur |
| ALLOW_PRIVATE_NETWORK_ACCESS | false; özel ağ webhook'ları kapalı |
| DB_MAX_OPEN_CONNS / POSTGRES_MAX_CONNECTIONS | 100 / 256; core/exporter havuzları için DB tarafında pay bırakın |
| JOB_MAX_WORKERS | 5; kapasite ölçümüne göre artırın |
| *_MEMORY_LIMIT / *_CPU_LIMIT | Servis bazında Compose limitleri; .env.example içinde tamamı var |
| HARBOR_HTTP_PROXY / HTTPS_PROXY / NO_PROXY | Harbor, jobservice ve Trivy dış bağlantıları |

Image sürümü tek başına Environment'tan değiştirilmez. Upstream şablonu, image digest'leri ve başlangıç komutları birlikte güncellenir.

Politika işi: seçilen projeleri private yapar, push sonrası taramayı açar, kritik zafiyetlerde pull'u engeller, kotayı ayarlar, v* ve release-* tag'lerini immutable yapar. dev-* dışındaki tag'ler retention tarafından korunur; untagged artifact'lar otomatik GC ile silinmez. Retention günlük UTC 22:00 / İstanbul 01:00'de çalışır. Kurallar Harbor'ın OR semantiğiyle birleştirilir. Diğer projeler otomatik değiştirilmez.

Bu iş açıkça çağrıldığında listelenen proje ayarlarını yeniden uygular. Restart sırasında çalışmaz; arada yapılan UI değişiklikleri korunur. İmza zorunluluğu kapalıdır. CI/CD için proje kapsamlı, süreli, sadece gereken push/pull yetkili robot oluşturun; admin hesabını pipeline'a vermeyin.

### Ayar değişikliği ve güncelleme

Yalnızca yedek, bildirim veya operasyon ayarını değiştirmek: Environment'ı kaydedip Deploy yapın. Harbor ayar hash'i değişmez; servis dosyaları yeniden üretilmez. Aktif operasyon varsa init kilidi nedeniyle deployment başarısız olabilir; operasyon bittikten sonra yeniden deneyin.

Harbor domain'i, DB bağlantısı, cache, worker, proxy, kota varsayılanı veya image sürümü değişikliği:

1. Yedek açıksa başarılı bir manuel yedek alın. Kapalıysa rollback için tutarlı bir yedeğiniz olduğunu ayrıca doğrulayın; yedeksiz schema değişikliği otomatik geri alınamaz.
2. [scripts/dokploy-job.sh](scripts/dokploy-job.sh) içeriğini Dokploy **Server Job** alanına kopyalayın. HARBOR_INSTANCE_ID'yi doğru seçin ve ACTION=maintenance yaparak bir kez çalıştırın. Yerel sunucuda Dokploy Server Job, uzak sunucuda Server Job kullanın. İş, Harbor workload'unu durdurur; operations container'ı açık kalır.
3. Environment/Git revision değişikliklerini uygulayıp Deploy'a basın. Harbor init bakım iznini kullanarak ayarları yeniden üretir; dahili şifreleme ve imzalama anahtarlarını korur.
4. Sağlık, login ve push/pull kontrollerini yapın. Volume silmeyin; down -v, volume prune ve Dokploy'un volume silme seçeneklerini kullanmayın.

Bakım başlatıp değişiklikten vazgeçerseniz aynı Server Job'u ACTION=recover ile çalıştırın. Yarım kalan prepare/restore sonrasında recover kullanmayın: hazır işareti olmayan yapılandırmayı init/redeployment ile tamamlayın. Birden fazla Compose projesini aynı instance ID ile çalıştırmayın.

Parola rotasyonu: admin parolasını Harbor UI'dan değiştirin, HARBOR_API_PASSWORD değerini güncelleyin. DB parolasında önce yazan servisleri maintenance ile durdurun; Dokploy DB konsolundan PostgreSQL'i tek başına başlatıp psql içinde **\password postgres** kullanın. Yeni HARBOR_DB_PASSWORD'ü kaydedin ve yeniden deploy edin. Sadece Environment değerini değiştirmek mevcut PostgreSQL parolasını değiştirmez.

Güncel sürümü kontrol etme ve hazırlama (geliştirme bilgisayarında/CI'da Node 22+):

~~~sh
node tools/update-images.mjs --check
node tools/update-images.mjs
node tools/check.mjs
~~~

İkinci komut en güncel kararlı Harbor sürümünü alır; goharbor image'larını, OCI başlangıç metadata'sını ve resmi şablonu birlikte günceller. Değişiklikleri inceleyin, test edin ve upstream destekli upgrade yoluna uyun. Rollback, eski image'ların yanında aynı backup setindeki DB/registry/anahtarları da geri yüklemeyi gerektirebilir.

## 3. İsteğe bağlı S3 yedekleme

~~~dotenv
BACKUP_ENABLED=true
RESTIC_REPOSITORY=s3:https://s3.example.net/backup-bucket/harbor-prod
RESTIC_PASSWORD='ayri-ve-uzun-bir-sifreleme-parolasi'
AWS_ACCESS_KEY_ID=...
AWS_SECRET_ACCESS_KEY=...
AWS_DEFAULT_REGION=us-east-1
~~~

S3 hedefi Harbor sunucusundan bağımsız olmalı. Hesaba yalnızca ilgili bucket/prefix yetkilerini verin. Restic parolasını ve S3 erişimini sunucu dışındaki parola kasasında da saklayın; şifreleme parolası olmadan yedek geri açılamaz. Hedefe TLS doğrulaması zorunludur.

1. Deploy sonrası operations container'ında Compose Job olarak **python3 /app/operations.py init-repository** komutunu bir kez çalıştırın. Var olan repoda tekrar init yapmayın.
2. scripts/dokploy-job.sh içeriğini doğru instance ID ve ACTION=backup ile Dokploy Server Job'a koyun. Saat dilimi Europe/Istanbul ise günlük **0 3 * * ***; scheduler UTC ise **0 0 * * *** seçin. Kaydettikten sonra ekranda görünen sonraki çalışma zamanını doğrulayın.
3. İlk yedeği manuel tetikleyin. **python3 /app/operations.py snapshots** ile snapshot ID'sini listeleyin.
4. Saklama varsayılanı 7 günlük, 4 haftalık, 6 aylıktır. Silme/prune sadece bu instance host'unun Harbor yedeklerine uygulanır.

Yedek işlemi bakım kilidi alır, yazan servisleri durdurur, mantıksal PostgreSQL dump alır, DB/cache'i düzgün kapatır; registry, DB, cache, job verileri, yapılandırma ve anahtarları soğuk ve tutarlı bir set olarak şifreler. **Servis kesintisi Restic upload tamamlanana kadar sürer**; ilk tam yedek uzun sürebilir. Yeniden başlatma sonrasında retention/prune yapılır. Trivy DB/reports yeniden oluşturulabilir ve yedek setine dahil değildir. Full restore için ayrıca yeterli yerel staging alanı gerekir.

Başarısız dump/upload ve SIGTERM'de servisler yeniden başlatılır. Süreç zorla öldürülürse kalıcı bakım kaydı tutulur. Aynı Server Job'u ACTION=recover ile çalıştırın; kaydedilen container ID'leri değişmişse otomatik toparlama reddedilir. Bildirim hedefi NOTIFICATION_WEBHOOK ve isteğe bağlı bearer token ile ayarlanır; hata durumunda görev başarısız exit kodu döndürür.

### Yedeklemeyi kapatmak

Environment'ta **BACKUP_ENABLED=false** yapıp Deploy edin. Sonraki otomatik veya manuel backup çağrıları **disabled/skipped** sonucu verir. Kilit alınmaz, S3'e bağlanılmaz, servis durdurulmaz, retention çalışmaz. S3 bilgileri artık zorunlu değildir. Mevcut yedekler silinmez. Aktif yedek varsa önce güvenli biçimde tamamlanmasına izin verin; ayar sonraki başarılı deployment ile geçer.

Tekrar açarken S3 ve Restic bilgilerini girip Deploy edin. Var olan repository için init'i tekrar çalıştırmayın. İlk yedeği doğrulayın. Otomatik yedek kapalıyken snapshots/restore komutları, bağlantı bilgileri sağlandığında kullanılabilir.

## 4. Yeni instance'a geri yükleme

Geri yükleme mevcut veriyi silmez; **tüm hedef veri/config volume'ları boş olmalıdır**.

1. Ayrı bir Dokploy Compose uygulaması oluşturun. Benzersiz yeni HARBOR_INSTANCE_ID seçin. Aynı repo ve **snapshot'taki Harbor sürümünü** kullanın; Compose Path **compose.restore.yaml** olsun.
2. Domain ve S3/Restic bilgilerini girin. BACKUP_ENABLED=false olabilir. Deploy edin. Bu tanım sadece recovery init ve operations container'ı açar; DB/registry başlatmaz. Hedef domain'i henüz trafiğe açmayın.
3. operations üzerinde **python3 /app/operations.py snapshots** ile açık snapshot ID'sini seçin.
4. Server Job şablonunda yeni instance ID'yi, ACTION=restore değerini kullanın. Son docker run satırına ACTION'dan sonra **--snapshot SNAPSHOT_ID --confirm-target YENI_INSTANCE_ID** ekleyin. İşi çalıştırın.
5. İş snapshot doğrulamasını, sürüm kontrolünü ve UID/izinleri koruyan kopyalamayı yapar. Hedef doluysa veya snapshot symlink içeriyorsa otomatik yazmayı reddeder. Yarım kalan restore'u dolu hedef üzerinde tekrar çalıştırmayın; ayrı boş instance kullanın.
6. Environment'a snapshot'ın **orijinal DB parolasını** girin. Compose Path'i **compose.yaml** yapın ve Deploy edin. Sertifika/domain yönlendirmesini kurun. Init geri yüklenen anahtarları koruyarak yeni adresin ayarlarını üretir.
7. Kullanıcı/robot erişimini, projeleri ve örnek artifact digest'lerini doğrulayın; gerçek RTO'yu kaydedin. Ancak bundan sonra istemcileri yeni instance'a yönlendirin. Yeni instance'ta yedeklemeyi ayrıca etkinleştirin.

İlk hedef RPO ≤24 saat ve RTO ≤4 saattir; storage boyutu, ağ ve restore provası bu hedefleri belirler. BACKUP_ENABLED=false olduğunda bu hedefler ve yedek restore açılış şartı uygulanmaz.

## 5. İzleme ve bakım görevleri

operations servisi için Dokploy Compose Job komutları:

| Sıklık | Komut |
|---|---|
| İlk kurulumda / bilinçli politika değişikliğinde | python3 /app/operations.py policies |
| Haftalık, örneğin Pazar İstanbul 05:00 | python3 /app/operations.py gc |
| 15 dakikada bir | python3 /app/operations.py monitor |
| İhtiyaç halinde | python3 /app/operations.py check |

GC ve backup aynı kilidi paylaşır. GC arka planda çalışırken polling işi kesilse bile kalıcı kayıt backup'ı engeller; **gc** komutunu tekrar çalıştırmak aynı işi uzlaştırır. Harbor UI'dan ayrı otomatik GC takvimi oluşturmayın. Uzun GC bakımında zaman aşımı kaydı varsa Harbor UI'dan iş durumunu inceleyin; bitmemiş GC varken backup başlatmayın.

monitor: disk %75 uyarı/%85 kritik, Harbor health, HTTPS doğrulaması, sertifikada 14 günden az süre, Trivy DB'nin 48 saatten eski/eksik olması ve açıkken son başarılı yedeğin 26 saatten eski olması durumlarını bildirir. Planlı bakım sırasında sağlık kesintisi kontrolü ertelenir; bakım 4 saati aşarsa MAINTENANCE_MAX_HOURS sınırı alarm üretir. İlk taramadan önce Trivy DB eksik uyarısı normaldir; bir test image'ı taratıp tekrar kontrol edin. BACKUP_ENABLED=false yalnızca yedek kontrolünü kapatır. İzleme endpoint'i erişilemiyorsa başarısızlık döner; hata bildirimlerini Dokploy/HTTPS alıcınızda takip edin.

Metrics proxy'nin **9090 /metrics** adresinden özel ağda sunulur. [monitoring/prometheus.yml](monitoring/prometheus.yml) örneğini Harbor'ın özel ağına bağlı mevcut Prometheus'a uyarlayın. Metrics için domain/host portu eklemeyin. Loglar Docker local driver ile döndürülür ve Dokploy Logs'tan okunur. Sunucu dışı log saklama için mevcut log toplayıcınızı kullanın.

## 6. Testler ve production kabulü

~~~sh
node tools/check.mjs
# Native Linux Python 3.10+ ile (sıfır üçüncü taraf test bağımlılığı):
python3 -m unittest discover -s tests -v
# Docker ile izole unit test: tools/test.sh
~~~

GitHub Actions, sürüm/Compose kontrollerini ve Python testlerini çalıştırır. Elle başlatılan integration işi resmi prepare image'ıyla boş volume kurulumunu, sağlık kontrolünü, restart/redeploy ve kapalı backup davranışını sınar. Üretim verisi üzerinde çalıştırılmaz.

Canlı açılışta ayrıca doğrulayın:

- Domain ve trusted HTTPS; /v2/ yanıtının 401 Bearer challenge döndürmesi, realm'in doğru HTTPS domain'ini göstermesi.
- docker login, büyük image push/pull, digest ve multi-architecture artifact.
- Yetkisiz push/pull engeli, private proje ve minimum yetkili süreli robot.
- Trivy taraması, kritik zafiyet engeli, taranmamış artifact ve scanner erişilememe davranışı.
- Restart/redeploy sonrası kullanıcılar, artifact'lar ve anahtarların korunması.
- Beklenen eşzamanlı CI yükünde CPU/RAM/disk ve upload timeout ölçümü. Harbor Nginx body limitini kaldırır ve upload için 900 saniye timeout kullanır; Traefik entrypoint timeout'u büyük upload için ayrıca Dokploy ayarlarından uyarlanmalıdır.
- Yedek açıkken gerçek S3 yedeği, hata bildirimi ve yeni instance'a restore/digest karşılaştırması.
- Yedek kapalıyken S3 bilgileri olmadan kurulum ve backup işinin hiçbir servis durdurmaması.

Bu çalışma ortamında Docker/Python süreçleri işletim sistemi erişim kısıtı nedeniyle çalıştırılamadı. Image metadata/digest doğrulamaları ve JS tabanlı yapısal/sözdizimi kontrolleri yerelde yapılabilir; native unit test ve gerçek Docker kurulum/restore sonuçlarını CI veya hedef staging ortamında doğrulayın.

## Kaynaklar

- https://github.com/goharbor/harbor/releases/latest
- https://goharbor.io/docs/2.15.0/install-config/
- https://goharbor.io/docs/2.15.0/install-config/configure-system-settings-cli/
- https://docs.dokploy.com/docs/core/docker-compose
- https://docs.dokploy.com/docs/core/docker-compose/domains
- https://docs.dokploy.com/docs/core/schedule-jobs

Vendor şablon lisansı ve kaynağı [vendor/NOTICE.md](vendor/NOTICE.md) içinde. .next ve node_modules dizinleri repo aramalarına dahil edilmez.
