"""Parse a combined basketball participant import — Team Name, Participant
Name, Jersey Number, Phone Number — from either a file upload (.csv/.xlsx) or
pasted tabular text.

`parse_participants(uploaded_file=None, pasted_text=None)` returns a
`ParticipantImportParse` with:
  - rows: list of ParticipantRow, one per non-blank data row, each carrying
    its own `.error` if that row failed validation (the row is still
    included so the view can report it, not silently dropped)
  - errors: file-level errors (bad file, missing required columns) — when
    non-empty, `rows` is empty and the whole import should be aborted
  - skipped_blank: count of empty rows ignored gracefully
"""
import re
from dataclasses import dataclass, field

from .tabular_import import read_pasted_text_rows, read_uploaded_file_rows

# Accepted header labels (matched case-insensitively, trimmed).
_TEAM_HEADERS = {'team name', 'team'}
_NAME_HEADERS = {'participant name', 'name', 'player name', 'player', 'full name'}
_JERSEY_HEADERS = {'jersey number', 'jersey', 'jersey no', 'jersey #', 'number', 'no', '#'}
_PHONE_HEADERS = {'phone number', 'phone', 'mobile', 'contact', 'contact number'}

MAX_JERSEY = 99

# Optional leading '+', 8-16 digits total, allowing internal spaces/hyphens.
_PHONE_RE = re.compile(r'^\+?\d[\d\s-]{6,14}\d$')


@dataclass
class ParticipantRow:
    excel_row: int
    team_name: str
    participant_name: str
    jersey: str
    phone: str
    error: str = ''


@dataclass
class ParticipantImportParse:
    rows: list = field(default_factory=list)
    errors: list = field(default_factory=list)
    skipped_blank: int = 0


def parse_participants(uploaded_file=None, pasted_text=None, require_jersey=True):
    """Exactly one of uploaded_file/pasted_text should be provided; the
    caller is responsible for that check. File takes precedence if both
    are somehow given.

    `require_jersey=False` (racket-sport doubles/mixed-doubles pairs have no
    jersey numbers) makes the Jersey Number column optional — a blank cell
    is accepted rather than rejected."""
    try:
        if uploaded_file is not None:
            rows = read_uploaded_file_rows(uploaded_file)
        elif pasted_text is not None:
            rows = read_pasted_text_rows(pasted_text)
        else:
            return ParticipantImportParse(errors=['Paste some rows or choose a file to import.'])
    except ValueError as e:
        if str(e) == 'unsupported_type':
            return ParticipantImportParse(errors=['Unsupported file type — upload a .csv or .xlsx file.'])
        return ParticipantImportParse(errors=['Could not read the file. Make sure it is a valid CSV or .xlsx file.'])

    return _validate_rows(rows, require_jersey=require_jersey)


def _norm_jersey(raw, required=True):
    """Return (value, error). Blank is rejected only when `required`."""
    s = str(raw or '').strip()
    if not s:
        return ('', None) if not required else (None, 'jersey number is required')
    try:
        f = float(s)          # tolerate '10' and Excel's '10.0'
    except ValueError:
        return None, f'jersey number "{s}" is not a number'
    if f != int(f):
        return None, f'jersey number "{s}" is not a whole number'
    n = int(f)
    if n < 0 or n > MAX_JERSEY:
        return None, f'jersey number {n} is out of range (0–{MAX_JERSEY})'
    return str(n), None


def _norm_phone(raw):
    """Return (value, error). Blank is always valid — phone is optional."""
    s = str(raw or '').strip()
    if not s:
        return '', None
    if not _PHONE_RE.match(s):
        return None, f'phone number "{s}" is not valid'
    return s, None


@dataclass
class IndividualRow:
    excel_row: int
    participant_name: str
    phone: str
    error: str = ''


