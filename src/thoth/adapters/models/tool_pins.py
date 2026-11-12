"""The exact versions and package integrity values of the tools THOTH installs, in one place.

THOTH installs these packages with npm, runs none of their install scripts, and accepts them only
when the integrity npm recorded for the installed packages is the value written here. To move to a
new version, change the values here together and check the result with the install tests.
"""

CODEX_PACKAGE_VERSION = "0.157.1"
CODEX_PLATFORM_VERSION = "0.157.1-win32-x64"
CODEX_WRAPPER_INTEGRITY = (
    "sha512-qJ/UZ0bmYP+/Umav1L9WpmtMYeA6q1+4r4qILSYOKQZhP7WRdjy"
    "TQWz3O0dTImZ9RT7AazZa85D87xRDHogcHw=="
)
CODEX_PLATFORM_INTEGRITY = (
    "sha512-vgqs/VRXNwhLYMsZDgYfnRSXpRh5Nm782L8lacGskw86kOxbMaquvQKxku"
    "ZHUBrJA2XGcksB7rMUHy1XaCJgrA=="
)

CLAUDE_CODE_VERSION = "2.1.284"
CLAUDE_CODE_WRAPPER_INTEGRITY = (
    "sha512-IuENsoLa+Y5fx5VaP0Md3n5UO7aYxjbFE/iydSDw6tMo2171oaxaaBa9"
    "oIepPG9NILd1owSx74ozsqAkTbEOjw=="
)
CLAUDE_CODE_PLATFORM_INTEGRITY = (
    "sha512-Usw9Z8unwN6sWlhAJm5pBdTj5LoYBotUddhCBpnnH1XFJT9dgxRvRXqhftuVQi6LhorFo/"
    "HD0VfeHmmJFNCMaw=="
)
