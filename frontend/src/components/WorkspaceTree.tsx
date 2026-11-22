import { useEffect, useMemo, useState } from "react";
import type { Workspace, WorkspaceChange } from "../api/types";
import { Card, EmptyState, formatTime } from "./ui";

type Kind = WorkspaceChange["change"];
type TreeNode = { name: string; path: string; isDir: boolean; children: Map<string, TreeNode> };

// Colours carry the meaning, and the letter repeats it, so the state does not depend on colour alone.
const KIND: Record<Kind, { mark: string; label: string; row: string; text: string; badge: string }> = {
  added: {
    mark: "A",
    label: "added",
    row: "bg-emerald-50 hover:bg-emerald-100",
    text: "text-emerald-900",
    badge: "bg-emerald-600 text-white",
  },
  changed: {
    mark: "M",
    label: "changed",
    row: "bg-amber-50 hover:bg-amber-100",
    text: "text-amber-900",
    badge: "bg-amber-600 text-white",
  },
  removed: {
    mark: "D",
    label: "removed",
    row: "bg-red-50 hover:bg-red-100",
    text: "text-red-900 line-through",
    badge: "bg-red-600 text-white",
  },
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

// Every folder that contains a changed file, so those folders can open by default and filter correctly.
function folderAncestors(paths: string[]): Set<string> {
  const folders = new Set<string>();
  for (const path of paths) {
    const parts = path.split("/");
    for (let i = 1; i < parts.length; i++) folders.add(parts.slice(0, i).join("/"));
  }
  return folders;
}

function formatBytes(bytes: number): string {
  if (bytes < 1024) return `${bytes} B`;
  if (bytes < 1024 * 1024) return `${(bytes / 1024).toFixed(1)} KB`;
  return `${(bytes / (1024 * 1024)).toFixed(1)} MB`;
}

function reveal(path: string) {
  // Opening the rows is done by the caller; this only brings the row into view once it has rendered.
  setTimeout(() => {
    document.getElementById(`wt-${CSS.escape(path)}`)?.scrollIntoView({ block: "nearest" });
  }, 0);
}

type TreeContext = {
  changed: Map<string, Kind>;
  changedFolders: Set<string>;
  sizes: Map<string, number>;
  onlyChanged: boolean;
  isOpen: (path: string, depth: number) => boolean;
  toggle: (path: string, depth: number) => void;
};

function TreeRows({ node, depth, ctx }: { node: TreeNode; depth: number; ctx: TreeContext }) {
  const children = sortedChildren(node).filter((child) =>
    !ctx.onlyChanged ? true : child.isDir ? ctx.changedFolders.has(child.path) : ctx.changed.has(child.path),
  );
  return (
    <ul role={depth === 0 ? "tree" : "group"}>
      {children.map((child) => {
        const kind = ctx.changed.get(child.path);
        const open = child.isDir && ctx.isOpen(child.path, depth);
        const hasChanges = ctx.changedFolders.has(child.path);
        const style = kind ? KIND[kind] : undefined;
        return (
          <li key={child.path} role="treeitem" aria-expanded={child.isDir ? open : undefined}>
            <div
              id={`wt-${child.path}`}
              className={`flex items-center gap-1.5 rounded px-1 py-1 text-sm ${
                style ? style.row : "hover:bg-stone-100"
              }`}
              style={{ paddingLeft: `${depth * 16 + 4}px` }}
              title={child.path}
            >
              {child.isDir ? (
                <button
                  type="button"
                  onClick={() => ctx.toggle(child.path, depth)}
                  aria-label={`${open ? "Collapse" : "Expand"} ${child.name}`}
                  className="flex h-5 w-5 shrink-0 items-center justify-center rounded text-stone-500 hover:bg-stone-200"
                >
                  {open ? "▾" : "▸"}
                </button>
              ) : (
                <span className="h-5 w-5 shrink-0" aria-hidden="true" />
              )}
              <span
                className={`min-w-0 flex-1 truncate ${
                  style ? style.text : "text-stone-800"
                } ${child.isDir ? "font-semibold" : ""}`}
              >
                {child.name}
              </span>
              {child.isDir && hasChanges && !open && (
                <span className="shrink-0 rounded-full bg-amber-500 px-1.5 text-xs text-white" aria-label="contains changes">
                  •
                </span>
              )}
              {!child.isDir && !style && (
                <span className="shrink-0 text-xs text-stone-400">{formatBytes(ctx.sizes.get(child.path) ?? 0)}</span>
              )}
              {style && (
                <span
                  className={`shrink-0 rounded px-1.5 text-xs font-bold ${style.badge}`}
                  aria-label={style.label}
                >
                  {style.mark}
                </span>
              )}
            </div>
            {child.isDir && open && <TreeRows node={child} depth={depth + 1} ctx={ctx} />}
          </li>
        );
      })}
    </ul>
  );
}

function DiffView({ diff, truncated }: { diff: string; truncated: boolean }) {
  return (
    <div className="max-h-80 overflow-auto rounded-md bg-stone-900 p-2 font-mono text-xs leading-5">
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
  const [onlyChanged, setOnlyChanged] = useState(false);
  const [overrides, setOverrides] = useState<Record<string, boolean>>({});

  // A new saved turn starts from the defaults again: folders with changes open, the rest closed.
  useEffect(() => {
    setOverrides({});
  }, [workspace?.saved_at]);

  const model = useMemo(() => {
    if (!workspace) return null;
    const changed = new Map(workspace.changes.map((c) => [c.path, c.change] as const));
    const changedPaths = workspace.changes.filter((c) => c.change !== "removed").map((c) => c.path);
    return {
      changed,
      changedFolders: folderAncestors(changedPaths),
      sizes: new Map(workspace.files.map((f) => [f.path, f.size] as const)),
      tree: buildTree(workspace.files.map((f) => f.path)),
      removed: workspace.changes.filter((c) => c.change === "removed"),
      liveChanges: workspace.changes.filter((c) => c.change !== "removed"),
    };
  }, [workspace]);

  if (!workspace || !model) {
    return (
      <Card>
        <h3 className="text-sm font-semibold text-stone-700">Working folder</h3>
        <EmptyState>
          No saved folder yet. Files the agent creates or changes in this session appear here after each reply.
        </EmptyState>
      </Card>
    );
  }

  const ctx: TreeContext = {
    changed: model.changed,
    changedFolders: model.changedFolders,
    sizes: model.sizes,
    onlyChanged,
    isOpen: (path, depth) => overrides[path] ?? (depth === 0 || model.changedFolders.has(path)),
    toggle: (path, depth) =>
      setOverrides((current) => {
        const base = depth === 0 || model.changedFolders.has(path);
        return { ...current, [path]: !(current[path] ?? base) };
      }),
  };

  // Jumping to a changed file opens the folders above it, then brings it into view.
  function jumpTo(path: string) {
    const parents = [...folderAncestors([path])];
    setOverrides((current) => {
      const next = { ...current };
      for (const parent of parents) next[parent] = true;
      return next;
    });
    reveal(path);
  }

  const changeCount = model.liveChanges.length + model.removed.length;

  return (
    <Card className="flex max-h-[calc(100vh-6rem)] flex-col gap-3 p-3">
      <div className="space-y-1">
        <div className="flex items-baseline justify-between gap-2">
          <h3 className="text-sm font-semibold text-stone-800">Working folder</h3>
          <span className="text-xs text-stone-500">{workspace.file_count} files</span>
        </div>
        <p className="text-xs text-stone-500">
          {formatBytes(workspace.total_bytes)} · saved {formatTime(workspace.saved_at)}
        </p>
        <div className="flex items-center gap-1 pt-1" role="group" aria-label="Show">
          <button
            type="button"
            onClick={() => setOnlyChanged(false)}
            aria-pressed={!onlyChanged}
            className={`rounded-md px-2.5 py-1 text-xs font-medium ${
              !onlyChanged ? "bg-stone-800 text-white" : "bg-stone-100 text-stone-700 hover:bg-stone-200"
            }`}
          >
            All files
          </button>
          <button
            type="button"
            onClick={() => setOnlyChanged(true)}
            aria-pressed={onlyChanged}
            className={`rounded-md px-2.5 py-1 text-xs font-medium ${
              onlyChanged ? "bg-stone-800 text-white" : "bg-stone-100 text-stone-700 hover:bg-stone-200"
            }`}
          >
            Changed in last reply ({changeCount})
          </button>
        </div>
      </div>

      {changeCount > 0 && (
        <section className="space-y-1.5 rounded-md border border-stone-200 bg-stone-50 p-2" aria-label="Changed in the last reply">
          <h4 className="text-xs font-semibold uppercase tracking-wide text-stone-500">Changed in the last reply</h4>
          <ul className="max-h-40 space-y-0.5 overflow-auto">
            {model.liveChanges.map((c) => (
              <li key={`${c.change}-${c.path}`}>
                <button
                  type="button"
                  onClick={() => jumpTo(c.path)}
                  className="flex w-full items-center gap-2 rounded px-1.5 py-1 text-left text-sm hover:bg-white"
                  title={`${c.path} (${KIND[c.change].label})`}
                >
                  <span className={`shrink-0 rounded px-1.5 text-xs font-bold ${KIND[c.change].badge}`}>
                    {KIND[c.change].mark}
                  </span>
                  <span className="min-w-0 truncate text-stone-800">{c.path}</span>
                </button>
              </li>
            ))}
            {model.removed.map((c) => (
              <li key={`removed-${c.path}`} className="flex items-center gap-2 px-1.5 py-1 text-sm">
                <span className={`shrink-0 rounded px-1.5 text-xs font-bold ${KIND.removed.badge}`}>D</span>
                <span className="min-w-0 truncate text-red-800 line-through">{c.path}</span>
              </li>
            ))}
          </ul>
        </section>
      )}

      {workspace.files.length === 0 ? (
        <p className="text-sm text-stone-500">The folder is empty.</p>
      ) : onlyChanged && changeCount === 0 ? (
        <p className="text-sm text-stone-500">Nothing changed in the last reply.</p>
      ) : (
        <div className="min-h-0 flex-1 overflow-auto rounded-md border border-stone-200 bg-white p-1">
          <TreeRows node={model.tree} depth={0} ctx={ctx} />
        </div>
      )}

      {workspace.diff && (
        <details>
          <summary className="cursor-pointer select-none text-sm font-medium text-stone-700 hover:text-stone-900">
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
