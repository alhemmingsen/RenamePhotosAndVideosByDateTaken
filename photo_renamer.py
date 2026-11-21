"""
PHOTO RENAMER - rename photos/videos to YYYYMMDD_HHMMSS by date taken.

Rules:
  * A file whose name already starts with a valid YYYYMMDD_HHMMSS is left alone
    (phones name files at the moment the shutter is pressed, in local time,
    which is the best date there is - including on trips abroad).
  * Otherwise the date comes from, in order:
      1. a capture date in the filename  (IMG_20250321_111214, Screenshot_..., PXL_...)
      2. Google Takeout JSON sidecar     (photoTakenTime)
      3. EXIF Date Taken / DateTime      (JPEG, PNG, HEIC, and TIFF-based RAW: NEF/CR2/DNG/ARW)
      4. embedded video date             (converted from UTC to this PC's time zone)
      5. a save/download date in the name (BandPhoto_, FB_IMG_<epoch>, IMG-YYYYMMDD-WA, lv_..., epoch)
      6. file modified date              - only if you say yes (wrong for fresh downloads)
  * Files with no date are left alone and listed in the preview.
  * Descriptive names are kept after the date:  Zoo_Day.jpg -> 20250421_190017_Zoo_Day.jpg
    Screenshot_20250419_163727_Chrome.jpg -> 20250419_163727_Screenshot_Chrome.jpg
  * RAW files (DSC_0001.NEF) get the same new name as their JPG twin (DSC_0001.JPG).
  * Name clashes get _01, _02 ... never an overwrite.
  * Every run writes a preview CSV first; applying writes a log that --undo can reverse.

Usage:
  python photo_renamer.py                          (interactive)
  python photo_renamer.py FOLDER [--suffix Al] [--preview]
  python photo_renamer.py --undo rename_log_....csv
"""
import os
import sys
import csv
import json
import re
import struct
import argparse
from datetime import datetime, timezone, timedelta
from PIL import Image

try:
    from pymediainfo import MediaInfo
    MEDIAINFO_AVAILABLE = True
except ImportError:
    MEDIAINFO_AVAILABLE = False

try:
    from pillow_heif import register_heif_opener
    register_heif_opener()
    HEIF_AVAILABLE = True
except ImportError:
    HEIF_AVAILABLE = False

IMAGE_EXTS = ('.jpg', '.jpeg', '.png', '.heic', '.heif', '.webp', '.gif', '.tif', '.tiff')
RAW_EXTS = ('.nef', '.cr2', '.cr3', '.arw', '.dng', '.orf', '.rw2', '.raf')
VIDEO_EXTS = ('.mp4', '.mov', '.3gp', '.m4v', '.avi', '.mkv')
MEDIA_EXTS = IMAGE_EXTS + RAW_EXTS + VIDEO_EXTS
TIFF_RAW_EXTS = ('.nef', '.cr2', '.arw', '.dng', '.orf', '.rw2', '.tif', '.tiff')

CANONICAL_RE = re.compile(r'^(\d{8}_\d{6})')
NAME_DATE_RE = re.compile(r'(?<!\d)(\d{4})(\d{2})(\d{2})_(\d{2})(\d{2})(\d{2})')

# Camera/app prefixes that carry no meaning once the date is in front
GENERIC_PREFIXES = {'img', 'vid', 'pxl', 'mvimg', 'pano', 'burst', 'video', 'photo'}
PREFIX_LABELS = {'screenshot': 'Screenshot', 'screen_recording': 'ScreenRecording',
                 'screenrecording': 'ScreenRecording'}

# Machine-generated names with no date in them: drop them entirely
MACHINE_NAME_RES = [re.compile(p, re.I) for p in (
    r'^dsc[nf_]?\d+$', r'^_?dsc\d+$', r'^img_\d+$', r'^_mg_\d+$', r'^mvi_\d+$',
    r'^(?-i:[0-9A-Z_]{4})\d{4}$', r'^gopr\d+$', r'^gh\d+$', r'^p\d{7}$', r'^dji_\d+$',
    r'^fb_img_\d+$', r'^bandphoto[_\d]+$', r'^band_video[_\d]+$',
    r'^(img|vid|aud)-\d{8}-wa\d+$', r'^lv_\d+_\d+$', r'^\d{10}(\d{3})?$',
    r'^temp\d+$', r'^(image|media) \(\d+\)$',
)]


def valid_dt(dt):
    """Reject dates that can't be real (camera clocks reset to 1904/1970, far future)."""
    return dt is not None and 1990 <= dt.year and dt <= datetime.now() + timedelta(days=1)


