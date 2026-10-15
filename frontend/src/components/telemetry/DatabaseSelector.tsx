import { useQuery } from "@tanstack/react-query";
import { useEffect } from "react";

import { Select } from "../ui/Select";
import { apiFetch } from "../../lib/api";
import { useInvestigation } from "../../lib/investigation-context";
import type { MonitoredDatabase } from "../../types/api";

export function useDatabases() {
  return useQuery({
    queryKey: ["databases"],
    queryFn: () => apiFetch<MonitoredDatabase[]>("/api/databases"),
  });
}

export function DatabaseSelector({
  databaseId,
  onChange,
  databases,
}: {
  databaseId: string;
  onChange: (id: string) => void;
  databases: MonitoredDatabase[];
}) {
  const { setCurrentDatabase } = useInvestigation();

  useEffect(() => {
    if (!databaseId) return;
    const name = databases.find((d) => d.id === databaseId)?.name;
    if (name) setCurrentDatabase(databaseId, name);
  }, [databaseId, databases, setCurrentDatabase]);

  return (
    <div className="w-64">
      <Select
        label="Database"
        options={[
          { value: "", label: databases.length === 0 ? "No databases registered" : "Select a database" },
          ...databases.map((d) => ({ value: d.id, label: d.name })),
        ]}
        value={databaseId}
        onChange={(e) => onChange(e.target.value)}
      />
    </div>
  );
}
