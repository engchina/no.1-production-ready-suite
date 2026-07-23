import type {
  DbAdminObjectPage,
  SchemaCatalog,
  SchemaObjectPage,
} from "./types";

export function isUserVisibleObjectName(objectName: string): boolean {
  const normalized = objectName.trim();
  return Boolean(normalized) && !normalized.includes("$") && !normalized.includes("#");
}

export function filterUserVisibleCatalog(catalog: SchemaCatalog): SchemaCatalog {
  return {
    ...catalog,
    tables: catalog.tables.filter((table) => isUserVisibleObjectName(table.table_name)),
    view_dependencies: catalog.view_dependencies?.filter(
      (dependency) =>
        isUserVisibleObjectName(dependency.view_name) &&
        isUserVisibleObjectName(dependency.referenced_name)
    ),
  };
}

export function filterUserVisibleSchemaObjectPage(page: SchemaObjectPage): SchemaObjectPage {
  const hidden = page.items.filter((item) => !isUserVisibleObjectName(item.object_name));
  const hiddenTables = hidden.filter(
    (item) => !["VIEW", "MATERIALIZED VIEW"].includes(item.object_type.toUpperCase())
  ).length;
  const hiddenViews = hidden.length - hiddenTables;
  return {
    ...page,
    items: page.items.filter((item) => isUserVisibleObjectName(item.object_name)),
    total: page.total === null ? null : Math.max(0, page.total - hidden.length),
    table_count:
      page.table_count === undefined
        ? undefined
        : Math.max(0, page.table_count - hiddenTables),
    view_count:
      page.view_count === undefined
        ? undefined
        : Math.max(0, page.view_count - hiddenViews),
  };
}

export function filterUserVisibleDbAdminObjectPage(page: DbAdminObjectPage): DbAdminObjectPage {
  const hidden = page.items.filter((item) => !isUserVisibleObjectName(item.name));
  const hiddenTables = hidden.filter((item) => item.object_type !== "view").length;
  const hiddenViews = hidden.length - hiddenTables;
  return {
    ...page,
    items: page.items.filter((item) => isUserVisibleObjectName(item.name)),
    total: Math.max(0, page.total - hidden.length),
    table_count: Math.max(0, page.table_count - hiddenTables),
    view_count: Math.max(0, page.view_count - hiddenViews),
  };
}
