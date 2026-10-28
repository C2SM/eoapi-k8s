// Replaces stac-browser's own relationTypes.config.js at build time - it is a
// source file read by the build, like widgets.config.js, not runtime config.
//
// Other relation types that point to STAC entities and should be shown in STAC
// Browser instead of opening them raw in a new tab.
export const stacNavigation = [];
// Relation types that should be hidden from the link list in STAC Browser.
// `catalogs` and `children` come from stac-fastapi's Catalogs extension and
// point at plain JSON lists stac-browser cannot render; the catalog's
// sub-catalogs and collections are already shown through `child` and `data`.
export const hidden = ['catalogs', 'children'];
