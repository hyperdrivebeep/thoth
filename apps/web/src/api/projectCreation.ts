export function captureProjectCreation(name: string, cutoff: string, overlay: string) {
  return {
    key: crypto.randomUUID(),
    input: {project_id:`project:web-${crypto.randomUUID()}`,name:name.trim(),cutoff_at:new Date(cutoff).toISOString(),overlay:overlay.trim()},
  };
  // Omit policy_binding_ref: the backend's policy:default expands to a project-unique binding.
}
