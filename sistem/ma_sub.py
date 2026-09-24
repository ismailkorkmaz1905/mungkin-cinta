"""Muhtemel Ask - Endonezce altyazi sistemi.

Colab (GPU):  run(bolum)  -> video Drive'a iner, Turkce altyazi cikar (Show TV VTT veya Whisper)
Claude:       ceviri + build() -> Muhtemel.Ask.S01Exx.ind.srt videonun yanina yazilir

Komut satiri:
  python ma_sub.py asr <audio.wav> <words.json>              (Colab icinde, run() cagirir)
  python ma_sub.py build <tr_cues.json> <ceviri.txt> <out.srt> (Claude kullanir)
"""
from __future__ import annotations

import html
import json
import os
import re
import shutil
import subprocess
import sys
import time
import urllib.request
from html.parser import HTMLParser
from pathlib import Path
from urllib.parse import urljoin, urlparse

ROOT = Path('/content/drive/MyDrive/Muhtemel Ask')
SEASON_DIR = 'Sezon 1'
WORK_DIR = '_is'
LOCAL = Path('/content/ma_tmp')
SHOW_INDEX = 'https://www.showtv.com.tr/dizi/tanitim/muhtemel-ask/3072'
UA = 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/128.0 Safari/537.36'

# Altyazi kurallari (eski projenin SUBTITLE_SPEC degerleri)
MAX_LINE = 42          # satir basina karakter
MAX_CHARS = 84         # 2 satir
MAX_MS = 7000          # bir altyazinin en uzun suresi
MIN_MS = 1000          # en kisa sure (yer varsa uzatilir)
START_DELAY_MS = 200   # Whisper kelime baslangiclari ~200 ms erken; 14. bolumde Show TV ile olculdu
TAIL_MS = 350          # konusma bitince bu kadar daha ekranda kalir (Show TV ~300-400)
GAP_MS = 80            # iki altyazi arasi minimum bosluk
PAUSE_SPLIT_MS = 700   # bu kadar sessizlik varsa yeni altyazi
MAX_CPS = 20           # okuma hizi (karakter/saniye)
CHECKPOINT_SN = 180    # Whisper bu siklikla Drive'a ara kayit yapar

os.environ['TZ'] = 'Asia/Singapore'  # loglar Singapur saatiyle
time.tzset()

NAMES = ['Defne', 'Kadir', 'Tolga', 'Levent', 'Bartıner', 'Mine', 'Melis', 'Özlem',
         'Selim', 'Selma', 'Sultan', 'Zeynep', 'Oğuz', 'Yavuz', 'Zeliha', 'Emindağ']
# Whisper'a isim ipucu. 24 Eylul A/B'de Demucs+hotwords birlikte daha kotu cikti; kanitlanmis ayar bu.
PROMPT = ', '.join(NAMES[:8]) + '.'
UNSURE_P = 0.5         # Whisper'in bu olasiligin altindaki kelimeleri cevirmene [kelime?] diye isaretlenir
HALLUCINATION = re.compile(
    r'altyaz[ıi]|izlediğiniz için|izlediginiz icin|abone ol|kanalıma|kanalima|www\.|\.com|'
    r'bir sonraki videoda|M\s*\.\s*K\s*\.', re.I)


def log(*a):
    """Ekrana ve (MA_LOG ayarliysa) Drive'daki log.txt'ye yazar. Log yazilamazsa is durmaz."""
    line = ' '.join([time.strftime('%Y-%m-%d %H:%M:%S')] + [str(x) for x in a])
    print(line, flush=True)
    path = os.environ.get('MA_LOG')
    if path:
        try:
            with open(path, 'a', encoding='utf-8') as f:
                f.write(line + '\n')
        except OSError:
            pass


def retry(fn, what: str, tries: int = 5):
    """Drive/ag islemlerini tekrar dener (Drive FUSE bazen gecici hata verir)."""
    for n in range(1, tries + 1):
        try:
            return fn()
        except Exception as exc:
            if n == tries:
                raise
            log(f'{what} hatasi ({n}/{tries}), {10 * n} sn sonra tekrar: {exc}')
            time.sleep(10 * n)


def write_text(path: Path, text: str):
    """Drive'a guvenli yazma: once gecici dosya, sonra yer degistir, tekrar oku ve dogrula."""
    data = text.encode('utf-8')

    def go():   # bayt bayt yaz ve kontrol et (read_text \r\n'yi \n'ye cevirip yanlis alarm veriyordu)
        tmp = Path(str(path) + '.tmp')
        tmp.write_bytes(data)
        os.replace(tmp, path)
        if Path(path).read_bytes() != data:
            raise IOError(f'{path} dogrulanamadi')
    retry(go, f'{Path(path).name} yazma')


