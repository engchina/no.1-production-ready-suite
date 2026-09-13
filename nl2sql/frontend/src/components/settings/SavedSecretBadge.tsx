interface SavedSecretBadgeProps {
  label: string;
}

export function SavedSecretBadge({ label }: SavedSecretBadgeProps) {
  return (
    <span className="inline-flex items-center justify-center whitespace-nowrap rounded-full border border-success-border bg-success-subtle px-2 py-0.5 text-xs font-medium text-success-fg">
      {label}
    </span>
  );
}
