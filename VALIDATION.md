# Doğrulama kaydı

Hazırlanan Harbor sürümü: v2.15.4. Platform: Linux amd64.

Tamamlanan kontroller:

- GitHub resmi latest sürümü ile sürüm kilidi eşleşiyor.
- 11 Harbor image'ı ve 2 yardımcı image için manifest digest, OCI config digest ve başlangıç metadata'sı resmi Docker Hub registry'siyle karşılaştırıldı.
- Vendor Harbor şablonunun SHA-256 değeri doğrulandı.
- Compose üretimi ile checked-in compose.yaml ve compose.restore.yaml dosyaları eşleşiyor.
- İki Compose dosyası resmi Compose Specification JSON Schema 2020-12 ile doğrulandı.
- YAML dosyaları parse edildi; Python kaynak ve test dosyaları Lezer Python parser ile sözdizimi açısından denetlendi.
- Host portu, privileged container, kalıcı Docker socket, runtime env_file ve yanlış Traefik ağı bağlantıları için yapısal kontroller geçti.

Çalıştırılamayan kontroller:

- 32 Python unit testinin yürütülmesi.
- Gerçek Docker build, Compose config ve boş volume kurulum smoke testi.
- Canlı TLS/domain, push/pull, tarama politikası ve S3 backup/restore provası.

Bu ortamda yerel süreç başlatma işletim sistemi tarafından engellendi. Python WebAssembly alternatifi de ortamın WebAssembly çalıştırma kısıtına takıldı. Sözdizimi kontrolleri, runtime unit testlerinin yerine geçmez.

.github/workflows/validate.yml unit kontrollerini çalıştırır. Workflow Dispatch içindeki integration seçeneği izole Docker smoke testini açar. Üretime açmadan önce README'deki canlı kabul adımlarını staging'de tamamlayın.
