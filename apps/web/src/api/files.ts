export type StagedFile = {
  relative_path: string;
  filename: string;
  media_type: string;
  byte_sha256: string;
  bytes: number;
};

export async function stageFile(file: File, projectId: string): Promise<StagedFile> {
  const body = new FormData();
  body.append("file", file);
  const response = await fetch("/files/stage", { method: "POST", body,
    headers: { "x-thoth-project-id": projectId } });
  if (!response.ok) {
    const detail = (await response.json().catch(() => null)) as { detail?: string } | null;
    throw new Error(detail?.detail ?? `파일 준비 실패 (${response.status})`);
  }
  return (await response.json()) as StagedFile;
}
