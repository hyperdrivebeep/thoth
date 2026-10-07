Exchange fixtures for the thoth-trace-csv/1 format (invented data, not measurements).

SYNTHETIC DEMO DATA - NOT MEASURED - NOT APPROVED - NOT FOR ENGINEERING USE

Each file stresses one habit of spreadsheet programs: IDs with leading zeros, long digit strings,
date-like IDs, duplicate IDs, shuffled rows and columns with blank lines and CRLF, UTF-8 with and
without a byte order mark, Korean text and quoting, cells that start with = + - @ tab or CR (raw,
and written with the leading apostrophe an export adds), and the difference between 0, "0",
blank, null and a quoted empty cell. They are stored as raw bytes (see .gitattributes) and are
read by tests/integration/test_trace_csv.py.