def make_dt(*parts):
    try:
        dt = datetime(*[int(p) for p in parts])
    except ValueError:
        return None
    return dt if valid_dt(dt) else None


# ---------------------------------------------------------------- filename dates

def get_date_from_name(stem):
    """Capture date written into the name by the camera (local time where taken)."""
    m = NAME_DATE_RE.search(stem)
    if m:
        return make_dt(*m.groups())
    return None


def get_weak_date_from_name(stem):
    """Dates in names that mark when a file was saved/shared/exported, not taken."""
    s = stem.lower()
    m = re.match(r'^band(?:photo|_video)_(\d{4})_(\d{2})_(\d{2})_(\d{2})_(\d{2})_(\d{2})', s)
    if m:
        return make_dt(*m.groups()), "Filename (Band save time)"
    m = re.match(r'^(?:img|vid)-(\d{4})(\d{2})(\d{2})-wa\d+', s)
    if m:
        return make_dt(*m.groups()), "Filename (WhatsApp date only)"
    m = re.search(r'(?<!\d)(\d{4})(\d{2})(\d{2})(\d{2})(\d{2})(\d{2})(?!\d)', s)
    if m:
        return make_dt(*m.groups()), "Filename (export time)"
    m = re.search(r'(?<!\d)(\d{13}|\d{10})(?!\d)', s)
    if m:
        n = int(m.group(1))
        try:
            dt = datetime.fromtimestamp(n / 1000 if len(m.group(1)) == 13 else n)
        except (OverflowError, OSError, ValueError):
            dt = None
        if valid_dt(dt) and dt.year >= 2005:
            return dt, "Filename (epoch save time)"
    return None, None


# ---------------------------------------------------------------- metadata dates

def parse_exif_dt(value):
    if not value:
        return None
    value = str(value).strip().rstrip('\x00')[:19]
    try:
        dt = datetime.strptime(value, '%Y:%m:%d %H:%M:%S')
    except ValueError:
        return None
    return dt if valid_dt(dt) else None


def get_date_from_json(filepath):
    """Google Takeout JSON sidecar"""
    for json_path in (filepath + '.json', os.path.splitext(filepath)[0] + '.json'):
        if os.path.exists(json_path):
            try:
                with open(json_path, 'r', encoding='utf-8') as f:
                    data = json.load(f)
                for key in ('photoTakenTime', 'creationTime'):
                    if key in data:
                        dt = datetime.fromtimestamp(int(data[key]['timestamp']))
                        if valid_dt(dt):
                            return dt, "Google Takeout JSON"
            except Exception:
                pass
    return None, None


def get_date_from_exif(filepath):
    """EXIF Date Taken (lives in the Exif sub-IFD), then IFD0 DateTime"""
    try:
        with Image.open(filepath) as image:
            exif = image.getexif()
            sub = exif.get_ifd(0x8769)
            dt = parse_exif_dt(sub.get(36867)) or parse_exif_dt(sub.get(36868))
            if dt:
                return dt, "EXIF Date Taken"
            dt = parse_exif_dt(exif.get(306))
            if dt:
                return dt, "EXIF DateTime"
    except Exception:
        pass
    return None, None


def get_date_from_tiff_raw(filepath):
    """Minimal TIFF reader for RAW files Pillow can't open (NEF, CR2, DNG, ARW...)"""
    try:
        with open(filepath, 'rb') as f:
            head = f.read(8)
            if head[:2] == b'II':
                e = '<'
            elif head[:2] == b'MM':
                e = '>'
            else:
                return None, None

            def read_ifd(offset):
                f.seek(offset)
                count = struct.unpack(e + 'H', f.read(2))[0]
                entries = {}
                for _ in range(min(count, 500)):
                    tag, typ, n, val = struct.unpack(e + 'HHI4s', f.read(12))
                    entries[tag] = (typ, n, val)
                return entries

            def read_ascii(entry):
                typ, n, val = entry
                if n <= 4:
                    return val[:n].decode('ascii', 'ignore')
                pos = f.tell()
                f.seek(struct.unpack(e + 'I', val)[0])
                s = f.read(n).decode('ascii', 'ignore')
                f.seek(pos)
                return s

            ifd0 = read_ifd(struct.unpack(e + 'I', head[4:8])[0])
            if 0x8769 in ifd0:
                sub = read_ifd(struct.unpack(e + 'I', ifd0[0x8769][2])[0])
                for tag in (36867, 36868):
                    if tag in sub:
                        dt = parse_exif_dt(read_ascii(sub[tag]))
                        if dt:
                            return dt, "EXIF Date Taken"
            if 306 in ifd0:
                dt = parse_exif_dt(read_ascii(ifd0[306]))
                if dt:
                    return dt, "EXIF DateTime"
    except Exception:
        pass
    return None, None


