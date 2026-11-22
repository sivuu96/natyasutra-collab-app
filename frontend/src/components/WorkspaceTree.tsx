import type { Workspace, WorkspaceChange } from "../api/types";
import { Card, EmptyState, formatTime } from "./ui";

type TreeNode = { name: string; path: string; isDir: boolean; children: Map<string, TreeNode> };

const CHANGE_STYLE: Record<WorkspaceChange["change"], { mark: string; className: string; label: string }> = {
  added: { mark: "A", className: "text-emerald-700", label: "added" },
  changed: { mark: "M", className: "text-amber-700", label: "changed" },
  removed: { mark: "D", className: "text-red-700", label: "removed" },
};

function buildTree(paths: string[]): TreeNode {
  const root: TreeNode = { name: "", path: "", isDir: true, children: new Map() };
  for (const path of paths) {
    const parts = path.split("/");
    let node = root;
    parts.forEach((part, index) => {
      let child = node.children.get(part);
      if (!child) {
        child = {
          name: part,
          path: parts.slice(0, index + 1).join("/"),
          isDir: index < parts.length - 1,
          children: new Map(),
        };
        node.children.set(part, child);
      }
      node = child;
    });
  }
  return root;
}

function sortedChildren(node: TreeNode): TreeNode[] {
  return [...node.children.values()].sort((a, b) => {
    if (a.isDir !== b.isDir) return a.isDir ? -1 : 1; // folders first, like VS Code
    return a.name.localeCompare(b.name);
  });
}

function formatBytes(bytes: number): string {
  if (bytes < 1024) return `${bytes} B`;
  if (bytes < 1024 * 1024) return `${(bytes / 1024).toFixed(1)} KB`;
  return `${(bytes / (1024 * 1024)).toFixed(1)} MB`;
}

function TreeRows({
  node,
  depth,
  changed,
}: {
  node: TreeNode;
  depth: number;
  changed: Map<string, WorkspaceChange["change"]>;
}) {
  return (
    <ul role={depth === 0 ? "tree" : "group"}>
      {sortedChildren(node).map((child) => {
        const own = changed.get(child.path);
        const innerChange = child.isDir
          ? [...changed.keys()].some((path) => path.startsWith(`${child.path}/`))
          : false;
        const style = own ? CHANGE_STYLE[own] : undefined;
        return (
          <li key={child.path} role="treeitem" aria-expanded={child.isDir ? true : undefined}>
            <div
              className="flex items-center gap-1 py-0.5 text-sm"
              style={{ paddingLeft: `${depth * 12}px` }}
              title={style ? `${child.path} (${style.label})` : child.path}
            >
              <span className="w-3 text-center text-stone-400">{child.isDir ? "▾" : ""}</span>
              <span className={`truncate ${style ? style.className : "text-stone-800"}`}>{child.name}</span>
              {style && <span className={`ml-auto pl-2 text-xs font-semibold ${style.className}`}>{style.mark}</span>}
              {!style && innerChange && (
                <span className="ml-auto pl-2 text-xs text-amber-700" aria-label="contains changes">
                  •
                </span>
              )}
            </div>
            {child.isDir && <TreeRows node={child} depth={depth + 1} changed={changed} />}
          </li>
        );
      })}
    </ul>
  );
}

function DiffView({ diff, truncated }: { diff: string; truncated: boolean }) {
  return (
    <div className="max-h-72 overflow-auto rounded-md bg-stone-900 p-2 font-mono text-xs leading-5">
      {diff.split("\n").map((line, index) => {
        const isAdd = line.startsWith("+") && !line.startsWith("+++");
        const isDel = line.startsWith("-") && !line.startsWith("---");
        const className = isAdd ? "text-emerald-300" : isDel ? "text-red-300" : "text-stone-300";
        return (
          <div key={index} className={`whitespace-pre ${className}`}>
            {line || " "}
          </div>
        );
      })}
      {truncated && <div className="mt-2 text-stone-400">The diff is shortened. Only the first part is shown.</div>}
    </div>
  );
}

export function WorkspaceTree({ workspace }: { workspace: Workspace | null }) {
  if (!workspace) {
    return (
      <Card>
        <h3 className="text-xs font-semibold uppercase text-stone-500">Working folder</h3>
        <EmptyState>
          No saved folder yet. Files the agent creates or changes in this session appear here after each reply.
        </EmptyState>
      </Card>
    );
  }

  const changed = new Map(workspace.changes.map((c) => [c.path, c.change] as const));
  const removed = workspace.changes.filter((c) => c.change === "removed");
  const tree = buildTree(workspace.files.map((f) => f.path));

  return (
    <Card className="space-y-3">
      <div>
        <h3 className="text-xs font-semibold uppercase text-stone-500">Working folder</h3>
        <p className="text-xs text-stone-500">
          {workspace.file_count} files · {formatBytes(workspace.total_bytes)} · saved {formatTime(workspace.saved_at)}
        </p>
      </div>

      {workspace.files.length === 0 ? (
        <p className="text-sm text-stone-500">The folder is empty.</p>
      ) : (
        <div className="max-h-96 overflow-auto rounded-md border border-stone-200 p-1">
          <TreeRows node={tree} depth={0} changed={changed} />
        </div>
      )}

      {workspace.changes.length > 0 && (
        <div className="space-y-1">
          <h4 className="text-xs font-semibold text-stone-600">Changed in the last reply</h4>
          <ul className="space-y-0.5 text-xs">
            {workspace.changes
              .filter((c) => c.change !== "removed")
              .map((c) => (
                <li key={`${c.change}-${c.path}`} className={CHANGE_STYLE[c.change].className}>
                  {CHANGE_STYLE[c.change].mark} {c.path}
                </li>
              ))}
          </ul>
        </div>
      )}

      {removed.length > 0 && (
        <div className="space-y-1">
          <h4 className="text-xs font-semibold text-stone-600">Removed in the last reply</h4>
          <ul className="space-y-0.5 text-xs text-red-700">
            {removed.map((c) => (
              <li key={c.path}>D {c.path}</li>
            ))}
          </ul>
        </div>
      )}

      {workspace.diff && (
        <details>
          <summary className="cursor-pointer select-none text-xs font-semibold text-stone-600 hover:text-stone-800">
            Diff against the branch
          </summary>
          <div className="mt-2">
            <DiffView diff={workspace.diff} truncated={workspace.diff_truncated} />
          </div>
        </details>
      )}
    </Card>
  );
}
