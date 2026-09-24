import { ChevronDown, type LucideProps } from "lucide-react";

import { cn } from "@/lib/utils";

export interface DisclosureChevronProps extends Omit<LucideProps, "aria-hidden"> {
  /**
   * 受控 disclosure は boolean、ネイティブ details は名前付き group を指定する。
   * 展開時は下向き、折りたたみ時は左向きで統一する。
   */
  expanded: boolean | "group";
}

export function DisclosureChevron({
  expanded,
  className,
  ...props
}: DisclosureChevronProps) {
  const stateClass =
    expanded === "group"
      ? "rotate-90 group-open/disclosure:rotate-0"
      : expanded
        ? "rotate-0"
        : "rotate-90";

  return (
    <ChevronDown
      {...props}
      className={cn(
        "shrink-0 transition-transform duration-200 motion-reduce:transition-none",
        stateClass,
        className
      )}
      data-state={
        expanded === "group" ? undefined : expanded ? "expanded" : "collapsed"
      }
      aria-hidden="true"
      focusable="false"
    />
  );
}
