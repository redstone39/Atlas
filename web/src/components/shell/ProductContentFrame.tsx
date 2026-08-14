import type { ReactNode } from "react";

import { cn } from "../../lib/utils";

export function ProductContentFrame({
  children,
  mobileNavigationOffset = false,
  dataSlot,
  className,
}: {
  children: ReactNode;
  mobileNavigationOffset?: boolean;
  className?: string;
  dataSlot?: string;
}) {
  return (
    <div
      data-slot={dataSlot ?? "product-content-frame"}
      className={cn(
        "min-w-0 flex-1 overflow-y-auto px-3 pb-4 md:px-6 md:py-4",
        mobileNavigationOffset && "pt-16",
        className,
      )}
    >
      {children}
    </div>
  );
}
