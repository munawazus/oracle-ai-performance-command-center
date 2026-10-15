import { EmptyState } from "../components/ui/EmptyState";

export function PlaceholderPage({ title }: { title: string }) {
  return (
    <EmptyState
      title={`${title} is not available yet`}
      description="This capability is planned for a later implementation phase. It will appear here once it's built against real telemetry - nothing is shown in the meantime rather than placeholder or sample data."
    />
  );
}
