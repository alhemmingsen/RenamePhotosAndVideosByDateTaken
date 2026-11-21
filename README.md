# RenamePhotosAndVideosByDateTaken

Renames a folder of photos and videos to `YYYYMMDD_HHMMSS.ext` by the date they were taken,
so they sort chronologically. Shows a preview of every rename first, and can undo a run.

## Usage

```
pip install pillow pymediainfo          # pillow-heif optional, for HEIC files without a dated name
python photo_renamer.py                  # prompts for the folder and an optional suffix
python photo_renamer.py "D:\Photos" --suffix Al --preview
python photo_renamer.py --undo rename_log_Photos_20251121_160000.csv
```

Each run writes `rename_preview_<folder>_<time>.csv` (every file: old name, new name, date source, status)
next to the script, then asks before renaming anything. Applying writes `rename_log_...csv`, which `--undo` reverses.

## How the date is chosen

| Order | Source | Examples |
|---|---|---|
| — | **Name already starts with `YYYYMMDD_HHMMSS`: left alone** | `20250727_193327.jpg`, `20250727_193327(1).jpg`, `20250721_165426_020.jpg` |
| 1 | Capture date in the filename | `IMG_20250321_111214.heic`, `Screenshot_20250419_163727_Chrome.jpg`, `PXL_...` |
| 2 | Google Takeout JSON sidecar | `photoTakenTime` |
| 3 | EXIF Date Taken, then EXIF DateTime | JPEG, PNG, HEIC, and RAW (NEF, CR2, DNG, ARW...) |
| 4 | Embedded video date (UTC converted to local) | `DSC_0013.MOV` |
| 5 | Save/export date in the filename | `BandPhoto_2025_04_22_...`, `FB_IMG_<epoch>`, `IMG-20250601-WA0009`, `lv_..._20251027181335` |
| 6 | File modified date — **only if you answer yes** | Wrong for fresh downloads (Google Photos, cloud, copies), so the default is no |

Files with no date are left alone and listed in the preview. WhatsApp names carry only the day, so the
file's modified time is used when it falls on that same day; otherwise the time is 00:00:00.

Why the filename wins: phones name each file the moment the shutter is pressed, in local time where it was taken.
EXIF DateTime is written a second or so later, file modified dates become the download time when files come
from Google Photos, and video metadata is in UTC at the end of the recording. Trusting the name keeps photos
and videos from a trip abroad in the same local time.

## Naming

- Descriptive parts of the old name are kept after the date:
  `Screenshot_20250419_163727_Chrome.jpg` → `20250419_163727_Screenshot_Chrome.jpg`,
  `Zoo_Day.jpeg` → `20250421_190017_Zoo_Day.jpeg`. Camera names (`DSC_0001`, `IMG_1234`, `141A9505`) are dropped.
- RAW files take the same new name as their JPG twin: `DSC_0001.JPG` + `DSC_0001.NEF` → `20250406_125400.jpg` + `.nef`.
- Same-second clashes get `_01`, `_02`... in capture order. Nothing is ever overwritten.
- An optional suffix goes last: `20250406_125400_Al.jpg` (also added to files that are already dated).