def parse_video_date(value):
    """MediaInfo dates: '2025-05-03 14:35:32 UTC', 'UTC 2025-...', or ISO with offset.
    Returns local (naive) time on this PC."""
    value = str(value).strip()
    is_utc = 'UTC' in value
    value = value.replace('UTC', '').strip()
    m = re.match(r'(\d{4})-(\d{2})-(\d{2})[ T](\d{2}):(\d{2}):(\d{2})(?:\.\d+)?\s*([+-]\d{2}:?\d{2}|Z)?', value)
    if not m:
        return None
    dt = make_dt(*m.groups()[:6])
    if not dt:
        return None
    off = m.group(7)
    if off and off != 'Z':
        sign = 1 if off[0] == '+' else -1
        digits = off[1:].replace(':', '')
        tz = timezone(sign * timedelta(hours=int(digits[:2]), minutes=int(digits[2:])))
        return dt.replace(tzinfo=tz).astimezone().replace(tzinfo=None)
    if is_utc or off == 'Z':
        return dt.replace(tzinfo=timezone.utc).astimezone().replace(tzinfo=None)
    return dt


def get_date_from_video(filepath):
    if not MEDIAINFO_AVAILABLE:
        return None, None
    try:
        for track in MediaInfo.parse(filepath).tracks:
            if track.track_type != "General":
                continue
            # Apple's creation date carries the real local offset - prefer it
            for field in ('comapplequicktimecreationdate', 'recorded_date', 'encoded_date', 'tagged_date'):
                value = getattr(track, field, None)
                if value:
                    dt = parse_video_date(value)
                    if valid_dt(dt):
                        return dt, "Video metadata"
    except Exception:
        pass
    return None, None


def find_date(filepath):
    """Return (datetime, source) or (None, None). Never uses file modified date."""
    stem, ext = os.path.splitext(os.path.basename(filepath))
    ext = ext.lower()

    dt = get_date_from_name(stem)
    if dt:
        return dt, "Filename"

    dt, source = get_date_from_json(filepath)
    if dt:
        return dt, source

    if ext in IMAGE_EXTS:
        dt, source = get_date_from_exif(filepath)
        if dt:
            return dt, source
    if ext in TIFF_RAW_EXTS:
        dt, source = get_date_from_tiff_raw(filepath)
        if dt:
            return dt, source
    if ext in VIDEO_EXTS or ext in ('.heic', '.heif', '.cr3'):
        dt, source = get_date_from_video(filepath)
        if dt:
            return dt, source

    dt, source = get_weak_date_from_name(stem)
    if dt and source == "Filename (WhatsApp date only)":
        # Name has the day only; the modified time is usable if it's the same day
        # (a fresh download would carry today's date and fail this check)
        modified = datetime.fromtimestamp(os.path.getmtime(filepath))
        if modified.date() == dt.date():
            return modified, "Filename date + modified time"
    return dt, source


# ---------------------------------------------------------------- naming

def clean_label(text):
    text = re.sub(r'\(\d+\)', '', text)
    text = re.sub(r'[^\w\-]+', '_', text, flags=re.UNICODE)
    return re.sub(r'_+', '_', text).strip('_-')


def label_for(stem):
    """What to keep from the old name after the new date."""
    m = NAME_DATE_RE.search(stem)
    if m:
        prefix = stem[:m.start()].strip('_- ')
        rest = re.sub(r'^[_\-]?\d*', '', stem[m.end():])   # drop millis / burst numbers
        prefix = PREFIX_LABELS.get(prefix.lower(), '' if prefix.lower() in GENERIC_PREFIXES else prefix)
        return clean_label('_'.join(p for p in (prefix, rest) if p))
    if any(r.match(stem) for r in MACHINE_NAME_RES):
        return ''
    return clean_label(stem)


def is_canonical(name):
    m = CANONICAL_RE.match(name)
    return bool(m) and make_dt(*NAME_DATE_RE.match(m.group(1)).groups()) is not None


