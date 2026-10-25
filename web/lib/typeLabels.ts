import { useT } from "@/lib/i18n";

/** Names of the types of apps and services, for "Sort into sub-folders by type". */
export function useTypeLabels(): Record<string, string> {
  const t = useT();
  return { nextjs: "Next.js", node: "Node.js", python: "Python", php: "PHP", static: t("Static sites"), stack: t("Ready-made services"), unknown: t("Other") };
}
