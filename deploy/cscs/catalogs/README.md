# Collection catalogs

Groups the eoapi-dev STAC collections into a browsable tree, served by the
stac-fastapi-pgstac Catalogs extension (`ENABLE_CATALOGS_EXTENSION` in
`values-cscs-dev.yaml`). The landing page at `/` redirects to the root,
<https://prometheus-dev.c2sm-tds.c2sm.cscs.ch/browser/catalogs/prometheus>.
`/browser/` itself is unchanged and still lists every collection.

- `catalogs.yaml`: the tree: id, title, Markdown description and preview image
  per catalog.
- `load_catalogs.py`: the rules that put each collection into a leaf catalog,
  and the SQL generator.

The extension stores a catalog as a `type: Catalog` row in
`pgstac.collections`, and membership as a `parent_ids` list in each
collection's JSON. Transactions are off on this deployment, so the extension's
write endpoints do not exist; the catalogs are loaded with SQL instead.

## Load or update

Re-run after changing either file, and after importing collections: a
collection matching no rule fails the run, and a re-import that rewrites a
collection's JSON drops its `parent_ids`.

```bash
PRIMARY=$(kubectl -n eoapi-dev get pod -o name \
  -l postgres-operator.crunchydata.com/cluster=eoapi,postgres-operator.crunchydata.com/role=master)

kubectl -n eoapi-dev exec "$PRIMARY" -c database -- psql -d eoapi -At -c \
  "select json_agg(json_build_object('id', id, 'title', content->>'title'))
   from pgstac.collections where content->>'type' = 'Collection'" \
  | python3 deploy/cscs/catalogs/load_catalogs.py > /tmp/catalogs.sql

kubectl -n eoapi-dev exec -i "$PRIMARY" -c database -- \
  psql -d eoapi -v ON_ERROR_STOP=1 < /tmp/catalogs.sql
```

The script prints the tree with collection counts to stderr. The SQL is one
transaction: it upserts every catalog, deletes catalogs no longer in
`catalogs.yaml`, and rewrites `parent_ids` on every collection. To try it
without changing anything, replace the final `COMMIT;` with `ROLLBACK;`.

Catalog ids must not match a collection id: both live in the same table, so
the catalog would replace the collection. The script and the SQL both refuse.

## Preview images

Previews are NASA GIBS WMS `GetMap` URLs with a fixed date, so the browser
fetches them straight from GIBS. stac-browser only shows JPEG, PNG, WebP and
GIF previews, not SVG.
