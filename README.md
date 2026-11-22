# RenamePhotosAndVideosByDateTaken

Renames a folder of photos and videos to `YYYYMMDD_HHMMSS.ext` by the date they were taken,
so they sort chronologically. Shows a preview of every rename first, and can undo a run.

## Usage

```
pip install pillow pymediainfo          # pillow-heif optional, for HEIC files without a dated name
python photo_renamer.py                  # prompts for the folder
python photo_renamer.py "D:\Photos\Al" --preview      # names end in _Al (the folder's name)
python photo_renamer.py "D:\Photos\Al" --suffix Phone  # or pick the suffix yourself
python photo_renamer.py --undo rename_log_Al_20251121_160000.csv
```

Each run writes `rename_preview_<folder>_<time>.csv` (every file: old name, new name, date source, status)
next to the script, then asks before renaming anything. See [Logging and undo](#logging-and-undo).

## How the date is chosen

| Order | Source | Examples |
|---|---|---|
| — | **Name already starts with `YYYYMMDD_HHMMSS`: left alone** | `20250727_193327.jpg`, `20250727_193327(1).jpg`, `20250721_165426_020.jpg` |
| 1 | Capture date in the filename | `IMG_20250321_111214.heic`, `Screenshot_20250419_163727_Chrome.jpg`, `PXL_...` |
| 2 | Google Takeout JSON sidecar | `photoTakenTime` |
| 3 | EXIF Date Taken, then EXIF DateTime | JPEG, PNG, HEIC, and RAW (NEF, CR2, DNG, ARW...) |
| 4 | Embedded video date (UTC converted to local) | `DSC_0013.MOV` |
| 5 | Save/export date in the filename | `BandPhoto_2025_04_22_...`, `FB_IMG_<epoch>`, `IMG-20250601-WA0009`, `lv_..._20251027181335` |
| 6 | File modified date — **only if it survived a copy** | Used when it's over a day older than the file's created time (when it landed on this disk). Fresh downloads (Google Photos, cloud) get the download time for both, so they're skipped |

Files with no date are left alone and listed in the preview. WhatsApp names carry only the day, so the
file's modified time is used when it falls on that same day; otherwise the time is 00:00:00.

Why the filename wins: phones name each file the moment the shutter is pressed, in local time where it was taken.
EXIF DateTime is written a second or so later, file modified dates become the download time when files come
from Google Photos, and video metadata is in UTC at the end of the recording. Trusting the name keeps photos
and videos from a trip abroad in the same local time.

## Fixing a wrong camera clock

Cameras don't set their own clocks. If one was left on home time abroad (or set to the wrong zone),
its shots land hours away from the phone photos of the same moments once everything is merged.
`--shift` corrects a numbered range of files before naming:

```
python photo_renamer.py "D:\Camera" --suffix Camera --shift DSC_0925-DSC_1369=+13h --shift DSC_1370-DSC_1796=-2h
python photo_renamer.py "D:\Camera" --suffix Camera --shift camera_shifts.txt
```

A shifts file has one `RANGE=OFFSET` per line (`#` for comments). Offsets look like `+13h`, `-2.5h`, `-2h30m`, `+45m`.
The preview shows how many files each shift matched and marks every corrected file.

Camera videos are dated by the camera's displayed clock (the file's modified time, when it equals the
embedded UTC time plus a whole time-zone step), so one shift fits a camera's photos and videos alike.

To work out the offset, compare a camera shot with a phone photo of the same moment.

### After renaming

Once renamed, the `DSC_` numbers are gone, so a clock problem spotted later is fixed by date range instead.
`--fix-time` shifts the date in names this script already wrote, keeping the rest of the name. It works on
a merged folder too: `--only` limits it to one device's suffix.

```
python photo_renamer.py "D:\Merged" --fix-time 20250101-20250719=-1h --only Camera --preview
python photo_renamer.py "D:\Merged" --fix-time 20250405_1200-20250405_1830=+30m --only Camera
```

Range ends are inclusive (`20250719` as an end means all of that day). It's logged and undoable like any run.

## Merging devices into one folder

Rename each device's folder before merging; each gets its folder's name as a suffix (`_Al`, `_Camera`, ...).
Two devices can take photos in the same second, and without a suffix the names would collide when copied
together. Explorer's name sort then interleaves all devices in true chronological order.

## Naming

- Descriptive parts of the old name are kept after the date:
  `Screenshot_20250419_163727_Chrome.jpg` → `20250419_163727_Screenshot_Chrome.jpg`,
  `Zoo_Day.jpeg` → `20250421_190017_Zoo_Day.jpeg`. Camera names (`DSC_0001`, `IMG_1234`, `141A9505`) are dropped.
- RAW files take the same new name as their JPG twin: `DSC_0001.JPG` + `DSC_0001.NEF` → `20250406_125400.jpg` + `.nef`.
- Same-second clashes get `_01`, `_02`... in capture order. Nothing is ever overwritten.
- The suffix (the folder's name unless `--suffix` is given) goes last: `20250406_125400_Al.jpg`
  (also added to files that are already dated).

## Logging and undo

Every rename is written to disk the moment it happens, so a crash or power cut loses nothing. Each one goes to
that run's `rename_log_<folder>_<run>.csv` and to `rename_history.csv`, a running record of every run
(both next to the script).

```
python photo_renamer.py --undo rename_log_Al_20251121_160000.csv   # one run
python photo_renamer.py --undo all                                  # every run, newest first
python photo_renamer.py --undo all --moved-to "D:\Merged"           # files were moved after renaming
```

`--moved-to` finds files that were moved after renaming (e.g. merged into one folder) and puts them back in
their original folders under their original names. Undos are logged too, so an undo can itself be undone.
