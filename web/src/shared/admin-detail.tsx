import { Fragment, type ReactNode } from "react";
import { useTranslation } from "react-i18next";

import {
  Breadcrumb,
  BreadcrumbItem,
  BreadcrumbLink,
  BreadcrumbList,
  BreadcrumbPage,
  BreadcrumbSeparator,
} from "../components/ui/breadcrumb";
import { Button } from "../components/ui/button";
import {
  Empty,
  EmptyDescription,
  EmptyHeader,
  EmptyTitle,
} from "../components/ui/empty";
import { Tabs, TabsList, TabsTrigger } from "../components/ui/tabs";
import type { AppRoute } from "./routes";

export type AdminBreadcrumbItem = {
  label: string;
  route?: AppRoute;
};

export function AdminBreadcrumb({
  items,
  onNavigate,
}: {
  items: AdminBreadcrumbItem[];
  onNavigate: (route: AppRoute) => void;
}) {
  return (
    <Breadcrumb>
      <BreadcrumbList>
        {items.map((item, index) => {
          const current = index === items.length - 1;
          return (
            <Fragment key={`${item.label}:${index}`}>
              <BreadcrumbItem>
                {current || !item.route ? (
                  <BreadcrumbPage className="max-w-64 truncate">{item.label}</BreadcrumbPage>
                ) : (
                  <BreadcrumbLink
                    href={item.route}
                    className="max-w-64 truncate"
                    onClick={(event) => {
                      event.preventDefault();
                      onNavigate(item.route!);
                    }}
                  >
                    {item.label}
                  </BreadcrumbLink>
                )}
              </BreadcrumbItem>
              {!current && <BreadcrumbSeparator />}
            </Fragment>
          );
        })}
      </BreadcrumbList>
    </Breadcrumb>
  );
}

export function AdminSectionNav<T extends string>({
  value,
  items,
  onValueChange,
}: {
  value: T;
  items: Array<{ value: T; label: string; icon?: ReactNode }>;
  onValueChange: (value: T) => void;
}) {
  return (
    <Tabs value={value}>
      <TabsList variant="line">
        {items.map((item) => (
          <TabsTrigger
            key={item.value}
            value={item.value}
            onClick={() => onValueChange(item.value)}
          >
            {item.icon}
            {item.label}
          </TabsTrigger>
        ))}
      </TabsList>
    </Tabs>
  );
}

export function AdminResourceUnavailable({
  onBack,
}: {
  onBack: () => void;
}) {
  const { t } = useTranslation();
  return (
    <section className="flex flex-col gap-5">
      <Empty className="border">
        <EmptyHeader>
          <EmptyTitle>{t("admin.resourceUnavailableTitle")}</EmptyTitle>
          <EmptyDescription>{t("admin.resourceUnavailableDescription")}</EmptyDescription>
        </EmptyHeader>
        <Button variant="outline" onClick={onBack}>
          {t("admin.backToDirectory")}
        </Button>
      </Empty>
    </section>
  );
}