def copy_verified(src: Path, dst: Path):
    """Buyuk dosyayi Drive'a kopyalar, boyutu dogrular, yarim dosya birakmaz."""
    def go():
        part = Path(str(dst) + '.part')
        shutil.copyfile(src, part)
        if part.stat().st_size != Path(src).stat().st_size:
            raise IOError('boyut tutmuyor')
        os.replace(part, dst)
    retry(go, f'{Path(dst).name} kopyalama')


def ep_name(ep: int) -> str:
    return f'Muhtemel.Ask.S01E{ep:02d}'


def paths(ep: int, root: Path = None, test: bool = False):
    root = Path(root or ROOT)
    season = root / ('TEST' if test else SEASON_DIR)
    work = root / WORK_DIR / f'{"TEST_" if test else ""}E{ep:02d}'
    return dict(video=season / f'{ep_name(ep)}.mp4', srt=season / f'{ep_name(ep)}.ind.srt', work=work)


def next_episode(root: Path = None) -> int:
    season = Path(root or ROOT) / SEASON_DIR
    done = [int(m.group(1)) for p in season.glob('*.ind.srt') if (m := re.search(r'S01E(\d+)', p.name))]
    return max(done) + 1 if done else 15


# ---------------------------------------------------------------- Show TV

class _Page(HTMLParser):
    def __init__(self, text):
        super().__init__(convert_charrefs=True)
        self.links, self.players, self.ld = [], [], []
        self._in_ld, self._buf = False, ''
        self.feed(text)

    def handle_starttag(self, tag, attrs):
        a = dict(attrs)
        if tag == 'a' and a.get('href'):
            self.links.append(a['href'])
        if a.get('data-hope-video'):
            try:
                self.players.append(json.loads(a['data-hope-video']))
            except ValueError:
                pass
        if tag == 'script' and a.get('type') == 'application/ld+json':
            self._in_ld, self._buf = True, ''

    def handle_data(self, data):
        if self._in_ld:
            self._buf += data

    def handle_endtag(self, tag):
        if tag == 'script' and self._in_ld:
            self._in_ld = False
            try:
                v = json.loads(self._buf)
                self.ld.extend(v if isinstance(v, list) else [v])
            except ValueError:
                pass


def _get(url, limit=8_000_000) -> bytes:
    req = urllib.request.Request(url, headers={'User-Agent': UA, 'Referer': 'https://www.showtv.com.tr/'})
    with urllib.request.urlopen(req, timeout=30) as r:
        return r.read(limit)


def showtv(ep: int, page_url: str = '') -> dict | None:
    """Show TV bolum sayfasindan MP4 ve (varsa) Turkce VTT linkini bulur."""
    if not page_url:
        index = _Page(_get(SHOW_INDEX).decode('utf-8', 'replace'))
        pat = re.compile(rf'/dizi/tum_bolumler/muhtemel-ask-sezon-\d+-bolum-{ep}-izle/\d+$')
        found = sorted({urljoin(SHOW_INDEX, h) for h in index.links if pat.search(h)})
        if not found:
            return None
        page_url = found[0]
    page = _Page(_get(page_url).decode('utf-8', 'replace'))
    mp4 = next((j.get('contentUrl') for j in page.ld
                if isinstance(j, dict) and j.get('@type') == 'VideoObject' and j.get('contentUrl')), None)
    vtt = None
    for p in page.players:
        for s in p.get('subtitles') or []:
            if s.get('srclang') == 'tr' and s.get('src'):
                vtt = urljoin(page_url, s['src'])
    return dict(page_url=page_url, mp4=mp4, vtt=vtt)


def _ms(stamp: str) -> int:
    parts = stamp.replace(',', '.').split(':')
    if len(parts) == 2:
        parts.insert(0, '0')
    h, m = int(parts[0]), int(parts[1])
    s, ms = parts[2].split('.')
    return ((h * 60 + m) * 60 + int(s)) * 1000 + int(ms.ljust(3, '0')[:3])


