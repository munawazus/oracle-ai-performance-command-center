import { useQuery } from "@tanstack/react-query";
import { useEffect, useState } from "react";

import { Badge } from "../components/ui/Badge";
import { EmptyState } from "../components/ui/EmptyState";
import { ErrorState } from "../components/ui/ErrorState";
import { Loading } from "../components/ui/Loading";
import { DatabaseSelector, useDatabases } from "../components/telemetry/DatabaseSelector";
import { apiFetch, errorMessage } from "../lib/api";
import type { BlockingChainEntry, BlockingSnapshot } from "../types/api";

export interface BlockingNode {
  sid: number;
  username: string | null;
  sqlId: string | null;
  inThisSample: boolean;
  waiters: BlockingNode[];
}

/** `chains` is flat - one entry per session with its *direct* blocker (see
 * backend/app/telemetry_views.py's build_blocking_chains). That's already enough to
 * reconstruct a real multi-level tree: if C is blocked by B and B is blocked by A, that's two
 * entries in the same list, and a waiter that's itself a blocker elsewhere gets recursed into.
 * A root is any blocker_sid that never itself appears as a blocked_sid. */
export function buildBlockingTree(chains: BlockingChainEntry[]): BlockingNode[] {
  const blockedSids = new Set(chains.map((c) => c.blocked_sid));
  const waitersByBlocker = new Map<number, BlockingChainEntry[]>();
  for (const c of chains) {
    const list = waitersByBlocker.get(c.blocker_sid) ?? [];
    list.push(c);
    waitersByBlocker.set(c.blocker_sid, list);
  }

  function buildNode(
    sid: number,
    username: string | null,
    sqlId: string | null,
    inThisSample: boolean,
    visited: Set<number>,
  ): BlockingNode {
    // Cycle guard: a real Oracle instance's own deadlock detection means a true cycle
    // shouldn't persist, but this is cheap insurance against ever inconsistent data.
    if (visited.has(sid)) return { sid, username, sqlId, inThisSample, waiters: [] };
    const nextVisited = new Set(visited).add(sid);
    const waiterEntries = waitersByBlocker.get(sid) ?? [];
    return {
      sid,
      username,
      sqlId,
      inThisSample,
      waiters: waiterEntries.map((w) =>
        buildNode(w.blocked_sid, w.blocked_username, w.blocked_sql_id, true, nextVisited),
      ),
    };
  }

  const rootBlockerSids = [...new Set(chains.map((c) => c.blocker_sid))].filter((sid) => !blockedSids.has(sid));
  return rootBlockerSids.map((sid) => {
    const entry = waitersByBlocker.get(sid)![0];
    return buildNode(sid, entry.blocker_username, entry.blocker_sql_id, entry.blocker_in_this_sample, new Set());
  });
}

function countWaiters(node: BlockingNode): number {
  return node.waiters.reduce((sum, w) => sum + 1 + countWaiters(w), 0);
}

export function BlockingLocksPage() {
  const databasesQuery = useDatabases();
  const [databaseId, setDatabaseId] = useState("");

  useEffect(() => {
    if (!databaseId && databasesQuery.data?.length) setDatabaseId(databasesQuery.data[0].id);
  }, [databaseId, databasesQuery.data]);

  const blockingQuery = useQuery({
    queryKey: ["blocking", databaseId],
    queryFn: () => apiFetch<BlockingSnapshot>(`/api/databases/${databaseId}/blocking`),
    enabled: Boolean(databaseId),
    refetchInterval: 15_000,
  });

  const tree = blockingQuery.data?.available ? buildBlockingTree(blockingQuery.data.blocking_chains) : [];

  return (
    <div className="flex flex-col gap-4">
      <DatabaseSelector databaseId={databaseId} onChange={setDatabaseId} databases={databasesQuery.data ?? []} />

      {!databaseId && <EmptyState title="Select a database to view blocking sessions" />}
      {databaseId && blockingQuery.isPending && <Loading label="Loading blocking chains…" />}
      {databaseId && blockingQuery.isError && (
        <ErrorState message={errorMessage(blockingQuery.error)} onRetry={() => blockingQuery.refetch()} />
      )}
      {databaseId && blockingQuery.data && !blockingQuery.data.available && (
        <EmptyState
          title="No session telemetry yet"
          description={blockingQuery.data.reason ?? "The collector hasn't sampled this database yet."}
        />
      )}
      {databaseId && blockingQuery.data?.available && tree.length === 0 && (
        <EmptyState title="No blocking sessions" description="Nothing is currently blocked in the latest sample." />
      )}
      {databaseId && blockingQuery.data?.available && tree.length > 0 && (
        <ul className="flex flex-col gap-3">
          {tree.map((node) => (
            <BlockingTreeNode key={node.sid} node={node} depth={0} />
          ))}
        </ul>
      )}
    </div>
  );
}

function BlockingTreeNode({ node, depth }: { node: BlockingNode; depth: number }) {
  const totalWaiters = countWaiters(node);
  return (
    <li>
      <div className="flex flex-wrap items-center gap-2 rounded-md border border-slate-200 bg-white px-3 py-2 text-sm dark:border-slate-800 dark:bg-slate-900">
        <Badge tone={depth === 0 ? "crit" : "warn"}>{depth === 0 ? "Blocker" : "Waiting"}</Badge>
        <span className="font-medium text-slate-800 dark:text-slate-200">SID {node.sid}</span>
        {node.username && <span className="text-slate-500 dark:text-slate-400">({node.username})</span>}
        {node.sqlId && <span className="font-mono text-xs text-slate-400 dark:text-slate-500">{node.sqlId}</span>}
        {!node.inThisSample && <Badge tone="warn">not in this sample</Badge>}
        {depth === 0 && totalWaiters > 0 && (
          <span className="ml-auto text-xs text-slate-400 dark:text-slate-500">
            {totalWaiters} session{totalWaiters === 1 ? "" : "s"} waiting
          </span>
        )}
      </div>
      {node.waiters.length > 0 && (
        <ul className="mt-2 flex flex-col gap-2 border-l-2 border-slate-200 pl-4 dark:border-slate-800">
          {node.waiters.map((w) => (
            <BlockingTreeNode key={w.sid} node={w} depth={depth + 1} />
          ))}
        </ul>
      )}
    </li>
  );
}
