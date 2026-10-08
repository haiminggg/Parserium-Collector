import { Badge } from "@mantine/core";
import type { ReactNode } from "react";
import { OperationalIcon as ExistingIcon, type OperationalIconName } from "../OperationalIcon";
import { activeJob, type DocumentRecord } from "./api";

export function CloudOperationalIcon({ name, size = 16 }: { name: OperationalIconName | "file" | "arrow-right"; size?: number }) {
  return <ExistingIcon name={name === "file" ? "document" : name === "arrow-right" ? "chevron" : name} width={size} height={size} style={name === "arrow-right" ? { transform: "rotate(-90deg)" } : undefined} />;
}

export const documentStatusText = (doc: DocumentRecord) => doc.job_status === "succeeded" ? "Ready" : doc.job_status === "running" ? "Parsing" : activeJob(doc) ? "Queued" : doc.job_status === "failed" || doc.validation_status === "invalid" ? "Needs attention" : "Not parsed";

export function DocumentStatus({ doc }: { doc: DocumentRecord }) {
  return <Badge variant="light" radius="xs" color={doc.job_status === "succeeded" ? "parseriumGreen" : doc.job_status === "failed" || doc.validation_status === "invalid" ? "parseriumRed" : activeJob(doc) ? "parseriumEmber" : "gray"}>{documentStatusText(doc)}</Badge>;
}

export function CloudEmpty({ title, children }: { title: string; children: ReactNode }) {
  return <div className="cloud-empty"><CloudOperationalIcon name="file" size={30} /><h3>{title}</h3><p>{children}</p></div>;
}
