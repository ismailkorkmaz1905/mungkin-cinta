# Muhtemel Aşk - Endonezce çeviri talimatı (Claude için)

Bu dosya, Claude'un her hafta çeviri adımını nasıl yapacağını anlatır. Kullanıcı İsmail, izleyici kız arkadaşı (Endonezyalı). Amaç: TV'de Infuse ile izlenen, doğal, konuşma dilinde Endonezce altyazı.

## Klasörler (Google Drive, "Muhtemel Ask" klasörü)

- `Sezon 1/Muhtemel.Ask.S01Exx.mp4` : video (Colab indirir)
- `Sezon 1/Muhtemel.Ask.S01Exx.ind.srt` : SONUÇ, Claude yazar. Infuse bunu otomatik yükler.
- `_is/Exx/ceviri_girdi.txt` : çevrilecek satırlar
- `_is/Exx/tr_cues.json` : zamanlar (build için)
- `_is/Exx/HAZIR.txt` : Colab bitti işareti
- `_is/Exx/ceviri/parca_01.txt ...` : Claude'un çeviri parçaları (yarıda kalırsa kaldığı yerden devam)
- `sistem/ma_sub.py` : kod

## Adımlar

1. Drive'da `_is/Exx/HAZIR.txt` var mı bak. Yoksa Colab henüz bitmemiş, dur ve söyle.
2. `ceviri_girdi.txt`, `tr_cues.json` ve `sistem/ma_sub.py` dosyalarını indir.
3. `_is/Exx/ceviri/` altında hangi parçalar zaten var bak, olanları indir, eksikten devam et.
4. Satırları ~200'lük parçalar halinde çevir. Her parçayı bitirince `ceviri/parca_NN.txt` olarak Drive'a yaz (text/plain, dönüştürme kapalı).
5. Hepsi bitince yerelde birleştir ve çalıştır:
   `python ma_sub.py build tr_cues.json ceviri_tum.txt Muhtemel.Ask.S01Exx.ind.srt`
   Eksik/fazla satır varsa hata verir, düzelt. `too_fast` / `too_long` listesindeki satırları kısalt, tekrar build et.
6. `.ind.srt` dosyasını `Sezon 1` klasörüne yükle (text/plain, dönüştürme kapalı). Aynı isimde eski dosya varsa önce çöpe at.
7. Kullanıcıya tek cümleyle haber ver.

## Girdi formatı

```
0012 [1.9s|38] (?) Tövbe bismillah, bile yaklaşmayacak.
```
- `0012` satır numarası. Çıktıda aynen kullan.
- `[1.9s|38]` ekranda kalma süresi ve Endonezce için en fazla karakter (okuma hızı 20 kar/sn). Bu sınırı aşma, gerekirse anlamı koruyarak kısalt.
- `(?)` Whisper bu satırdan emin değil. Bağlama bakıp en makul Türkçeyi tahmin ederek çevir.
- `[Alo?]` gibi köşeli parantezli kelime: Whisper tam o kelimeden emin değil. Önce o kelimenin yerine bağlama uyan benzer sesli Türkçeyi düşün.
- Türkçe metin Whisper çıktısıdır, yazım/duyma hataları olabilir ("Evliya!" aslında "Bekle!" olabilir). Önceki/sonraki satırlara bakarak düzelt, ama uydurma.

## 14. bölüm testinde görülen Whisper hataları

Kör çeviride 218 satırın ~8'i yanlış duyulan Türkçe yüzünden hatalı çıktı. Bu kalıplara dikkat et:
- "Alo" (dramatik anda, telefon yoksa) -> "Allah'ım"
- "Umut ama", "Unutulmuş" -> "Unut onu", "Unut onuymuş" (Kadir'in Defne'ye sözü, bölüm boyunca tekrar ediyor)
- "Refne", "Etna Hanım" -> Defne, "Defne Hanım"
- "Emin da", "Emin Dağ", "Kadir Emin" -> Emindağ
- "Anamgül" -> "anamgil" (annemler)
- "barbağı" -> "barbar"
- "Bu çok uygun" (evlilik teklifi sahnesi) -> "Hukuka uygun"
Bir satır bağlama oturmuyorsa önce benzer sesli Türkçe ifadeyi düşün; hâlâ emin değilsen kısa ve nötr çevir, uydurma.

