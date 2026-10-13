"""Safe appending to db/contacts.csv — the canonical dedup source for manual work.

The file is also edited by hand (Excel, Numbers, a text editor), so: read
with BOM handling, refuse to write into a file of a foreign format (a
different delimiter means no "ник" column), and before appending, add a
line break at the end if it's missing — otherwise the new row would merge
into the last one."""
import csv
import os

from leadgen.core import contacts


class FormatError(Exception):
    pass


def read(path):
    """(header, known nicks). Raises FormatError if the file isn't ours by format."""
    with open(path, encoding='utf-8-sig', newline='') as f:
        reader = csv.DictReader(f)
        header = reader.fieldnames or []
        if 'ник' not in header:
            raise FormatError('в %s нет колонки «ник» — другой разделитель или формат' % path)
        known = {contacts.norm_nick(r.get('ник')) for r in reader}
    known.discard('')
    return header, known


def append(path, header, rows):
    if not rows:
        return 0
    with open(path, 'rb+') as f:
        f.seek(0, os.SEEK_END)
        if f.tell() > 0:
            f.seek(-1, os.SEEK_END)
            if f.read(1) != b'\n':
                f.write(b'\r\n')
    with open(path, 'a', encoding='utf-8', newline='') as f:
        w = csv.DictWriter(f, fieldnames=header, extrasaction='ignore', lineterminator='\r\n')
        for r in rows:
            w.writerow({k: r.get(k, '') for k in header})
    return len(rows)
