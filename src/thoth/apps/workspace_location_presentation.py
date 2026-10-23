"""Approved bilingual location notices, projected from the decision used to open storage."""

from pydantic import JsonValue

from thoth.apps.workspace_paths import default_workspace
from thoth.domain.workspace_location import WorkspaceLocation, WorkspaceLocationError
from thoth.ports.workspace_location import WorkspaceLocationReporterPort

KINDS = {
    "KNOWN_SYNC": ("동기화 폴더(OneDrive 등)", "a synced folder (such as OneDrive)"),
    "REMOTE_SHARE": ("네트워크 폴더", "a network folder"),
    "UNSUPPORTED_BRIDGE": ("Windows↔WSL 공유 경로", "a Windows↔WSL shared path"),
}


def location_messages(location: WorkspaceLocation) -> tuple[str, str]:
    reason = ", ".join(location.reasons)
    path = location.canonical_path
    if location.blocked:
        ko, en = KINDS[location.kind]
        default = str(default_workspace())
        return (
            f"이 작업 공간 폴더는 {ko}에 있어 THOTH가 안전하게 쓸 수 없습니다 ({reason}). "
            f"이 PC의 일반 폴더(예: {default})로 옮긴 뒤 다시 실행하세요. "
            f"동기화가 필요하면 완료된 내보내기 파일만 동기화하세요. 작업 공간: {path}",
            f"This workspace folder is on {en} ({reason}), where THOTH cannot work safely. "
            f"Move it to a normal folder on this PC (for example {default}) and run again. "
            f"If you need syncing, sync only finished exports. Workspace: {path}",
        )
    if location.kind == "REMOVABLE":
        return (
            "이동식 드라이브의 작업 공간입니다. "
            "연결을 유지하고 사용을 마친 뒤 안전하게 제거하세요.",
            "This workspace is on a removable drive. "
            "Keep it connected and remove it safely when done.",
        )
    if location.kind == "UNKNOWN":
        ko = (
            f"이 폴더가 동기화·네트워크 폴더가 아닌지 확인하지 못했습니다 ({reason}). "
            "THOTH는 이 위치의 안전을 보증하지 않습니다."
        )
        en = (
            "THOTH could not confirm that this folder is not a synced or network folder "
            f"({reason}). "
            "It cannot vouch for it."
        )
        if not location.confirmed:
            ko += f' 계속하려면 확인하세요: thoth accept-location --workspace "{path}"'
            en += f' To continue, confirm: thoth accept-location --workspace "{path}"'
        return ko, en
    return "", ""


def location_detail(location: WorkspaceLocation) -> dict[str, JsonValue]:
    ko, en = location_messages(location)
    return {
        "classification": location.kind,
        "reason_codes": list(location.reasons),
        "confirmed": location.confirmed,
        "message_ko": ko,
        "message_en": en,
    }


def location_error_detail(error: WorkspaceLocationError) -> dict[str, JsonValue]:
    ko, en = location_messages(error.location)
    return {"code": error.code, "message_ko": ko, "message_en": en}


def workspace_location_payload(stores: object) -> dict[str, JsonValue] | None:
    if not isinstance(stores, WorkspaceLocationReporterPort):
        return None
    location = stores.workspace_location()
    return None if location is None else location_detail(location)