## Çıktı formatı

Her satır: numara, boşluk, Endonezce metin.
```
0012 Tobat, bismillah, dia nggak bakal mendekat.
```
- Satır sayısı ve numaralar girdiyle birebir aynı. Birleştirme, bölme, sıra değiştirme YOK. Zamanlar Python'da, dokunulmaz.
- İki satıra bölmek istersen `|` kullan: `0020 Kamu gila, ya?|Ngapain kamu ke sini?` Yoksa kod otomatik böler.
- Satır tamamen anlamsız Whisper çöpüyse (konuşma yok, müzik, "Altyazı M.K." gibi) sadece `-` yaz: `0031 -`. O satır altyazıdan çıkarılır.

## Üslup

- Doğal, konuşma dilinde Endonezce. Kitap dili, çeviri kokan cümle yok. Bir Endonezya dizisinin altyazısı gibi okunmalı.
- Günlük diyalogda `aku`, `kamu`, `nggak`, `udah`, `aja`, `banget`, `sih`, `kok`, `dong` uygun. Resmi/saygılı sahnelerde zorlama argo kullanma; gerektiğinde `Pak`, `Bu`, `Anda`.
- Duyguyu koru: romantizm, öfke, alay, komedi. Sansürleme, yumuşatma, açıklama ekleme.
- İsimleri çevirme. Sayı, tarih, para değerlerini değiştirme.
- Kısa ve net: altyazı okunacak, ekran süresi kısa.

## İsimler (doğru yazım)

Defne, Kadir, Tolga, Levent, Levent Bartıner, Bartıner, Mine, Melis, Özlem, Selim, Selma, Sultan, Zeynep, Zeyno, Suzi, Leyla, Oğuz, Yavuz, Zeliha, Emindağ

- `Emindağ` tek kelime (asla "Emin Dağ"). `Bartıner` bu şekilde (asla "Bartiner", "Bartınar").
- Whisper'ın isimleri yanlış duyduğu bilinen hâller (bağlam uygunsa düzelt): Def/Defter/Medefne -> Defne; Kader/Kadeh/Katiş -> Kadir; Dolgu -> Tolga; Levan/Levhat/Elifat -> Levent; Emine/Mina/Müniş/Nina -> Mine; Melisa/Menis -> Melis; Selam/Selvi -> Selim; Selman/Selva -> Selma; Bartner/partner/partnere/Atner -> Bartıner; Kadir Emin/Emin da -> Emindağ; Suzy -> Suzi. Dikkat: "selam", "partner", "kader" normal kelime de olabilir, sadece bağlam isim gerektiriyorsa düzelt.
- Abla / Abi / Hanım / Bey: isimle geliyorsa Endonezce karşılığı ya da isim yeterli (Tolga Bey -> Pak Tolga, Defne Hanım -> Bu Defne / Mbak Defne bağlama göre; Abi -> Kak / Mas).

## Dini ifadeler

| Türkçe | Endonezce |
|---|---|
| Allah aşkına | Demi Allah |
| Allah Allah / Allah'ım | Ya Allah |
| Ya Rabbim / Ya Rabbi | Ya Rabb |
| İnşallah | Insyaallah |
| Maşallah | Masyaallah |
| Estağfurullah | Astagfirullah |
| Tövbe estağfurullah | Tobat, astagfirullah |
| La havle vela kuvvete illa billah | La hawla wala quwwata illa billah |

Türkçede Allah geçiyorsa Endonezcede de koru. Sadece "Semoga" ile değiştirme; gerçek dua için "Semoga Allah ..." doğru.