def build_plan(folder, suffix, use_modified):
    """Return list of dicts: old, new, date, source, status."""
    all_names = os.listdir(folder)
    media = sorted(f for f in all_names
                   if f.lower().endswith(MEDIA_EXTS) and os.path.isfile(os.path.join(folder, f)))
    occupied = {n.lower() for n in all_names}
    plan = []
    by_stem = {}            # old stem (lower) -> new stem, for RAW+JPG pairs
    pending = []

    print(f"🔍 Reading dates from {len(media)} files...")
    for i, f in enumerate(media, 1):
        if i % 500 == 0:
            print(f"   {i}/{len(media)}")
        path = os.path.join(folder, f)
        stem, ext = os.path.splitext(f)
        if is_canonical(f):
            new_stem = stem
            if suffix and not stem.endswith('_' + suffix):
                new_stem = f"{stem}_{suffix}"
            plan.append(dict(old=f, new_stem=new_stem, ext=ext, date=stem[:15],
                             source="Already dated name", raw=ext.lower() in RAW_EXTS))
            continue
        dt, source = find_date(path)
        if not dt and use_modified:
            dt, source = datetime.fromtimestamp(os.path.getmtime(path)), "File modified date"
        pending.append(dict(old=f, stem=stem, ext=ext, dt=dt, source=source,
                            raw=ext.lower() in RAW_EXTS))

    # Chronological order so _01/_02 follow capture order; non-RAW first so RAW can pair
    pending.sort(key=lambda p: (p['raw'], p['dt'] or datetime.max, p['old'].lower()))
    for p in plan:
        if not p['raw']:
            by_stem[os.path.splitext(p['old'])[0].lower()] = p['new_stem']
    for p in pending:
        ext = p['ext'].lower()
        entry = dict(old=p['old'], ext=ext, source=p['source'] or "No date found", raw=p['raw'])
        twin = by_stem.get(p['stem'].lower()) if p['raw'] else None
        if twin and (twin + ext).lower() not in occupied:
            entry.update(new_stem=twin, date=twin[:15], source=entry['source'] + " (paired with JPG)")
        elif p['dt']:
            base = p['dt'].strftime('%Y%m%d_%H%M%S')
            label = label_for(p['stem'])
            if label:
                base = f"{base}_{label}"
            tail = f"_{suffix}" if suffix else ""
            candidate, n = base + tail, 0
            while (candidate + ext).lower() in occupied:
                n += 1
                candidate = f"{base}_{n:02d}{tail}"
            entry.update(new_stem=candidate, date=p['dt'].strftime('%Y%m%d_%H%M%S'))
        else:
            entry.update(new_stem=p['stem'], ext=p['ext'], date='')
        new_name = entry['new_stem'] + entry['ext']
        if new_name != p['old']:
            occupied.add(new_name.lower())
        if not p['raw']:
            by_stem[p['stem'].lower()] = entry['new_stem']
        plan.append(entry)

    for e in plan:
        e['new'] = e['new_stem'] + e['ext']
        if e['source'] == "No date found":
            e['status'] = "no date - left alone"
        elif e['new'] == e['old']:
            e['status'] = "unchanged"
        else:
            e['status'] = "rename"
    plan.sort(key=lambda e: e['old'].lower())
    return plan


