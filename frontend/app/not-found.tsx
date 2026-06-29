import { EmptyState } from "@/components/EmptyState";

export default function NotFound(): JSX.Element {
  return (
    <EmptyState
      title="Page not found"
      body="That digest or story doesn't exist here."
      cta={{ href: "/", label: "Go to Today" }}
    />
  );
}
