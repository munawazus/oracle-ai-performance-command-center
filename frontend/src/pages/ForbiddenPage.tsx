import { EmptyState } from "../components/ui/EmptyState";

export function ForbiddenPage() {
  return (
    <EmptyState
      title="You don't have access to this page"
      description="Your account role doesn't include the permission this page requires. Contact an administrator if you believe this is wrong."
    />
  );
}