def parse_vtt(text: str) -> list[dict]:
    text = text.replace('﻿', '').replace('\r\n', '\n').replace('\r', '\n')
    cues = []
    for block in re.split(r'\n\s*\n', text):
        lines = block.strip().split('\n')
        idx = next((i for i, l in enumerate(lines) if '-->' in l), None)
        if idx is None:
            continue
        a, b = re.split(r'\s+-->\s+', lines[idx].strip())[:2]
        body = ' '.join(l.strip() for l in lines[idx + 1:] if l.strip())
        body = html.unescape(re.sub(r'<[^>]+>', '', body)).strip()
        if body:
            cues.append(dict(s=_ms(a), e=_ms(b.split()[0]), tr=body))
    return cues


# ---------------------------------------------------------------- indirme

def download(url: str, dest: Path):
    """Kesilirse kaldigi yerden devam eden basit indirme."""
    dest.parent.mkdir(parents=True, exist_ok=True)
    part = dest.with_suffix(dest.suffix + '.part')
    for attempt in range(1, 6):
        have = part.stat().st_size if part.exists() else 0
        headers = {'User-Agent': UA, 'Referer': 'https://www.showtv.com.tr/'}
        if have:
            headers['Range'] = f'bytes={have}-'
        try:
            with urllib.request.urlopen(urllib.request.Request(url, headers=headers), timeout=60) as r:
                total = have + int(r.headers.get('Content-Length', 0))
                if have and r.status != 206:
                    have = 0
                    part.unlink()
                with part.open('ab') as f:
                    last = time.time()
                    while chunk := r.read(8 << 20):
                        f.write(chunk)
                        have += len(chunk)
                        if time.time() - last > 20:
                            log(f'indiriliyor {have / 1e9:.2f} / {total / 1e9:.2f} GB')
                            last = time.time()
            if total and have < total:
                raise IOError(f'eksik indi: {have} / {total}')
            part.rename(dest)
            return
        except Exception as exc:  # ag hatasi -> tekrar dene
            log(f'indirme hatasi ({attempt}/5): {exc}')
            time.sleep(10 * attempt)
    raise RuntimeError('Video indirilemedi')


def _duration(path: Path) -> float:
    """Video suresi (sn); okunamazsa 0."""
    try:
        out = subprocess.run(['ffprobe', '-v', 'error', '-show_entries', 'format=duration', '-of', 'csv=p=0',
                              str(path)], capture_output=True, text=True, timeout=60).stdout
        return float(out.strip() or 0)
    except Exception:
        return 0.0


def fetch_clip(url: str, dest: Path, minutes: int):
    """Sadece ilk N dakikayi indirir (test icin). Olmazsa tamamini indirip keser."""
    dest.parent.mkdir(parents=True, exist_ok=True)
    head = f'Referer: https://www.showtv.com.tr/\r\nUser-Agent: {UA}\r\n'
    cmd = ['ffmpeg', '-v', 'error', '-y', '-headers', head, '-t', str(minutes * 60), '-i', url,
           '-map', '0:v:0', '-map', '0:a:0', '-c', 'copy', '-movflags', '+faststart', str(dest)]
    if subprocess.run(cmd).returncode != 0 or _duration(dest) < minutes * 60 * 0.95:
        log('Parca indirme olmadi, tamami indirilip kesilecek')
        full = dest.with_name('tam_' + dest.name)
        fetch_video(url, full)
        subprocess.run(['ffmpeg', '-v', 'error', '-y', '-t', str(minutes * 60), '-i', str(full),
                        '-c', 'copy', '-movflags', '+faststart', str(dest)], check=True)
        full.unlink()


def fetch_video(url: str, dest: Path):
    host = urlparse(url).hostname or ''
    if url.lower().split('?')[0].endswith('.mp4') or 'vmcdn' in host:
        download(url, dest)
    else:  # YouTube vb.
        subprocess.run([sys.executable, '-m', 'pip', 'install', '-q', '-U', 'yt-dlp'], check=True)
        subprocess.run([sys.executable, '-m', 'yt_dlp', '--no-playlist', '-f', 'bv*[height<=1080]+ba/b',
                        '--merge-output-format', 'mp4', '-o', str(dest), url], check=True)


# ---------------------------------------------------------------- vokal ayirma (Demucs)