def looks_freshly_downloaded(folder, names):
    """True if nearly all modified dates fall within a couple of hours (a bulk download/copy)."""
    times = sorted(os.path.getmtime(os.path.join(folder, n)) for n in names)
    if len(times) < 20:
        return False
    lo, hi = times[len(times) // 20], times[-(len(times) // 20) - 1]
    return hi - lo < 3 * 3600


def write_csv(path, rows, fields):
    with open(path, 'w', newline='', encoding='utf-8-sig') as f:
        w = csv.DictWriter(f, fieldnames=fields, extrasaction='ignore')
        w.writeheader()
        w.writerows(rows)


def ask(prompt, default=False):
    answer = input(prompt).strip().lower()
    if not answer:
        return default
    return answer in ('y', 'yes')


# ---------------------------------------------------------------- main flows

def rename_photos(folder, suffix="", preview_only=False):
    folder = os.path.abspath(folder)
    report_dir = os.path.dirname(os.path.abspath(__file__))
    stamp = datetime.now().strftime('%Y%m%d_%H%M%S')
    tag = clean_label(os.path.basename(folder)) or 'folder'

    print(f"\n📁 Folder: {folder}")
    if suffix:
        print(f"🏷️  Suffix: _{suffix}")
    if not MEDIAINFO_AVAILABLE:
        print("⚠️  pymediainfo not installed - videos without a dated name can't be dated")
        print("   Install with: pip install pymediainfo")

    plan = build_plan(folder, suffix, use_modified=False)
    undated = [e for e in plan if e['status'].startswith("no date")]

    if undated:
        print(f"\n❓ {len(undated)} files have no date in their name or metadata, e.g.:")
        for e in undated[:5]:
            print(f"     {e['old']}")
        if looks_freshly_downloaded(folder, [e['old'] for e in plan]):
            print("   ⚠️  This folder looks freshly downloaded/copied: modified dates are the")
            print("      download time, not when the photos were taken. Answer no.")
        if ask("   Use each file's modified date for these? (yes/NO): "):
            plan = build_plan(folder, suffix, use_modified=True)

    renames = [e for e in plan if e['status'] == "rename"]
    preview_path = os.path.join(report_dir, f"rename_preview_{tag}_{stamp}.csv")
    write_csv(preview_path, plan, ['old', 'new', 'date', 'source', 'status'])

    counts = {}
    for e in plan:
        key = e['status'] if e['status'] != "rename" else f"rename via {e['source']}"
        counts[key] = counts.get(key, 0) + 1
    print("\n" + "=" * 60)
    print("📋 PREVIEW")
    print("=" * 60)
    for key, n in sorted(counts.items(), key=lambda kv: -kv[1]):
        print(f"   {n:6}  {key}")
    if renames:
        print("\n   Examples:")
        for e in renames[:10]:
            print(f"     {e['old']}  →  {e['new']}   ({e['source']})")
    print(f"\n📄 Full list: {preview_path}")

    if not renames:
        print("\n✅ Nothing to rename.")
        return
    if preview_only:
        print("\n👀 Preview only - no files were renamed.")
        return
    if not ask(f"\n⚠️  Rename {len(renames)} files? (yes/no): "):
        print("Operation cancelled. No files were renamed.")
        return

    log_path = os.path.join(report_dir, f"rename_log_{tag}_{stamp}.csv")
    done, errors = [], 0
    try:
        for e in renames:
            src, dst = os.path.join(folder, e['old']), os.path.join(folder, e['new'])
            if os.path.exists(dst):
                print(f"⚠️  Skipped {e['old']} - {e['new']} already exists")
                continue
            try:
                os.rename(src, dst)
                done.append(dict(folder=folder, old=e['old'], new=e['new'], source=e['source']))
            except OSError as ex:
                errors += 1
                print(f"❌ {e['old']}: {ex}")
    except KeyboardInterrupt:
        print("\n❌ Stopped by user - files renamed so far are in the log.")
    finally:
        write_csv(log_path, done, ['folder', 'old', 'new', 'source'])

    print("\n" + "=" * 60)
    print(f"✅ Renamed: {len(done)}   ❌ Errors: {errors}")
    print(f"↩️  To undo: python photo_renamer.py --undo \"{log_path}\"")
    print("=" * 60)


def undo(log_path):
    with open(log_path, newline='', encoding='utf-8-sig') as f:
        rows = list(csv.DictReader(f))
    restored = 0
    for r in reversed(rows):
        src, dst = os.path.join(r['folder'], r['new']), os.path.join(r['folder'], r['old'])
        if os.path.exists(src) and not os.path.exists(dst):
            os.rename(src, dst)
            restored += 1
        else:
            print(f"⚠️  Couldn't restore {r['new']} → {r['old']}")
    print(f"↩️  Restored {restored} of {len(rows)} files")


def main():
    parser = argparse.ArgumentParser(description="Rename photos/videos to YYYYMMDD_HHMMSS by date taken")
    parser.add_argument('folder', nargs='?', help="folder to rename (asked for if omitted)")
    parser.add_argument('--suffix', default=None, help="text to add after the date, e.g. Al")
    parser.add_argument('--preview', action='store_true', help="write the preview list only, rename nothing")
    parser.add_argument('--undo', metavar='LOG', help="reverse a previous run using its rename_log CSV")
    args = parser.parse_args()

    if args.undo:
        undo(args.undo)
        return

    print("=" * 60)
    print("📷 PHOTO RENAMER - Rename by Date Taken")
    print("=" * 60)
    print("  IMG_20250321_111214.jpg     → 20250321_111214.jpg")
    print("  DSC_0001.JPG + DSC_0001.NEF → 20250406_125400.jpg + .nef")
    print("  20250727_193327.jpg         → left alone (already dated)")

    folder = args.folder or input("\n📁 Enter folder path (or drag folder here): ").strip().strip('"')
    if not os.path.isdir(folder):
        print(f"\n❌ Error: '{folder}' is not a folder!")
        return
    suffix = args.suffix
    if suffix is None:
        suffix = input("\n🏷️  Enter suffix to add (or press Enter for none): ").strip()
    rename_photos(folder, clean_label(suffix), args.preview)


if __name__ == "__main__":
    # Emoji output must not crash on consoles/pipes that aren't UTF-8
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(errors='replace')
        except Exception:
            pass
    try:
        main()
    except Exception as e:
        print(f"\n❌ Unexpected error: {e}")
    finally:
        if len(sys.argv) == 1:
            input("\n📌 Press Enter to exit...")
