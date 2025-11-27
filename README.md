# RenamePhotosAndVideosByDateTaken
Rename photos by date taken 
This python script will take a folder full of photos and videos. It will then attempt to rename them all based on date taken. i.e. 20251104
Run the python script, it will prompt you for a folder location. Then it will prompt you for an optional suffix to append to file names. I.e. 20251104_Al

Key logic for videos:
python# For videos: if filename has a date and it's OLDER than file modified,
# use the filename date (likely Google Photos download scenario)
if ext in ('.mp4', '.3gp', '.mov'):
    filename_dt, filename_source = get_date_from_filename(filepath)
    if filename_dt and filename_dt < file_modified:
        return filename_dt, "Filename (Google Photos)"
Updated priority for videos:

PrioritySourceCondition
1stGoogle Takeout JSON If sidecar exists
2nd pymediainfo If encoded/tagged date found
3rd Filename date If filename date < file modified date 
4th File modifiedLast resort
