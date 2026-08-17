interface SavedSecretBadgeProps {
  label: string;
}

export function SavedSecretBadge({ label }: SavedSecretBadgeProps) {
  return (
    <span className="inline-flex items-center justify-center whitespace-nowrap rounded-full border border-success/30 bg-success-bg px-2 py-0.5 text-xs font-medium text-success">
      {label}
    </span>
  );
}