def separate_vocals(src_video: Path, out_wav: Path, chunk_sn: int = 300):
    """Muzigi atip sadece konusmayi birakir (htdemucs). 5 dakikalik parcalarla calisir, RAM sismez.
    Hata olursa istisna firlatir; cagiran taraf normal sesle devam eder."""
    import numpy as np
    import torch
    from demucs.apply import apply_model
    from demucs.pretrained import get_model

    device = 'cuda' if torch.cuda.is_available() else 'cpu'
    model = get_model('htdemucs')
    model.to(device).eval()
    vocal_idx = model.sources.index('vocals')
    total = _duration(src_video)
    raw = out_wav.with_suffix('.f32')
    started = time.time()
    with raw.open('wb') as out:
        pos = 0.0
        while pos < total:
            pcm = subprocess.run(['ffmpeg', '-v', 'error', '-ss', str(pos), '-t', str(chunk_sn), '-i', str(src_video),
                                  '-vn', '-ac', '2', '-ar', '44100', '-f', 'f32le', '-'],
                                 capture_output=True, check=True).stdout
            audio = np.frombuffer(pcm, dtype='<f4').reshape(-1, 2).T.copy()
            if audio.shape[1] == 0:
                break
            wav = torch.from_numpy(audio)
            ref = wav.mean(0)
            mean, std = ref.mean(), ref.std() + 1e-8
            with torch.no_grad():
                src = apply_model(model, ((wav - mean) / std)[None], device=device, split=True,
                                  overlap=0.25, progress=False)[0]
            vocals = (src[vocal_idx] * std + mean).cpu().numpy().T.astype('<f4')
            out.write(vocals.tobytes())
            pos += chunk_sn
            log(f'Vokal ayirma {min(pos, total) / 60:.0f}/{total / 60:.0f} dk, {time.time() - started:.0f} sn')
    del model                      # GPU bellegini Whisper'a birak
    if device == 'cuda':
        torch.cuda.empty_cache()
    subprocess.run(['ffmpeg', '-v', 'error', '-y', '-f', 'f32le', '-ar', '44100', '-ac', '2', '-i', str(raw),
                    '-ac', '1', '-ar', '16000', '-c:a', 'pcm_s16le', str(out_wav)], check=True)
    raw.unlink()


# ---------------------------------------------------------------- ASR (Whisper)

def _gpu_env():
    env = dict(os.environ)
    libs = []
    for mod in ('nvidia.cublas.lib', 'nvidia.cudnn.lib'):
        try:
            m = __import__(mod, fromlist=['_'])
            libs.append(list(m.__path__)[0])
        except Exception:
            pass
    env['LD_LIBRARY_PATH'] = ':'.join(libs + [env.get('LD_LIBRARY_PATH', '')])
    return env


