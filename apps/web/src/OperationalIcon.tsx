import type { SVGProps } from "react";

export type OperationalIconName =
  | "search"
  | "queue"
  | "document"
  | "health"
  | "settings"
  | "filter"
  | "file-pdf"
  | "file-docx"
  | "chevron"
  | "folder"
  | "database"
  | "table"
  | "globe"
  | "logout"
  | "check"
  | "check-circle"
  | "minus-circle"
  | "copy"
  | "external-link"
  | "info"
  | "download"
  | "trash";

interface OperationalIconProps extends Omit<SVGProps<SVGSVGElement>, "name"> {
  name: OperationalIconName;
  size?: number;
}

export function OperationalIcon({ name, size = 20, ...props }: OperationalIconProps) {
  const common = {
    fill: "none",
    stroke: "currentColor",
    strokeLinecap: "round" as const,
    strokeLinejoin: "round" as const,
    strokeWidth: 1.65,
  };

  return (
    <svg
      aria-hidden="true"
      focusable="false"
      viewBox="0 0 24 24"
      width={size}
      height={size}
      {...props}
    >
      {name === "search" ? (
        <g {...common}>
          <circle cx="10.5" cy="10.5" r="6.5" />
          <path d="m15.5 15.5 4.5 4.5" />
        </g>
      ) : null}
      {name === "queue" ? (
        <g {...common}>
          <path d="m3 7 9-4 9 4-9 4-9-4Z" />
          <path d="m3 12 9 4 9-4M3 17l9 4 9-4" />
        </g>
      ) : null}
      {name === "document" ? (
        <g {...common}>
          <path d="M6 2.5h8l4 4v15H6z" />
          <path d="M14 2.5v4h4M9 12h6M9 16h6" />
        </g>
      ) : null}
      {name === "health" ? (
        <path {...common} d="M2 13h4l2-7 4 14 3-10 2 3h5" />
      ) : null}
      {name === "settings" ? (
        <g {...common}>
          <circle cx="12" cy="12" r="3" />
          <path d="M19 12a7 7 0 0 0-.1-1l2-1.5-2-3.5-2.4 1A7 7 0 0 0 15 6l-.4-2.5h-4L10 6a7 7 0 0 0-1.5 1L6 6 4 9.5 6.1 11A7 7 0 0 0 6 12c0 .3 0 .7.1 1L4 14.5 6 18l2.5-1a7 7 0 0 0 1.5 1l.5 2.5h4L15 18a7 7 0 0 0 1.5-1l2.5 1 2-3.5-2.1-1.5c.1-.3.1-.7.1-1Z" />
        </g>
      ) : null}
      {name === "filter" ? (
        <g {...common}>
          <path d="M4 6h16M7 11h10M10 16h4" />
        </g>
      ) : null}
      {name === "file-pdf" || name === "file-docx" ? (
        <g {...common}>
          <path d="M6 2.5h8l4 4v15H6z" />
          <path d="M14 2.5v4h4" />
          <path d={name === "file-pdf" ? "M8.5 16.5h7M9 12.5h6" : "m8.5 12 1.5 5 2-4 2 4 1.5-5"} />
        </g>
      ) : null}
      {name === "chevron" ? <path {...common} d="m8 10 4 4 4-4" /> : null}
      {name === "folder" ? (
        <path {...common} d="M3 6.5h7l2 2h9v10H3z" />
      ) : null}
      {name === "database" ? (
        <g {...common}>
          <ellipse cx="12" cy="5" rx="8" ry="3" />
          <path d="M4 5v7c0 1.7 3.6 3 8 3s8-1.3 8-3V5M4 12v7c0 1.7 3.6 3 8 3s8-1.3 8-3v-7" />
        </g>
      ) : null}
      {name === "table" ? (
        <g {...common}>
          <rect x="3" y="4" width="18" height="16" rx="1" />
          <path d="M3 9h18M8 9v11M15 9v11M3 14h18" />
        </g>
      ) : null}
      {name === "globe" ? (
        <g {...common}>
          <circle cx="12" cy="12" r="9" />
          <path d="M3 12h18M12 3a15 15 0 0 1 0 18M12 3a15 15 0 0 0 0 18" />
        </g>
      ) : null}
      {name === "logout" ? (
        <g {...common}>
          <path d="M10 5H4v14h6M14 8l4 4-4 4M8 12h10" />
        </g>
      ) : null}
      {name === "check" ? <path {...common} d="m5 12 4 4L19 6" /> : null}
      {name === "check-circle" ? (
        <g {...common}>
          <circle cx="12" cy="12" r="8.5" />
          <path d="m8 12 2.5 2.5L16.5 8.5" />
        </g>
      ) : null}
      {name === "minus-circle" ? (
        <g {...common}>
          <circle cx="12" cy="12" r="8.5" />
          <path d="M8.5 12h7" />
        </g>
      ) : null}
      {name === "copy" ? (
        <g {...common}>
          <rect x="8" y="8" width="11" height="12" rx="1" />
          <path d="M16 8V4H5v12h3" />
        </g>
      ) : null}
      {name === "external-link" ? (
        <g {...common}>
          <path d="M14 4h6v6M20 4l-9 9" />
          <path d="M18 13v6H5V6h6" />
        </g>
      ) : null}
      {name === "info" ? (
        <g {...common}>
          <circle cx="12" cy="12" r="9" />
          <path d="M12 10.5v6M12 7.5h.01" />
        </g>
      ) : null}
      {name === "download" ? (
        <g {...common}>
          <path d="M12 3v12M7.5 10.5 12 15l4.5-4.5" />
          <path d="M4 17v3h16v-3" />
        </g>
      ) : null}
      {name === "trash" ? (
        <g {...common}>
          <path d="M4 7h16M9 7V4h6v3M7 7l1 14h8l1-14" />
          <path d="M10 11v6M14 11v6" />
        </g>
      ) : null}
    </svg>
  );
}
