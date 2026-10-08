export function BrandMark({ className = "" }: { className?: string }) {
  return (
    <svg className={`auth-brand-mark ${className}`} aria-hidden="true" focusable="false" viewBox="0 0 40 44" fill="none">
      <path fill="currentColor" d="M4 3h18c10 0 15 6 15 14s-5 14-15 14H13v10H4V3Zm9 9v10h9c4 0 6-2 6-5s-2-5-6-5h-9Z" />
      <path fill="#878b91" d="m4 3 9 9h9l-9-9H4Z" />
    </svg>
  );
}