def asr_worker(wav: str, out: str, model_name: str = 'large-v3'):
    """VAD ile konusma bolgelerini bulur, her bolgeyi ayri cozer (kelime zamanlari dogru cikar).
    Her ~3 dakikada bir <out>.partial dosyasina kaydeder; kesilirse kaldigi pencereden devam eder."""
    import wave
    import numpy as np
    from faster_whisper import WhisperModel
    from faster_whisper.vad import VadOptions, get_speech_timestamps

    with wave.open(wav, 'rb') as w:
        audio = np.frombuffer(w.readframes(w.getnframes()), dtype='<i2').astype(np.float32) / 32768
    speech = get_speech_timestamps(audio, vad_options=VadOptions(
        threshold=0.25, min_speech_duration_ms=150, min_silence_duration_ms=300,
        speech_pad_ms=0, max_speech_duration_s=25), sampling_rate=16000)
    pad = 500 * 16  # VAD konusma baslangicini ~200 ms gec buluyor; pencere genis tutulur
    windows = []  # [baslangic, bitis, ilk_konusma, son_konusma] (ornek sayisi)
    for sp in speech:
        a, b = max(0, sp['start'] - pad), min(len(audio), sp['end'] + pad)
        if windows and a <= windows[-1][1]:
            if b - windows[-1][0] <= 20 * 16000:        # yakin konusmalar tek pencerede (en fazla 20 sn)
                windows[-1][1], windows[-1][3] = b, sp['end']
                continue
            mid = (windows[-1][3] + sp['start']) // 2   # ust uste binmesin, sessizligin ortasindan bol
            windows[-1][1], a = mid, mid
        windows.append([a, b, sp['start'], sp['end']])
    talk = sum(sp['end'] - sp['start'] for sp in speech) / 16000 / 60
    log(f'VAD: {len(windows)} pencere, {talk:.1f} dk konusma')

    try:
        model = WhisperModel(model_name, device='cuda', compute_type='float16')
    except Exception as exc:
        if not re.search(r'cuda|cudnn|cublas|gpu|device', str(exc), re.I):
            raise   # model indirilemedi vb.: CPU'ya dusmek cozmez
        log('GPU kullanilamadi, CPU ile devam (cok yavas olur):', exc)
        model = WhisperModel(model_name, device='cpu', compute_type='int8')
    prompt = PROMPT
    words, dropped, started = [], [], time.time()
    partial, done, saved_at = Path(str(out) + '.partial'), 0, time.time()
    if partial.exists():
        try:
            ck = json.loads(partial.read_text(encoding='utf-8'))
            if ck.get('windows') == len(windows):
                words, dropped, done = ck['words'], ck['dropped'], ck['done']
                log(f'Kaldigi yerden devam: {done}/{len(windows)} pencere zaten bitmis')
        except (ValueError, KeyError, OSError):
            log('Yarim kayit okunamadi, bastan basliyor')
    for n, (a, b, s0, s1) in enumerate(windows, 1):
        if n <= done:
            continue
        segs, _ = model.transcribe(audio[a:b], language='tr', beam_size=5, temperature=0,
                                   word_timestamps=True, condition_on_previous_text=False,
                                   vad_filter=False, initial_prompt=prompt)
        for seg in segs:
            text = seg.text.strip()
            norm = re.sub(r'\W+', '', text.lower())
            echo = len(norm) >= 12 and norm in re.sub(r'\W+', '', prompt.lower())   # prompt'u tekrar ediyorsa
            if (not text or HALLUCINATION.search(text) or echo
                    or (seg.no_speech_prob > 0.6 and seg.avg_logprob < -1.0)):
                dropped.append(dict(t=round(a / 16 + seg.start * 1000), text=text))
                continue
            for w in seg.words or []:
                st, en = round(a / 16 + w.start * 1000), round(a / 16 + w.end * 1000)
                st = max(st, round(a / 16))                 # pencere disina tasmaz
                en = max(en, st + 60)
                if en - st > 1500:                          # tek kelime 1.5 sn'den uzun olmaz
                    st = en - min(1500, 90 * len(w.word.strip()) + 250)
                words.append(dict(w=w.word, s=st, e=en, p=round(w.probability, 3)))  # bastaki bosluk korunur
        if n % 100 == 0:
            log(f'ASR {n}/{len(windows)} pencere, {time.time() - started:.0f} sn')
        if time.time() - saved_at > CHECKPOINT_SN and n < len(windows):   # checkpoint
            write_text(partial, json.dumps(dict(windows=len(windows), done=n, words=words, dropped=dropped),
                                           ensure_ascii=False))
            saved_at = time.time()
            log(f'Ara kayit: {n}/{len(windows)} pencere Drive\'a yazildi')
    words.sort(key=lambda x: x['s'])
    write_text(Path(out), json.dumps(dict(words=words, dropped=dropped), ensure_ascii=False))
    if partial.exists():
        partial.unlink()
    log(f'ASR bitti: {len(words)} kelime, {len(dropped)} supheli parca atildi')


# ---------------------------------------------------------------- segmentasyon

_END = re.compile(r'[.!?…]["\')]*$')