def parse_individual_participants(uploaded_file=None, pasted_text=None):
    """Parse a simple individual-registration import — Participant Name +
    optional Phone Number, one row per entrant. No team grouping, no jersey
    numbers — for racket-sport Singles/Women's tournaments (see
    RACKET_SPORTS), which register solo entrants rather than teams/pairs."""
    try:
        if uploaded_file is not None:
            rows = read_uploaded_file_rows(uploaded_file)
        elif pasted_text is not None:
            rows = read_pasted_text_rows(pasted_text)
        else:
            return ParticipantImportParse(errors=['Paste some rows or choose a file to import.'])
    except ValueError as e:
        if str(e) == 'unsupported_type':
            return ParticipantImportParse(errors=['Unsupported file type — upload a .csv or .xlsx file.'])
        return ParticipantImportParse(errors=['Could not read the file. Make sure it is a valid CSV or .xlsx file.'])

    result = ParticipantImportParse()

    header_idx = None
    for i, row in enumerate(rows):
        if any((c or '').strip() for c in row):
            header_idx = i
            break
    if header_idx is None:
        result.errors.append('The input is empty.')
        return result

    header = [(c or '').strip().lower() for c in rows[header_idx]]
    name_col = next((i for i, h in enumerate(header) if h in _NAME_HEADERS), None)
    phone_col = next((i for i, h in enumerate(header) if h in _PHONE_HEADERS), None)
    if name_col is None:
        result.errors.append('Missing required column: Participant Name. '
                             'Expected headers "Participant Name" and, optionally, "Phone Number".')
        return result

    for i in range(header_idx + 1, len(rows)):
        row = rows[i]
        excel_row = i + 1
        if not any((c or '').strip() for c in row):
            result.skipped_blank += 1
            continue

        def cell(col):
            return (row[col].strip() if col is not None and col < len(row) else '')

        participant_name = cell(name_col)
        phone_raw = cell(phone_col)

        if not participant_name:
            result.rows.append(IndividualRow(excel_row, participant_name, '',
                                             error='participant name is required'))
            continue
        phone, perr = _norm_phone(phone_raw)
        if perr:
            result.rows.append(IndividualRow(excel_row, participant_name, '', error=perr))
            continue
        result.rows.append(IndividualRow(excel_row, participant_name, phone))

    return result


def _validate_rows(rows, require_jersey=True):
    result = ParticipantImportParse()

    # First non-empty row is the header.
    header_idx = None
    for i, row in enumerate(rows):
        if any((c or '').strip() for c in row):
            header_idx = i
            break
    if header_idx is None:
        result.errors.append('The input is empty.')
        return result

    header = [(c or '').strip().lower() for c in rows[header_idx]]
    team_col = next((i for i, h in enumerate(header) if h in _TEAM_HEADERS), None)
    name_col = next((i for i, h in enumerate(header) if h in _NAME_HEADERS), None)
    jersey_col = next((i for i, h in enumerate(header) if h in _JERSEY_HEADERS), None)
    phone_col = next((i for i, h in enumerate(header) if h in _PHONE_HEADERS), None)

    missing = []
    if team_col is None:
        missing.append('Team Name')
    if name_col is None:
        missing.append('Participant Name')
    if require_jersey and jersey_col is None:
        missing.append('Jersey Number')
    if missing:
        expected = '"Team Name", "Participant Name" and "Jersey Number"' if require_jersey \
            else '"Team Name" and "Participant Name"'
        result.errors.append('Missing required column(s): ' + ', '.join(missing) +
                             f'. Expected headers {expected}.')
        return result

    for i in range(header_idx + 1, len(rows)):
        row = rows[i]
        excel_row = i + 1  # 1-based, for messages

        if not any((c or '').strip() for c in row):
            result.skipped_blank += 1
            continue

        def cell(col):
            return (row[col].strip() if col is not None and col < len(row) else '')

        team_name = cell(team_col)
        participant_name = cell(name_col)
        jersey_raw = cell(jersey_col)
        phone_raw = cell(phone_col)

        if not team_name:
            result.rows.append(ParticipantRow(excel_row, team_name, participant_name, '', '',
                                              error='team name is required'))
            continue
        if not participant_name:
            result.rows.append(ParticipantRow(excel_row, team_name, participant_name, '', '',
                                              error='participant name is required'))
            continue

        jersey, jerr = _norm_jersey(jersey_raw, required=require_jersey)
        if jerr:
            result.rows.append(ParticipantRow(excel_row, team_name, participant_name, '', '', error=jerr))
            continue

        phone, perr = _norm_phone(phone_raw)
        if perr:
            result.rows.append(ParticipantRow(excel_row, team_name, participant_name, jersey, '', error=perr))
            continue

        result.rows.append(ParticipantRow(excel_row, team_name, participant_name, jersey, phone))

    return result
