# Muhtemel Aşk - Endonezce altyazı

Show TV'deki Muhtemel Aşk bölümlerini indirir, Türkçe konuşmayı Whisper ile yazıya döker, Claude Endonezceye çevirir. Sonuç `Muhtemel.Ask.S01Exx.ind.srt` olarak Google Drive'da videonun yanına gider, Apple TV'de Infuse otomatik yükler.

## Dosyalar
- `sistem/ma_sub.py`: bütün kod (indirme, Whisper, satırlara bölme, SRT build).
- `sistem/Muhtemel_Ask_Calistir.ipynb`: Colab notebook'u, tek hücre. `ma_sub.py`'yi Drive'dan import edip `ma_sub.run()` çağırır.
- `sistem/CEVIRI_TALIMATI.md`: Claude'un çeviri kuralları.

## Haftalık kullanım
1. Colab'da notebook'u aç, çalışma zamanı L4 GPU, hücreyi çalıştır (`BOLUM=0` sıradaki bölümü kendisi bulur).
2. Bitince `_is/Exx/HAZIR.txt` oluşur. Claude çevirir: `python ma_sub.py build tr_cues.json ceviri_tum.txt Muhtemel.Ask.S01Exx.ind.srt`
3. `.ind.srt` Drive'da `Sezon 1` klasörüne, videonun yanına konur.

## Çalışan kopya
Colab'ın kullandığı kopya Google Drive'da: `Muhtemel Ask/sistem/`. Bu repo yedek ve geçmiş içindir; kod değişince iki taraf da güncellenmeli.

## Ölçümler (14. bölüm, Colab L4)
- İlk 15 dk, Show TV resmi altyazısına karşı: kelime hatası %10,9, başlangıç zamanı medyan -18 ms, satırların %80'i ±200 ms içinde.
- Tam bölüm (2 sa 30 dk): indirme 387 sn, Whisper 653 sn, toplam 1071 sn.

Eski Codex/ChatGPT denemeleri (arşiv): github.com/ismailkorkmaz1905/ma-sub