def segment(words: list[dict]) -> list[dict]:
    """Kelime zamanlarindan altyazi bloklari kurar."""
    groups, cur = [], []

    def text(ws):  # Whisper kelimeleri bastaki boslukla gelir; "Allah" + "'ım" -> "Allah'ım"
        return re.sub(r'\s+', ' ', ''.join(x['w'] for x in ws)).strip()

    def marked(ws):  # cevirmen icin: emin olunmayan kelime [kelime?]
        out = ''.join((re.sub(r'^(\s*)(.*?)(\s*)$', r'\1[\2?]\3', x['w']) if x.get('p', 1) < UNSURE_P
                       else x['w']) for x in ws)
        return re.sub(r'\s+', ' ', out).strip()

    for w in words:
        if cur:
            gap = w['s'] - cur[-1]['e']
            length = len(text(cur + [w]))
            dur = w['e'] - cur[0]['s']
            ended = bool(_END.search(cur[-1]['w'].strip()))
            comma = cur[-1]['w'].strip().endswith(',')
            split = (gap >= PAUSE_SPLIT_MS or length > MAX_CHARS or dur > MAX_MS
                     or (ended and (gap >= 250 or len(text(cur)) >= 30 or length > MAX_LINE))
                     or (comma and len(text(cur)) >= 50 and gap >= 150))
            if split:
                groups.append(cur)
                cur = []
        cur.append(w)
    if cur:
        groups.append(cur)

    cues = []
    for g in groups:
        low = sum(1 for x in g if x.get('p', 1) < 0.4) >= max(1, len(g) // 3)
        start = min(g[0]['s'] + START_DELAY_MS, (g[0]['s'] + g[0]['e']) // 2)
        cues.append(dict(s=start, e=g[-1]['e'] + TAIL_MS, speech_e=g[-1]['e'], tr=text(g), trm=marked(g), low=low))
    return fix_timing(cues)


def fix_timing(cues: list[dict]) -> list[dict]:
    """Baslangic hic geciktirilmez (konusmayla ayni anda cikar). Bitis: konusma bitene kadar
    kesin, sonrasinda TAIL_MS kadar kalir ama bir sonraki altyaziya GAP_MS'den fazla yaklasmaz."""
    cues = sorted(cues, key=lambda c: c['s'])
    for i, c in enumerate(cues):
        c['s'] = max(0, c['s'])
        speech_e = c.pop('speech_e', c['e'])
        nxt = cues[i + 1] if i + 1 < len(cues) else None
        if nxt is None:
            c['e'] = max(c['e'], c['s'] + MIN_MS)
        else:
            limit = nxt['s'] - GAP_MS
            c['e'] = min(c['e'], max(limit, speech_e))
            if c['e'] - c['s'] < MIN_MS:
                c['e'] = max(c['e'], min(c['s'] + MIN_MS, limit))
            if c['e'] >= nxt['s']:                      # konusmalar ust uste: cakisma olmasin
                c['e'] = nxt['s'] - 1
            if c['e'] - c['s'] < 300:                   # asiri kisa kalmasin, sonrakini biraz kaydir
                c['e'] = c['s'] + 300
                nxt['s'] = max(nxt['s'], c['e'] + 1)
        c['i'] = i + 1
    return cues


# ---------------------------------------------------------------- SRT

def _clock(ms: int) -> str:
    ms = max(0, int(ms))
    return f'{ms // 3600000:02d}:{ms // 60000 % 60:02d}:{ms // 1000 % 60:02d},{ms % 1000:03d}'


def wrap(text: str) -> str:
    """Iki satira dengeli boler. '|' varsa oradan boler."""
    text = re.sub(r'\s+', ' ', text).strip()
    if '|' in text:
        return '\n'.join(p.strip() for p in text.split('|') if p.strip())
    if len(text) <= MAX_LINE:
        return text
    words = text.split(' ')
    best, best_score = text, None
    for k in range(1, len(words)):
        a, b = ' '.join(words[:k]), ' '.join(words[k:])
        score = max(len(a), len(b)) * 10 + abs(len(a) - len(b))
        if a.endswith((',', '.', '?', '!')):
            score -= 60
        if best_score is None or score < best_score:
            best, best_score = a + '\n' + b, score
    return best


def write_srt(cues: list[dict], key: str, path: Path):
    out = [f"{n}\n{_clock(c['s'])} --> {_clock(c['e'])}\n{wrap(c[key])}\n" for n, c in enumerate(cues, 1)]
    Path(path).write_text('\n'.join(out), encoding='utf-8')


def translation_input(cues: list[dict]) -> str:
    """Claude'a verilecek metin: 0001 [1.2s|24] (?) Turkce"""
    rows = []
    for c in cues:
        sec = (c['e'] - c['s']) / 1000
        budget = min(MAX_CHARS, max(12, int(sec * MAX_CPS)))
        rows.append(f"{c['i']:04d} [{sec:.1f}s|{budget}]{' (?)' if c.get('low') else ''} {c.get('trm') or c['tr']}")
    return '\n'.join(rows) + '\n'


def build(tr_cues_path: str, translation_path: str, out_path: str) -> dict:
    """Ceviri dosyasini (0001 metin) Turkce zamanlarla birlestirip SRT yazar."""
    data = json.loads(Path(tr_cues_path).read_text(encoding='utf-8'))
    cues = data['cues']
    got = {}
    for line in Path(translation_path).read_text(encoding='utf-8').splitlines():
        m = re.match(r'^\s*(\d{1,5})\s+(.*\S)\s*$', line)
        if m:
            got[int(m.group(1))] = m.group(2)
    missing = [c['i'] for c in cues if c['i'] not in got]
    if missing:
        raise ValueError(f'{len(missing)} satir eksik, ilk: {missing[:10]}')
    extra = sorted(set(got) - {c['i'] for c in cues})
    if extra:
        raise ValueError(f'Fazla satir numarasi: {extra[:10]}')
    keep, fast, long_ = [], [], []
    for c in cues:
        t = got[c['i']].strip()
        if t in ('-', '[-]'):          # cevirmen "burada konusma yok" dedi
            continue
        c = dict(c, id=t)
        plain = t.replace('|', ' ')
        if len(plain) / max(0.3, (c['e'] - c['s']) / 1000) > MAX_CPS + 5:
            fast.append(c['i'])
        if any(len(l) > MAX_LINE + 6 for l in wrap(t).split('\n')) or wrap(t).count('\n') > 1:
            long_.append(c['i'])
        keep.append(c)
    write_srt(keep, 'id', Path(out_path))
    report = dict(cues=len(keep), removed=len(cues) - len(keep), too_fast=fast, too_long=long_)
    print(json.dumps(report, ensure_ascii=False))
    return report


# ---------------------------------------------------------------- ana akis (Colab)

def run(ep: int = 0, url: str = '', force_asr: bool = True, root: Path = None, test: bool = False,
        minutes: int = 0, vocals: bool = False):
    """force_asr=True: Show TV altyazisi olsa bile Whisper kullanilir (varsayilan).
    test=True: gercek akisin aynisi ama video 'TEST' klasorune, is dosyalari _is/TEST_Exx'e gider;
               Sezon 1'e ve HAZIR.txt'ye dokunmaz (zamanlanmis ceviri tetiklenmez).
    minutes>0: sadece ilk N dakika (hizli deneme).
    vocals=True: Whisper'dan once muzik ayiklanir (Demucs). 24 Eylul A/B'de kotu cikti, varsayilan kapali."""
    root = Path(root or ROOT)
    ep = ep or next_episode(root)
    test = test or bool(minutes)   # kisa deneme asla Sezon 1'e yazmaz, otomatik ceviriyi tetiklemez
    p = paths(ep, root, test)
    p['work'].mkdir(parents=True, exist_ok=True)
    p['video'].parent.mkdir(parents=True, exist_ok=True)
    os.environ['MA_LOG'] = str(p['work'] / 'log.txt')   # alt surecler (Whisper) de buraya yazar
    t0, times = time.time(), {}
    log(f'=== Bolum {ep}{" (TEST)" if test else ""}{f" ilk {minutes} dk" if minutes else ""} | Whisper zorla: {force_asr}'
        f' | Vokal ayirma: {vocals} ===')

    # 1) kaynak
    info = {}
    if not url or 'showtv.com.tr' in url:
        try:
            info = showtv(ep, url if url and 'showtv.com.tr' in url else '') or {}
        except Exception as exc:
            log('Show TV sayfasi okunamadi:', exc)
        if info:
            log('Show TV:', info.get('page_url'), '| TR altyazi:', 'VAR' if info.get('vtt') else 'yok (Whisper kullanilacak)')
    src_url = url if url and 'showtv.com.tr' not in url else info.get('mp4')
    write_text(p['work'] / 'kaynak.json', json.dumps(dict(episode=ep, url=src_url, **info), ensure_ascii=False, indent=1))

    local_video = LOCAL / p['video'].name
    if p['video'].exists():  # .part ile yazildigi icin varsa tamdir
        log('Video zaten Drive\'da:', p['video'].name)
        local_video = p['video']
    elif not local_video.exists():
        if not src_url:
            raise SystemExit(f'Bolum {ep} Show TV\'de henuz yok. Yayinlaninca tekrar calistir '
                             f'veya URL alanina bolum linkini yapistir.')
        log('Video indiriliyor:', src_url)
        if minutes:
            fetch_clip(src_url, local_video, minutes)
        else:
            fetch_video(src_url, local_video)
    if local_video != p['video']:   # checkpoint: video hemen Drive'a, oturum kapansa da tekrar inmez
        log(f'Video Drive\'a kopyalaniyor ({local_video.stat().st_size / 1e9:.2f} GB)')
        copy_verified(local_video, p['video'])
        log('Video Drive\'da:', p['video'])
    times['indirme_sn'] = round(time.time() - t0)

    # 2) Turkce altyazi
    tr_json = p['work'] / 'tr_cues.json'
    cues, source = None, None
    if info.get('vtt') and force_asr:  # karsilastirma icin sakla, kullanma
        try:
            write_text(p['work'] / 'showtv_tr.vtt', _get(info['vtt'], 20_000_000).decode('utf-8', 'replace'))
        except Exception:
            pass
    if info.get('vtt') and not force_asr and not minutes:
        try:
            cues = parse_vtt(_get(info['vtt'], 20_000_000).decode('utf-8', 'replace'))
            for n, c in enumerate(cues, 1):
                c['i'], c['low'] = n, False
            source = 'showtv_vtt'
            log(f'Show TV Turkce altyazi alindi: {len(cues)} satir')
        except Exception as exc:
            log('VTT okunamadi, Whisper kullanilacak:', exc)
    if cues is None:
        src = local_video if local_video.exists() else p['video']
        wav = LOCAL / (p['video'].stem + ('_test' if test else '') + '.wav')
        wav.parent.mkdir(parents=True, exist_ok=True)
        if not wav.exists():
            log('Ses cikariliyor')
            subprocess.run(['ffmpeg', '-v', 'error', '-y', '-i', str(src), '-vn', '-ac', '1', '-ar', '16000',
                            '-c:a', 'pcm_s16le', str(wav)], check=True)
        used_vocals = False
        if vocals:
            vwav = p['work'] / ('vokal_16k' + (f'_{minutes}dk' if minutes else '') + '.wav')   # checkpoint (Drive)
            if not vwav.exists():
                try:
                    log('Vokal ayirma (Demucs) basliyor')
                    tmp = LOCAL / 'vokal_16k.wav'
                    separate_vocals(src, tmp)
                    copy_verified(tmp, vwav)
                    log('Vokal ayirma bitti')
                except Exception as exc:
                    log(f'Vokal ayirma olmadi, normal sesle devam ({type(exc).__name__}: {exc})')
            if vwav.exists():
                wav, used_vocals = vwav, True
        times['vokal_sn'] = round(time.time() - t0) - times['indirme_sn']
        words = p['work'] / ('words' + (f'_{minutes}dk' if minutes else '') + ('_vokal' if used_vocals else '') + '.json')
        if not words.exists():
            log('Whisper large-v3 calisiyor (L4\'te ~15-25 dk)')
            env = _gpu_env()
            env.pop('MA_LOG', None)   # alt surecin ciktisini burada kendimiz log'a yaziyoruz
            proc = subprocess.Popen([sys.executable, '-u', str(Path(__file__).resolve()), 'asr', str(wav), str(words)],
                                    env=env, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True)
            for line in proc.stdout:   # Whisper ciktisi (hatalar dahil) hem ekrana hem log.txt'ye
                line = line.rstrip()
                print(line, flush=True)
                try:
                    with open(os.environ['MA_LOG'], 'a', encoding='utf-8') as f:
                        f.write(line + '\n')
                except OSError:
                    pass
            if proc.wait() != 0:
                raise SystemExit('Whisper hata verdi (ayrinti log.txt\'de). Hucreyi tekrar calistir, '
                                 'kaldigi yerden devam eder.')
        times['whisper_sn'] = round(time.time() - t0) - times['indirme_sn'] - times['vokal_sn']
        cues = segment(json.loads(words.read_text(encoding='utf-8'))['words'])
        source = 'whisper'
    write_text(tr_json, json.dumps(dict(episode=ep, source=source, cues=cues), ensure_ascii=False))
    tr_srt = LOCAL / f'{ep_name(ep)}.tr.srt'
    tr_srt.parent.mkdir(parents=True, exist_ok=True)
    write_srt(cues, 'tr', tr_srt)
    write_text(p['work'] / tr_srt.name, tr_srt.read_text(encoding='utf-8'))
    write_text(p['work'] / 'ceviri_girdi.txt', translation_input(cues))

    # 3) bitti isareti (zamanlanmis ceviri bunu bekler)
    times['toplam_sn'] = round(time.time() - t0)
    write_text(p['work'] / 'sureler.json', json.dumps(times))
    marker = 'TEST_TAMAM.txt' if test else 'HAZIR.txt'
    write_text(p['work'] / marker, f'{source} {len(cues)} satir {time.strftime("%Y-%m-%d %H:%M")} {times}\n')
    log(f'BITTI. {len(cues)} Turkce satir ({source}), sureler: {times}.')
    log(f'Simdi Claude\'a "{ep}. bolum {"testi " if test else ""}hazir" de.')
    return dict(episode=ep, source=source, cues=len(cues), **times)


if __name__ == '__main__':
    cmd = sys.argv[1] if len(sys.argv) > 1 else ''
    if cmd == 'asr':
        asr_worker(sys.argv[2], sys.argv[3], *(sys.argv[4:5]))
    elif cmd == 'build':
        build(sys.argv[2], sys.argv[3], sys.argv[4])
    elif cmd == 'segment':  # test: words.json -> tr_cues.json
        ws = json.loads(Path(sys.argv[2]).read_text(encoding='utf-8'))['words']
        Path(sys.argv[3]).write_text(json.dumps(dict(source='whisper', cues=segment(ws)), ensure_ascii=False), encoding='utf-8')
    else:
        print(__doc__)
