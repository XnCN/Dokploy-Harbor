# Dokploy Harbor

**Dokploy üzerinde production odaklı Harbor kurulumu.**

Harbor'ı tek Docker sunucusunda kalıcı depolama, değiştirilebilir proje politikaları, zafiyet taraması ve isteğe bağlı şifreli S3 yedekleriyle çalıştırın. Dağıtım ve işletim adımlarını Dokploy üzerinden yönetin.

[![Harbor v2.15.4](https://img.shields.io/badge/Harbor-v2.15.4-60B932)](https://github.com/goharbor/harbor/releases/tag/v2.15.4)
[![Platform linux/amd64](https://img.shields.io/badge/platform-linux%2Famd64-2496ED)](#gereksinimler)
[![Docker Compose](https://img.shields.io/badge/Docker-Compose-2496ED)](compose.yaml)
[![Dokploy](https://img.shields.io/badge/deploy-Dokploy-111827)](https://dokploy.com/)

[English](README.md) · **Türkçe**

[Hızlı kurulum](#hızlı-kurulum) · [Yapılandırma](#yapılandırma) · [Yedekleme](#yedekleme) · [Geri yükleme](#geri-yükleme) · [Operasyonlar](#operasyonlar) · [Geliştirme](#geliştirme)

## Neler sunar?

- **Dokploy kurulumu:** resmi Harbor hazırlık aracını çalıştıran başlangıç container'ı ve Compose tanımı.
- **Tekrarlanabilir image'lar:** upstream image digest'leri, başlangıç komutları ve Harbor yapılandırma şablonu birlikte sürümlenir.
- **Kalıcı veriler:** registry, PostgreSQL, cache durumu, yapılandırma ve dahili anahtarlar yeniden dağıtımlarda named volume'larda korunur.
- **Proje politikaları:** açıkça çalıştırılan bir komutla private projeler, kota, push sonrası tarama, zafiyet engeli, retention ve release tag koruması.
- **İsteğe bağlı yedekleme:** Restic ile S3 uyumlu depoya tutarlı ve şifreli yedekler. ``BACKUP_ENABLED=false`` ile kapatılabilir.
- **İşletim araçları:** bakım ve kurtarma komutları, sağlık izlemesi, özel ağdan metrics, log rotasyonu ve CI kontrolleri.

Bu repo, [Harbor](https://goharbor.io/) ve [Dokploy](https://dokploy.com/) için bağımsız bir kurulum paketidir. **Tek sunucuyu** hedefler; bakım ve sunucu arızası hizmet kesintisine yol açabilir. Yüksek erişilebilirlik kapsam dışındadır.

## Mimari

~~~mermaid
flowchart TD
    client[Docker istemcileri ve tarayıcı] -->|HTTPS| traefik[Dokploy / Traefik]
    traefik -->|HTTP :8080| proxy[Harbor proxy]
    proxy --> portal[Portal]
    proxy --> core[Core API]
    core --> registry[Registry]
    core --> db[(PostgreSQL)]
    core --> cache[(Redis / Valkey)]
    core --> jobs[Jobservice]
    jobs --> trivy[Trivy]
    registry --> data[(Kalıcı image depolaması)]
~~~

Dış erişimde TLS ve sertifika yenilemeyi Traefik yönetir. Yalnızca proxy servisi Traefik ağına bağlanır. Dahili servisler host portu yayımlamaz.

## Gereksinimler

| Bileşen | Başlangıç gereksinimi |
|---|---|
| Sunucu | Linux, amd64 |
| Docker Engine | 27+ |
| Docker Compose | 2.35+ |
| Dokploy | Docker Compose build ve volume subpath desteğini koruyan sürüm |
| Harbor kaynakları | Başlangıçta 4 vCPU, 8 GB RAM ve en az 160 GB SSD |
| Ağ | Sunucuya yönlendirilmiş ayrı bir domain; seçilen sertifika yöntemi için erişilebilir HTTP/HTTPS |

Dokploy ve diğer uygulamalar için ayrıca kaynak bırakın. Depolama ve eşzamanlı işlem limitlerini gerçek yükünüze göre belirleyin. Node.js 22+ yalnızca repo bakım araçları için gerekir; çalışan Harbor servislerinin bağımlılığı değildir.

## Hızlı kurulum

### 1. Repoyu bağlayın

Dokploy'da bir Compose servisi oluşturup bu Git reposunu bağlayın. Tür olarak **Docker Compose**, Compose Path olarak **./compose.yaml** seçin. İlk aşamada otomatik production dağıtımını kapalı tutun.

Build işlemleri bu repodan yapılır; sunucuda SSH üzerinden elle Harbor hazırlık komutu çalıştırmanız gerekmez.

### 2. Ortam değişkenlerini girin

Tüm ayarlar için [.env.example](.env.example) dosyasını kullanın. Yedekleme kapalı bir ilk kurulum için:

~~~dotenv
HARBOR_INSTANCE_ID=harbor-prod
HARBOR_HOSTNAME=harbor.example.com
HARBOR_ADMIN_PASSWORD=
HARBOR_DB_PASSWORD=
BACKUP_ENABLED=false
~~~

Örnek domain'i değiştirin ve iki parola alanını birbirinden farklı, rastgele üretilmiş en az 20 karakterlik değerlerle doldurun. Hostname içine protokol, port veya path yazmayın. Özel karakterlerde Compose .env tırnaklama kurallarını kullanın; gerçek kimlik bilgilerini Git'e koymayın.

`HARBOR_INSTANCE_ID` kalıcı volume önekidir. Kurulumdan sonra sabit tutun ve başka bir dağıtımda tekrar kullanmayın.

Paketin varsayılanı **``BACKUP_ENABLED=true``** değeridir. S3 bilgileri olmadan kuracaksanız açıkça false yapın.

### 3. Dokploy üzerinden domain ekleyin

DNS kaydını sunucuya yönlendirdikten sonra şu ayarlarla domain ekleyin:

| Dokploy alanı | Değer |
|---|---|
| Host | `HARBOR_HOSTNAME` ile aynı domain |
| Service | **proxy** |
| Container port | **8080** |
| Path | / |
| HTTPS | Açık |
| Certificate | Let's Encrypt veya yapılandırdığınız sertifika |

HTTP→HTTPS yönlendirmesini açın. Preview Compose'ta proxy'nin hem Harbor ağına hem Traefik ağına bağlı kaldığını doğrulayın. Traefik ağının adı farklıysa `DOKPLOY_NETWORK` değerini değiştirin. Host portu eşlemesi gerekmez.

https://harbor.example.com gibi ayrı bir kök domain kullanın. /harbor altında path tabanlı yayın desteklenmez. Registry kimlik doğrulamasını Harbor yönetir; önüne Basic Auth veya ForwardAuth eklemek Docker istemcilerini bozabilir.

### 4. Deploy edin ve giriş yapın

Deploy'a basın. Beklenen akış:

1. harbor-init, exit code 0 ile başarıyla tamamlanır.
2. PostgreSQL, cache ve Harbor servisleri healthy durumuna gelir.
3. HTTPS adresi erişilebilir olur.

**admin** kullanıcısı ve `HARBOR_ADMIN_PASSWORD` ile giriş yapın. Başlangıç hatalarında deployment ve init loglarını kontrol edin. Ayrıntılı prepare hataları operations volume'undaki özel prepare-error.log dosyasına yazılır; paylaşmadan önce hassas içeriği temizleyin.

### 5. Proje politikalarını nasıl yöneteceğinizi seçin

Projeleri Harbor arayüzünden oluşturup ayarlayabilirsiniz. İsteğe bağlı policies komutu, bu reponun hazır kurallarını `POLICY_PROJECTS` listesindeki projelere uygular; **Harbor'ın çalışması için zorunlu değildir**.

Hazır kuralları kullanmak için Dokploy'da `HARBOR_API_PASSWORD` değerini mevcut admin parolasıyla doldurun, yeniden deploy edin ve operations terminalinde veya Compose Job üzerinden çalıştırın:

~~~sh
python3 /app/operations.py policies
~~~

Varsayılan hedefler library, production ve development projeleridir. Yalnızca listedeki projeler yönetilir. Komutu tekrar çalıştırmak ilgili ayarları yeniden uygular; restart sırasında otomatik çalışmaz.

### 6. Docker erişimini doğrulayın

~~~sh
docker login harbor.example.com
~~~

Kendi projenize bir test image'ı gönderip geri çekin, digest'i doğrulayın ve tarama sonucunu inceleyin. CI/CD için proje kapsamlı, minimum yetkili ve süreli robot hesapları kullanın.

## Yapılandırma

| Değişken | Varsayılan / amaç |
|---|---|
| `HARBOR_INSTANCE_ID` | Zorunlu; kalıcı ve benzersiz volume öneki |
| `HARBOR_HOSTNAME` | Zorunlu; dış HTTPS adresi buradan üretilir |
| `HARBOR_ADMIN_PASSWORD` | İlk kurulum parolası; mevcut hesabın parolasını değiştirmez |
| `HARBOR_DB_PASSWORD` | DB başlangıç ve Harbor bağlantı parolası; mevcut DB'de SQL rotasyonu gerekir |
| `BACKUP_ENABLED` | true; otomatik yedekleme davranışını ve yedek gecikmesi kontrolünü yönetir |
| `DOKPLOY_NETWORK` | dokploy-network |
| `PROJECT_QUOTA_GIB` | 20 GiB; yeni proje varsayılan kotası ve politika komutunun uyguladığı kota |
| `POLICY_PROJECTS` | library,production,development |
| `RETENTION_KEEP_COUNT` / `RETENTION_KEEP_DAYS` | 20 / 30; geliştirme artifact'larının saklanması |
| `JOB_MAX_WORKERS` | 5 |
| `DB_MAX_OPEN_CONNS` / `POSTGRES_MAX_CONNECTIONS` | 100 / 256; core ve exporter havuzları için kapasite bırakılır |
| `ALLOW_PRIVATE_NETWORK_ACCESS` | false; webhook'ların özel ağ erişimini yönetir |
| `*_MEMORY_LIMIT` / `*_CPU_LIMIT` | Servis bazında kaynak limitleri |
| `HARBOR_HTTP_PROXY` / `HARBOR_HTTPS_PROXY` / `HARBOR_NO_PROXY` | Dış bağlantılar için proxy ayarları |

Tüm değişkenler ve kaynak varsayılanları [.env.example](.env.example) içindedir. Harbor sürümü tek bir ortam değişkeniyle değiştirilmez; sürüm kilidi ve güncelleme aracı birlikte kullanılır.

### Güvenlik ve politika varsayılanları

İlk açılıştan itibaren self-registration kapalıdır, yerel hesaplarla giriş yapılır ve proje oluşturma admin ile sınırlıdır. Bu ayarlar core yapılandırmasından yönetilir.

İsteğe bağlı politika paketi:

- Listedeki projeleri private yapar, push sonrası taramayı açar ve kritik zafiyetli image'ların çekilmesini engeller.
- Ayarlanan proje kotasını uygular.
- v* ve release-* tag'lerinin üzerine yazılmasını immutability kurallarıyla engeller.
- Son 20 dev-* artifact'ını **veya** son 30 günde çekilenleri korur; kurallar Harbor'ın OR mantığıyla birleşir. Diğer tag'ler ve untagged artifact'lar korunur.
- Retention işlemini her gün **22:00 UTC** saatine ayarlar. Başlangıçta image imzası zorunlu değildir.

Trivy, registry sertifikası doğrulaması ve zafiyet veritabanı güncellemeleri açık olarak gelir. Hazır paket yerine politikaları arayüzden elle yönetebilirsiniz. İlk library projesini private yapan adım init değil, politika komutudur.

## Yedekleme

### Şifreli S3 yedeklerini açma

~~~dotenv
BACKUP_ENABLED=true
RESTIC_REPOSITORY=s3:https://s3.example.com/backup-bucket/harbor-prod
RESTIC_PASSWORD=
AWS_ACCESS_KEY_ID=
AWS_SECRET_ACCESS_KEY=
AWS_DEFAULT_REGION=us-east-1
~~~

Erişim bilgilerini ve en az 20 karakterlik ayrı bir Restic şifreleme parolasını doldurun. Harbor sunucusundan bağımsız bir depo kullanın; şifreleme parolasını sunucu dışında da saklayın.

1. Ayarları kaydedip yeniden deploy edin.
2. Yalnızca **yeni** bir repository için operations Compose Job üzerinden bir kez çalıştırın:

   ~~~sh
   python3 /app/operations.py init-repository
   ~~~

3. [scripts/dokploy-job.sh](scripts/dokploy-job.sh) içeriğini Harbor sunucusundaki bir Dokploy Server Job'a kopyalayın. Doğru instance ID ve `ACTION=backup` değerini girin. Yerel Dokploy sunucusunda Dokploy Server Job, uzak sunucuda Server Job kullanın.
4. Günlük takvim oluşturun: örneğin Europe/Istanbul için **0 3 * * *** veya UTC için **0 0 * * ***. Dokploy'da bir sonraki çalışma zamanını doğrulayın.
5. İlk işi elle tetikleyip snapshot'ları kontrol edin:

   ~~~sh
   python3 /app/operations.py snapshots
   ~~~

Var olan Restic repository'sini yeniden initialize etmeyin. Varsayılan saklama politikası bu instance'a ait **7 günlük, 4 haftalık ve 6 aylık** Harbor yedeğini korur.

### Tutarlılık ve hizmet kesintisi

İş operasyon kilidi alır, yazan servisleri durdurur, mantıksal PostgreSQL dump'ı oluşturur, veri servislerini düzgün kapatır ve registry, PostgreSQL, cache durumu, job verileri, yapılandırma ile dahili anahtarları tek bir şifreli set olarak yedekler.

**Restic upload tamamlanana kadar Harbor kapalı kalır.** İlk tam yedek zaman alabilir. Servisler retention/prune işleminden önce yeniden başlatılır. Tekrar indirilebilen Trivy veritabanları ve raporları yedeğe dahil değildir. Restore staging için ayrıca yerel disk alanı gerekir.

Normal hatalarda servis kurtarma denenir. Zorla sonlandırılan iş kalıcı bakım kaydı bırakır; Server Job'u `ACTION=recover` ile çalıştırarak kayıtlı container'ları toparlayın. Kısa ömürlü operasyon yardımcıları Docker socket erişimi alır; sürekli çalışan Harbor ve operations container'larında socket mount edilmez.

### Yedeklemeyi kapatma

**``BACKUP_ENABLED=false``** yapıp yeniden deploy edin:

- Zamanlanmış ve manuel backup çağrıları disabled/skipped sonucu verir.
- Yedek kilidi alınmaz; S3 isteği, servis durdurma veya yedek temizliği yapılmaz.
- S3 bilgileri zorunlu değildir; yedek gecikmesi kontrolü atlanır.
- Mevcut yedekler korunur. Erişim bilgileri verildiğinde snapshot listeleme ve restore kullanılabilir.

Değişiklikten önce aktif yedeğin bitmesini bekleyin. Yeniden açmak için erişim bilgilerini girin, bayrağı true yapın, deploy edin ve başarılı bir yedek doğrulayın. Var olan repository'yi yeniden initialize etmeyin.

Günlük yedeklerde hedef **RPO ≤24 saat**, **RTO ≤4 saat** değeridir; gerçek veri boyutu ve restore provasıyla ölçülmelidir. Yedekleme kapalıyken bu hedefler uygulanmaz.

## Geri yükleme

Restore için **hedef veri ve yapılandırma volume'ları boş olmalıdır**. Mevcut veriler otomatik silinmez.

1. Yeni ve benzersiz instance ID ile ayrı bir Dokploy Compose uygulaması açın. **Snapshot'taki Harbor sürümünü** ve Compose Path olarak **./compose.restore.yaml** kullanın.
2. Hedef hostname'i ve yedek erişim bilgilerini girin. `BACKUP_ENABLED` false olabilir. Deploy edin; yalnızca recovery init ve operations açılır, Harbor workload'u başlamaz.
3. operations container'ında snapshot'ları listeleyip açık bir snapshot ID seçin.
4. Server Job şablonunu hedef instance ve `ACTION=restore` için düzenleyin. Son docker run komutunda action'dan sonra **`--snapshot SNAPSHOT_ID --confirm-target HEDEF_INSTANCE_ID`** ekleyin.
5. İşi çalıştırın. Snapshot, sürüm ve boş hedef kontrolünden sonra sahiplik ve izinler korunarak yükleme yapılır. Symlink içeren snapshot'lar reddedilir. Yarım kalan restore sonrasında yeni bir boş hedef kullanın.
6. Snapshot'ın **orijinal DB parolasını** girin, Compose Path'i **./compose.yaml** yapıp deploy edin. Init geri yüklenen anahtarları koruyarak hedef ayarlarını üretir.
7. İstemcileri taşımadan önce HTTPS yönlendirmesini, hesapları, yetkileri, projeleri ve artifact digest'lerini doğrulayın. Yeni instance için yedeklemeyi ayrıca ayarlayın.

## Operasyonlar

Komutları Dokploy Compose Job üzerinden operations container'ında çalıştırın:

| İşlem | Komut | Önerilen sıklık |
|---|---|---|
| İsteğe bağlı hazır politikalar | `python3 /app/operations.py policies` | Bilinçli olarak, ihtiyaç duyulduğunda |
| Garbage collection | `python3 /app/operations.py gc` | Haftalık, yedekten ayrı zamanda |
| İzleme | `python3 /app/operations.py monitor` | 15 dakikada bir |
| Yedek durumu | `python3 /app/operations.py check` | İhtiyaç halinde |

GC ve backup aynı kilidi paylaşır. Polling kesilirse kalıcı kayıt korunur; gc komutunu yeniden çalıştırmak aynı işin durumunu uzlaştırır. Harbor'da bu koordinasyonu atlayan ayrı bir otomatik GC takvimi oluşturmayın.

İzleme; disk kullanımını (%75 uyarı / %85 kritik), Harbor sağlığını, güvenilir HTTPS'i, sertifikanın 14 günden az süresinin kalmasını, eksik/eski Trivy verisini (48 saat) ve açıkken geciken yedeği (26 saat) kontrol eder. Planlı bakımda erişilebilirlik kontrolleri ertelenir; 4 saati aşan bakım bildirilir. Eşikler .env.example üzerinden ayarlanabilir.

JSON bildirimleri için `NOTIFICATION_WEBHOOK` ve isteğe bağlı `NOTIFICATION_BEARER_TOKEN` girin. Webhook HTTPS kullanmalıdır. İlk taramadan önce görülen Trivy veritabanı uyarısını bir image taraması tamamlayarak giderebilirsiniz.

Metrics özel ağda **proxy:9090/metrics** üzerinden sunulur. [Prometheus yapılandırmasını](monitoring/prometheus.yml) ve [alarm örneğini](monitoring/alerts.yml), Harbor ağına bağlı mevcut izleme servisinize uyarlayın. Loglar Docker'ın rotasyonlu local driver'ını kullanır ve Dokploy üzerinden okunur.

### Yapılandırma değişikliği ve güncelleme

Yedek/bildirim/operasyon ayarları Environment kaydedilip deploy edilerek değiştirilebilir; Harbor servis ayarları yeniden üretilmez. Bir operasyon kilidi tutuyorsa tamamlandıktan sonra deployment'ı tekrar deneyin.

Harbor yapılandırması, hostname, veritabanı, worker, dış bağlantı proxy'si, varsayılan kota veya sürüm değişikliğinde:

1. Geri dönüş için tutarlı bir yedek alın.
2. Server Job'u `ACTION=maintenance` ile çalıştırın. Harbor workload'u durur, operations açık kalır.
3. Ayarları veya Git revision'ını güncelleyip deploy edin. Init dahili anahtarları koruyarak yapılandırmayı yeniler.
4. Sağlık, login ve push/pull kontrollerini yapın. Süreç boyunca volume'ları koruyun.

Değişiklik yapılmamış bakım oturumunu iptal etmek için `ACTION=recover` kullanın. Yarım kalan prepare veya restore'u init/redeployment ile tamamlayın. DB/schema rollback, eski image'larla birlikte aynı yedek setindeki veritabanı, registry ve anahtarların da geri yüklenmesini gerektirebilir.

Admin parolasını Harbor'dan değiştirip `HARBOR_API_PASSWORD` değerini güncelleyin. `HARBOR_ADMIN_PASSWORD` sadece ilk kurulumda etkilidir. DB parolası rotasyonunda bakım sırasında PostgreSQL rol parolası ve `HARBOR_DB_PASSWORD` birlikte güncellenmelidir; yalnızca ortam değişkenini değiştirmek yeterli değildir.

## Geliştirme

~~~sh
# Image kilidini, şablon bütünlüğünü ve üretilen Compose yapısını kontrol et
node tools/check.mjs

# Dosyaları değiştirmeden güncel kararlı Harbor sürümünü kontrol et
node tools/update-images.mjs --check

# Harbor image'larını, başlangıç metadata'sını, şablonu ve Compose'u güncelle
node tools/update-images.mjs

# Linux Python 3.10+: standart kütüphane ile unit testler
python3 -m unittest discover -s tests -v

# Testleri Docker içinde çalıştır
sh tools/test.sh
~~~

Deployment öncesinde sürüm değişikliklerini ve upstream'in desteklediği upgrade yolunu inceleyin. Compose üreticisini düzenlediğinizde `node tools/render-compose.mjs` ile dosyaları yeniden üretin.

[GitHub Actions workflow'u](.github/workflows/validate.yml) yapısal kontrolleri, unit testleri ve Compose doğrulamasını tanımlar. Elle Workflow Dispatch çalıştırırken integration seçeneği açılırsa izole runner'da Docker smoke testi yapılır. Production öncesinde güvenilir TLS, gerçek push/pull, yetkiler, scanner davranışı, kaynak limitleri ve yedek açıksa S3 restore provası doğrulanmalıdır. Yapılan kontroller ve sınırlar için [doğrulama kaydına](VALIDATION.md) bakın.

## Kaynaklar ve atıf

- [Harbor kurulum ve yapılandırma](https://goharbor.io/docs/2.15.0/install-config/)
- [Harbor sürümleri](https://github.com/goharbor/harbor/releases)
- [Dokploy Docker Compose](https://docs.dokploy.com/docs/core/docker-compose)
- [Dokploy domain yönlendirmesi](https://docs.dokploy.com/docs/core/docker-compose/domains)
- [Dokploy zamanlanmış işler](https://docs.dokploy.com/docs/core/schedule-jobs)

Vendor olarak tutulan Harbor yapılandırma şablonunun upstream atıfları ve Apache 2.0 lisansı korunur. Ayrıntılar: [vendor/NOTICE.md](vendor/NOTICE.md) ve [vendor/HARBOR-LICENSE](vendor/HARBOR-LICENSE).
