import { runFileUrl } from "../api/client";
import type { RunFile } from "../api/types";

/** Download links for the files an agent returned in a reply. Shown only when there are files. */
export function RunFiles({
  projectId,
  invocationId,
  files,
}: {
  projectId: string;
  invocationId: string | null;
  files: RunFile[];
}) {
  if (!invocationId || files.length === 0) return null;
  return (
    <ul className="mt-2 flex flex-wrap gap-2 text-xs" aria-label="Files from the agent">
      {files.map((file) => (
        <li key={file.id}>
          <a
            className="rounded border border-stone-200 bg-white px-2 py-1 text-amber-800 hover:bg-stone-50"
            href={runFileUrl(projectId, invocationId, file.id)}
            download={file.name}
          >
            {file.name} <span className="text-stone-500">({Math.max(1, Math.round(file.size_bytes / 1024))} KB)</span>
          </a>
        </li>
      ))}
    </ul>
  );
}
